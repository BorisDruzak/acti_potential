#!/usr/bin/python3 -I
"""Fixed, root-only, read-only ACTI observation; stdout is a closed boolean contract."""

import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

CONFIG = Path("/etc/acti-v3/observer.json")
STATE_ROOT = Path("/var/lib/acti-v3/external-backup")
SERVICES = ("frontend", "api", "postgres", "redis", "worker", "keycloak")
POSTGRES_ROOT = "/srv/acti/postgresql"
DOCUMENTS_ROOT = "/srv/acti/documents"
ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/root",
    "LC_ALL": "C.UTF-8",
}
DOCKER = ("/usr/bin/docker", "--host=unix:///var/run/docker.sock")
MAX_BYTES = 65536
MAX_STATE_ENTRIES = 2048
MAX_COMMAND_SECONDS = 5.0
TOTAL_SECONDS = 18.0
OUTBOX_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout = '2s';
SET LOCAL lock_timeout = '500ms';
SELECT
 (SELECT COUNT(*) FROM public.outbox_events WHERE state = 'failed'),
 (SELECT COUNT(*) FROM public.outbox_events WHERE state = 'pending'
  AND next_attempt_at < CURRENT_TIMESTAMP - INTERVAL '5 minutes'),
 (SELECT COUNT(*) FROM public.content_extractions WHERE state = 'running'
  AND (lease_until IS NULL OR lease_until < CURRENT_TIMESTAMP));
ROLLBACK;"""


class ObservationError(Exception):
    """Private failures never become operator-visible diagnostics."""


def unknown():
    return {
        "format": "acti-observer-v1",
        "services": dict.fromkeys(SERVICES, False),
        "worker_heartbeat": False,
        "mounts_verified": False,
        "storage_capacity": False,
        "outbox_healthy": False,
        "backup_fresh": False,
        "backup_check_fresh": False,
    }


def require(condition):
    if not condition:
        raise ObservationError()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def decode_json(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


def protected_info(path, *, directory=False):
    """Walk without following links; all traversed parents must be root-controlled."""
    path = Path(path)
    require(path.is_absolute())
    for parent in reversed(path.parents):
        info = os.lstat(parent)
        require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
        )
    info = os.lstat(path)
    require(info.st_uid == 0)
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    require(stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600))
    if not directory:
        require(info.st_nlink == 1)
    return info


def protected_json(path):
    before = protected_info(path)
    require(0 < before.st_size <= MAX_BYTES)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        require((info.st_dev, info.st_ino) == (before.st_dev, before.st_ino))
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0)
        require(stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
        require(0 < info.st_size <= MAX_BYTES)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES)
        value = decode_json(raw.decode("utf-8"))
        require(isinstance(value, dict))
        return value, info.st_mtime
    finally:
        os.close(descriptor)


def canonical_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return str(parsed) == value and parsed.int > 0


def read_config():
    value, _ = protected_json(CONFIG)
    require(
        set(value)
        == {
            "format_version",
            "expected_postgres_uuid",
            "expected_documents_uuid",
        }
    )
    require(type(value["format_version"]) is int and value["format_version"] == 1)
    for key in ("expected_postgres_uuid", "expected_documents_uuid"):
        require(canonical_uuid(value[key]))
    require(value["expected_postgres_uuid"] != value["expected_documents_uuid"])
    return value


class Budget:
    def __init__(self):
        self.deadline = time.monotonic() + TOTAL_SECONDS

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        require(seconds > 0)
        return seconds

    def command(self, argv):
        result = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
            env=ENVIRONMENT,
            timeout=min(MAX_COMMAND_SECONDS, self.remaining()),
        )
        self.remaining()
        require(result.returncode == 0 and len(result.stdout) <= MAX_BYTES)
        return result.stdout.decode("utf-8")


def verified_mounts(config, budget):
    for path, key in (
        (POSTGRES_ROOT, "expected_postgres_uuid"),
        (DOCUMENTS_ROOT, "expected_documents_uuid"),
    ):
        raw = budget.command(
            (
                "/usr/bin/findmnt",
                "--json",
                "--list",
                "--output",
                "TARGET,UUID",
                "--mountpoint",
                path,
            )
        )
        filesystems = decode_json(raw).get("filesystems")
        require(isinstance(filesystems, list) and len(filesystems) == 1)
        require(filesystems[0] == {"target": path, "uuid": config[key]})
    return True


def storage_capacity(budget):
    for path in ("/", POSTGRES_ROOT, DOCUMENTS_ROOT):
        budget.remaining()
        usage = shutil.disk_usage(path)
        require(usage.total > 0 and 0 <= usage.free <= usage.total)
        require(usage.free * 100 >= usage.total * 15)
    budget.remaining()
    return True


def containers(budget):
    health = dict.fromkeys(SERVICES, False)
    found = {name: [] for name in SERVICES}
    raw = budget.command(
        (
            *DOCKER,
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=acti-v3",
            "--format",
            '{{.ID}}\t{{.Label "com.docker.compose.service"}}',
        )
    )
    lines = raw.splitlines()
    require(len(lines) <= 128)
    seen = set()
    for line in lines:
        identifier, service = line.split("\t")
        require(re.fullmatch(r"[0-9a-f]{64}", identifier) and identifier not in seen)
        seen.add(identifier)
        if service in found:
            found[service].append(identifier)
    unique = {name: values[0] for name, values in found.items() if len(values) == 1}
    if not unique:
        return health, {}
    raw = budget.command(
        (
            *DOCKER,
            "inspect",
            "--type",
            "container",
            "--format",
            "{{json .State}}",
            *unique.values(),
        )
    )
    states = [decode_json(line) for line in raw.splitlines()]
    require(len(states) == len(unique))
    for (name, _), state in zip(unique.items(), states, strict=True):
        health[name] = (
            isinstance(state, dict)
            and state.get("Running") is True
            and isinstance(state.get("Health"), dict)
            and state["Health"].get("Status") == "healthy"
        )
    return health, unique


def worker_heartbeat(identifier, budget):
    raw = budget.command(
        (*DOCKER, "exec", identifier, "redis-cli", "--raw", "GET", "worker:heartbeat")
    )
    require(len(raw) <= 128)
    timestamp = float(raw.strip())
    now = time.time()
    require(
        math.isfinite(now) and math.isfinite(timestamp) and -5 <= now - timestamp <= 30
    )
    return True


def outbox_healthy(identifier, budget):
    raw = budget.command(
        (
            *DOCKER,
            "exec",
            "--user",
            "postgres",
            identifier,
            "psql",
            "-X",
            "-w",
            "-qAt",
            "--username=postgres",
            "--dbname=acti",
            "--host=/var/run/postgresql",
            "--port=5432",
            "--set=ON_ERROR_STOP=1",
            "--command",
            OUTBOX_SQL,
        )
    )
    require(re.fullmatch(r"0\|0\|0\s*", raw) is not None)
    return True


def backup_jobs(budget):
    names = ("acti-v3-backup", "acti-v3-backup-check")
    units = tuple(name + suffix for name in names for suffix in (".service", ".timer"))
    raw = budget.command(
        (
            "/usr/bin/systemctl",
            "--system",
            "show",
            "--no-pager",
            "--property=Id",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=Result",
            "--property=ExecMainStatus",
            "--",
            *units,
        )
    )
    observed = {}
    for block in re.split(r"\n\s*\n", raw.strip()):
        values = {}
        for line in block.splitlines():
            key, value = line.split("=", 1)
            require(
                key in {"Id", "LoadState", "ActiveState", "Result", "ExecMainStatus"}
            )
            require(key not in values)
            values[key] = value
        identity = values.get("Id")
        require(identity in units and identity not in observed)
        observed[identity] = values
    require(set(observed) == set(units))
    results = []
    for name in names:
        service, timer = observed[name + ".service"], observed[name + ".timer"]
        results.append(
            service.get("LoadState") == "loaded"
            and service.get("Result") == "success"
            and service.get("ExecMainStatus") == "0"
            and service.get("ActiveState") in {"inactive", "active"}
            and timer.get("LoadState") == "loaded"
            and timer.get("ActiveState") == "active"
        )
    return tuple(results)


def backup_receipts(budget, now):
    protected_info(STATE_ROOT, directory=True)
    backup, check = False, False
    with os.scandir(STATE_ROOT) as entries:
        for index, entry in enumerate(entries):
            require(index < MAX_STATE_ENTRIES)
            budget.remaining()
            if not canonical_uuid(entry.name):
                continue
            directory = STATE_ROOT / entry.name
            protected_info(directory, directory=True)
            try:
                value, modified = protected_json(directory / "result.json")
            except FileNotFoundError:
                continue
            require(math.isfinite(modified))
            age = now - modified
            if value.get("status") == "backed_up":
                valid = (
                    isinstance(value.get("snapshot_id"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", value["snapshot_id"]) is not None
                    and canonical_uuid(value.get("run_id"))
                    and isinstance(value.get("source_sha"), str)
                    and re.fullmatch(r"[0-9a-f]{40}", value["source_sha"]) is not None
                )
                backup |= valid and -300 <= age <= 30 * 3600
            elif value.get("status") == "checked":
                check |= value.get("read_data") is True and -300 <= age <= 8 * 86400
    budget.remaining()
    return backup, check


def attempt(function, *args, default=False):
    try:
        return function(*args)
    except Exception:
        return default


def observe():
    report = unknown()
    if getattr(os, "geteuid", lambda: -1)() != 0:
        return report
    budget = Budget()
    try:
        config = read_config()
        now = time.time()
        require(math.isfinite(now))
    except Exception:
        return report
    report["mounts_verified"] = attempt(verified_mounts, config, budget)
    if report["mounts_verified"]:
        report["storage_capacity"] = attempt(storage_capacity, budget)
    health, identifiers = attempt(
        containers, budget, default=(dict.fromkeys(SERVICES, False), {})
    )
    report["services"] = health
    if health["redis"]:
        report["worker_heartbeat"] = attempt(
            worker_heartbeat, identifiers["redis"], budget
        )
    if health["postgres"]:
        report["outbox_healthy"] = attempt(
            outbox_healthy, identifiers["postgres"], budget
        )
    receipts = attempt(backup_receipts, budget, now, default=(False, False))
    jobs = attempt(backup_jobs, budget, default=(False, False))
    report["backup_fresh"], report["backup_check_fresh"] = (
        bool(receipt and job) for receipt, job in zip(receipts, jobs, strict=True)
    )
    return report


def main():
    result, exit_code = unknown(), 0
    if len(sys.argv) != 1:
        exit_code = 2
    elif getattr(os, "geteuid", lambda: -1)() != 0:
        exit_code = 1
    else:
        result = attempt(observe, default=result)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
