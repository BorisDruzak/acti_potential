#!/usr/bin/env python3
"""Root-only encrypted external backup. Never initializes repositories or restores ACTI.

Provision /etc/acti-v3/backup/config.json and the existing restic repository separately.
Only the read-only exporter supplies application data. A fetched bundle must subsequently
pass restore-verify.py prepare --bundle-source; fetch never starts application containers.
"""

import argparse
import configparser
import contextlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

DOCUMENTS_ROOT = Path("/srv/acti/documents")
POSTGRES_ROOT = Path("/srv/acti/postgresql")
CONFIG_ROOT = Path("/etc/acti-v3")
STATE_ROOT = Path("/var/lib/acti-v3/external-backup")
CACHE_ROOT = DOCUMENTS_ROOT / "external-backup/cache"
FETCH_ROOT = DOCUMENTS_ROOT / "external-backup/fetched"
LOCK_PATH = Path("/run/lock/acti-v3-external-backup.lock")
HOST = "acti-v3"
TAG = "acti-v3-external"
HEX64 = re.compile(r"[0-9a-f]{64}")
MARKER = ".acti-backup-owner"
DIAGNOSTIC_LIMIT = 1024 * 1024


class BackupSafetyError(RuntimeError):
    """Only fixed, operator-safe error codes may leave this module."""


def require(condition, code):
    if not condition:
        raise BackupSafetyError(code)


def protected(path, *, directory=False):
    path = Path(path).absolute()
    require(path == path.resolve(strict=True), "symlink_path_forbidden")
    info = path.stat()
    require(info.st_uid == 0 and not info.st_mode & 0o077, "private_path_permissions")
    require(
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
        "unexpected_private_path_type",
    )
    for parent in path.parents:
        info = parent.stat()
        require(
            info.st_uid == 0 and not info.st_mode & 0o022, "unsafe_parent_permissions"
        )
    return path


def private_directory(path):
    path = Path(path)
    if not path.exists():
        require(
            path.parent == path.parent.resolve(strict=True), "symlink_parent_forbidden"
        )
        path.mkdir(mode=0o700)
    return protected(path, directory=True)


def write_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_config(path):
    require(
        Path(path).absolute() == CONFIG_ROOT / "backup/config.json",
        "unexpected_config_path",
    )
    config = json.loads(protected(path).read_text(encoding="utf-8"))
    require(isinstance(config, dict), "invalid_config")
    require(
        set(config)
        == {
            "format_version",
            "expected_repository_id",
            "restic_repository",
            "ssh_config",
            "password_file",
            "cache_dir",
            "state_dir",
        },
        "invalid_config_fields",
    )
    require(config["format_version"] == 1, "unsupported_config_version")
    require(
        isinstance(config["expected_repository_id"], str)
        and HEX64.fullmatch(config["expected_repository_id"]),
        "invalid_repository_id",
    )
    require(
        config["restic_repository"] == "rclone:acti-backup:/repository",
        "unexpected_repository",
    )
    require(
        config["ssh_config"] == str(CONFIG_ROOT / "backup/ssh_config"),
        "unexpected_ssh_config",
    )
    require(
        Path(config["password_file"]).parent == CONFIG_ROOT / "backup",
        "unsafe_password_path",
    )
    require(config["cache_dir"] == str(CACHE_ROOT), "unsafe_cache_path")
    require(config["state_dir"] == str(STATE_ROOT), "unsafe_state_path")
    protected(config["password_file"])
    ssh_config = protected(config["ssh_config"])
    # ssh(1) evaluates this file, so constrain it to a single static SFTP identity.
    directives = {}
    allowed = {
        "host",
        "hostname",
        "user",
        "port",
        "identityfile",
        "userknownhostsfile",
        "stricthostkeychecking",
        "identitiesonly",
        "batchmode",
        "passwordauthentication",
        "kbdinteractiveauthentication",
        "pubkeyauthentication",
        "connecttimeout",
        "serveraliveinterval",
        "serveralivecountmax",
        "loglevel",
    }
    for line in ssh_config.read_text(encoding="utf-8").splitlines():
        parts = shlex.split(line, comments=True)
        if not parts:
            continue
        require(len(parts) == 2 and parts[0].lower() in allowed, "unsafe_ssh_directive")
        key, value = parts[0].lower(), parts[1]
        require(key not in directives, "duplicate_ssh_directive")
        directives[key] = value
    require(directives.get("host") == "acti-backup", "unexpected_ssh_host")
    for key in ("stricthostkeychecking", "identitiesonly", "batchmode"):
        require(directives.get(key) == "yes", "unsafe_ssh_authentication")
    require(
        directives.get("passwordauthentication") == "no"
        and directives.get("kbdinteractiveauthentication") == "no",
        "unsafe_ssh_authentication",
    )
    for key in ("identityfile", "userknownhostsfile"):
        require(
            key in directives
            and Path(directives[key]).parent == CONFIG_ROOT / "backup",
            "unsafe_ssh_file_path",
        )
        protected(directives[key])
    validate_rclone_config(CONFIG_ROOT / "backup/rclone.conf", directives)
    return config


def validate_rclone_config(path, ssh):
    """The Go SFTP client preserves the Windows chroot without SSH exec requests."""
    parsed = configparser.ConfigParser(interpolation=None, strict=True)
    parsed.read_string(protected(path).read_text(encoding="utf-8"))
    require(
        not parsed.defaults() and parsed.sections() == ["acti-backup"],
        "unsafe_rclone_sections",
    )
    values = dict(parsed["acti-backup"])
    require(
        set(values)
        == {
            "type",
            "host",
            "user",
            "port",
            "key_file",
            "known_hosts_file",
            "shell_type",
            "disable_hashcheck",
        },
        "unsafe_rclone_options",
    )
    require(
        values["type"] == "sftp"
        and values["shell_type"] == "none"
        and values["disable_hashcheck"] == "true",
        "unsafe_rclone_execution",
    )
    require(
        re.fullmatch(r"[A-Za-z0-9_.:-]+", values["host"])
        and re.fullmatch(r"[A-Za-z0-9_.-]+", values["user"]),
        "unsafe_rclone_identity",
    )
    require(
        values["port"].isdigit() and 1 <= int(values["port"]) <= 65535,
        "unsafe_rclone_port",
    )
    expected = {
        "host": ssh.get("hostname"),
        "user": ssh.get("user"),
        "port": ssh.get("port", "22"),
        "key_file": ssh.get("identityfile"),
        "known_hosts_file": ssh.get("userknownhostsfile"),
    }
    require(
        all(values[key] == value for key, value in expected.items()),
        "rclone_ssh_identity_mismatch",
    )
    for key in ("key_file", "known_hosts_file"):
        require(
            Path(values[key]).parent == CONFIG_ROOT / "backup",
            "unsafe_rclone_file_path",
        )
        protected(values[key])


def verify_expected_mounts(expected):
    require(
        set(expected) == {POSTGRES_ROOT, DOCUMENTS_ROOT}, "incomplete_mount_contract"
    )
    for path, uuid in expected.items():
        require(bool(uuid), "missing_mount_uuid")
        result = subprocess.run(
            ["findmnt", "-rn", "-o", "UUID", "--mountpoint", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        require(
            result.returncode == 0 and result.stdout.decode().strip() == uuid,
            "mount_uuid_mismatch",
        )


def runtime_preflight(runtime_env, expected_sha=None):
    require(Path(runtime_env) == CONFIG_ROOT / "runtime.env", "unexpected_runtime_path")
    env = {}
    for line in protected(runtime_env).read_text(encoding="utf-8").splitlines():
        parts = shlex.split(line, comments=True, posix=True)
        if not parts:
            continue
        if parts[0] == "export":
            parts = parts[1:]
        require(len(parts) == 1 and "=" in parts[0], "nonliteral_runtime_env")
        key, value = parts[0].split("=", 1)
        require(
            re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            and key not in env
            and not any(c in value for c in "\r\n\x00`$"),
            "unsafe_runtime_assignment",
        )
        env[key] = value
    sha = env.get("ACTI_SOURCE_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "invalid_runtime_sha")
    require(expected_sha is None or sha == expected_sha, "runtime_sha_mismatch")
    release = Path(env["ACTI_RELEASE_DIR"])
    require(release == release.resolve(strict=True), "symlink_release_forbidden")
    info = release.stat()
    require(info.st_uid == 0 and not info.st_mode & 0o022, "unsafe_release_permissions")
    for parent in release.parents:
        info = parent.stat()
        require(info.st_uid == 0 and not info.st_mode & 0o022, "unsafe_release_parent")
    script = release / "deploy/restore-verify.py"
    require(script == script.resolve(strict=True), "symlink_exporter_forbidden")
    info = script.stat()
    require(
        stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
        "unsafe_exporter_permissions",
    )
    verify_expected_mounts(
        {
            POSTGRES_ROOT: env.get("ACTI_POSTGRES_UUID", ""),
            DOCUMENTS_ROOT: env.get("ACTI_DOCUMENTS_UUID", ""),
        }
    )
    return sha, release


@contextlib.contextmanager
def exclusive_lock():
    import fcntl

    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and not info.st_mode & 0o077,
            "unsafe_lock_file",
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupSafetyError("backup_operation_already_running") from None
        yield
    finally:
        os.close(fd)


class Runner:
    def __init__(self, config, sha, release, runtime_env):
        self.config, self.sha, self.release = config, sha, Path(release)
        self.runtime_env = runtime_env
        self.diagnostics = None

    def command(self, argv, *, env=None, timeout=6900):
        try:
            result = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise BackupSafetyError("bounded_command_unavailable_or_timeout") from None
        if result.returncode:
            if self.diagnostics is not None:
                fd = os.open(
                    self.diagnostics,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "ab") as stream:
                    info = os.fstat(stream.fileno())
                    require(
                        info.st_uid == 0 and not info.st_mode & 0o077,
                        "unsafe_diagnostic_file",
                    )
                    stream.write(
                        result.stderr[: max(0, DIAGNOSTIC_LIMIT - info.st_size)]
                    )
            raise BackupSafetyError("command_failed")
        return result.stdout

    def restic(self, *args, cache=False):
        # Explicit env prevents ambient RESTIC_* settings from redirecting repositories.
        env = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "RESTIC_PASSWORD_FILE": self.config["password_file"],
        }
        command = [
            "restic",
            "--repo",
            self.config["restic_repository"],
            "--json",
            "-o",
            "rclone.program=/usr/bin/rclone",
            "-o",
            "rclone.args=--config "
            + str(CONFIG_ROOT / "backup/rclone.conf")
            + " serve restic --stdio",
        ]
        command += (
            ["--cache-dir", self.config["cache_dir"]] if cache else ["--no-cache"]
        )
        return self.command([*command, *args], env=env)

    def verify_repository(self):
        identity = json.loads(self.restic("cat", "config"))
        require(
            isinstance(identity, dict)
            and identity.get("id") == self.config["expected_repository_id"],
            "repository_id_mismatch",
        )

    def start_state(self):
        private_directory(STATE_ROOT)
        external = private_directory(DOCUMENTS_ROOT / "external-backup")
        private_directory(external / "cache")
        operation = STATE_ROOT / str(uuid4())
        operation.mkdir(mode=0o700)
        self.diagnostics = operation / "command-stderr.log"
        return operation

    def snapshots(self, snapshot=None):
        args = ["snapshots"] + ([snapshot] if snapshot else [])
        result = json.loads(self.restic(*args))
        require(isinstance(result, list), "invalid_snapshot_metadata")
        return result

    @staticmethod
    def verify_owned_snapshot(snapshot):
        require(
            isinstance(snapshot, dict) and HEX64.fullmatch(snapshot.get("id", "")),
            "invalid_snapshot_id",
        )
        require(
            snapshot.get("hostname") == HOST and snapshot.get("tags") == [TAG],
            "foreign_snapshot_forbidden",
        )

    def exporter(self, *args):
        options = (
            [] if args[0] == "cleanup" else ["--runtime-env", str(self.runtime_env)]
        )
        result = json.loads(
            self.command(
                [
                    "python3",
                    str(self.release / "deploy/restore-verify.py"),
                    *args,
                    *options,
                ]
            )
        )
        require(isinstance(result, dict), "invalid_exporter_response")
        return result

    def verify_export(self, exported):
        require(exported.get("status") == "exported", "bundle_export_failed")
        run_id = exported.get("run_id", "")
        require(str(UUID(run_id)) == run_id, "invalid_export_run_id")
        bundle = Path(exported["bundle_source"])
        require(
            bundle == DOCUMENTS_ROOT / "restore-verification" / run_id / "bundle",
            "unexpected_bundle_path",
        )
        protected(bundle, directory=True)
        require(
            protected(bundle / MARKER).read_text(encoding="utf-8").strip() == run_id,
            "bundle_owner_mismatch",
        )
        require(exported.get("source_sha") == self.sha, "bundle_source_sha_mismatch")
        return run_id, bundle

    def recovery(self, bundle):
        destination = bundle / "recovery"
        destination.mkdir(mode=0o700)
        # Config/TLS/SSH secrets remain root-only and are encrypted before crossing SFTP.
        self.copy_private_tree(CONFIG_ROOT, destination / "etc-acti-v3")
        self.copy_private_tree(
            self.release, destination / "release", private_source=False
        )

    @staticmethod
    def copy_private_tree(source, destination, *, private_source=True):
        require(source == source.resolve(strict=True), "recovery_symlink_forbidden")
        destination.mkdir(mode=0o700)
        for item in sorted(source.iterdir()):
            require(not item.is_symlink(), "recovery_symlink_forbidden")
            info = item.stat()
            require(
                info.st_uid == 0 and not info.st_mode & 0o022, "unsafe_recovery_source"
            )
            if item.is_dir():
                if item.name in {
                    ".git",
                    "__pycache__",
                    ".pytest_cache",
                    "node_modules",
                    ".venv",
                }:
                    continue
                Runner.copy_private_tree(
                    item, destination / item.name, private_source=private_source
                )
            else:
                require(stat.S_ISREG(info.st_mode), "recovery_special_file_forbidden")
                fd = os.open(
                    destination / item.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                with item.open("rb") as incoming, os.fdopen(fd, "wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)

    def backup(self):
        self.verify_repository()
        operation = self.start_state()
        exported = self.exporter("export", "--expected-sha", self.sha)
        run_id, bundle = self.verify_export(exported)
        snapshot_id = None
        try:
            self.recovery(bundle)
            output = self.restic(
                "backup", "--host", HOST, "--tag", TAG, str(bundle), cache=True
            )
            summaries = [
                json.loads(line) for line in output.splitlines() if line.strip()
            ]
            require(
                all(isinstance(row, dict) for row in summaries),
                "invalid_backup_response",
            )
            ids = [
                row.get("snapshot_id")
                for row in summaries
                if row.get("message_type") == "summary"
            ]
            require(
                len(ids) == 1 and isinstance(ids[0], str) and HEX64.fullmatch(ids[0]),
                "missing_full_snapshot_id",
            )
            snapshot_id = ids[0]
            metadata = self.snapshots(snapshot_id)
            require(
                len(metadata) == 1 and metadata[0].get("id") == snapshot_id,
                "snapshot_confirmation_failed",
            )
            self.verify_owned_snapshot(metadata[0])
            require(
                metadata[0].get("paths") == [str(bundle)],
                "snapshot_bundle_path_mismatch",
            )
            # Verify every snapshot before retention: this dedicated repository must have no foreign owner.
            all_snapshots = self.snapshots()
            for row in all_snapshots:
                self.verify_owned_snapshot(row)
            require(
                any(row.get("id") == snapshot_id for row in all_snapshots),
                "snapshot_not_in_repository",
            )
            self.verify_repository()
            self.restic(
                "forget",
                "--host",
                HOST,
                "--tag",
                TAG,
                "--group-by",
                "host,tags",
                "--keep-last",
                "3",
                "--keep-daily",
                "14",
                "--keep-weekly",
                "8",
                "--keep-monthly",
                "12",
                "--prune",
                cache=True,
            )
            result = {
                "status": "backed_up",
                "snapshot_id": snapshot_id,
                "source_sha": self.sha,
                "run_id": run_id,
                "counts": exported.get(
                    "counts",
                    {
                        key: exported.get(key, 0)
                        for key in ("tables", "sequences", "files", "bytes")
                    },
                ),
            }
            write_json(operation / "result.json", result)
            return result
        finally:
            self.exporter(
                "cleanup",
                "--receipt",
                str(
                    Path("/var/lib/acti-v3/restore-verification")
                    / run_id
                    / "receipt.json"
                ),
                "--run-id",
                run_id,
            )

    def check(self):
        self.verify_repository()
        operation = self.start_state()
        self.restic("check", "--read-data", cache=True)
        result = {"status": "checked", "read_data": True}
        write_json(operation / "result.json", result)
        return result

    def fetch(self, snapshot_id):
        require(HEX64.fullmatch(snapshot_id), "full_snapshot_id_required")
        self.verify_repository()
        metadata = self.snapshots(snapshot_id)
        require(
            len(metadata) == 1 and metadata[0].get("id") == snapshot_id,
            "snapshot_not_unique",
        )
        self.verify_owned_snapshot(metadata[0])
        paths = metadata[0].get("paths", [])
        require(len(paths) == 1, "unexpected_snapshot_paths")
        original = Path(paths[0])
        require(
            original.name == "bundle"
            and original.parent.parent == DOCUMENTS_ROOT / "restore-verification",
            "unexpected_bundle_path",
        )
        source_run = original.parent.name
        require(str(UUID(source_run)) == source_run, "invalid_bundle_run_id")
        operation = self.start_state()
        private_directory(FETCH_ROOT)
        target = FETCH_ROOT / str(uuid4())
        target.mkdir(mode=0o700)
        # A fresh owned target is preserved on failure for investigation; no recursive arbitrary cleanup.
        write_json(
            target / ".acti-fetch-owner.json",
            {"snapshot_id": snapshot_id, "run_id": target.name},
        )
        self.restic(
            "restore", snapshot_id, "--target", str(target), "--verify", cache=True
        )
        bundle = target / original.relative_to("/")
        protected(bundle, directory=True)
        require(
            protected(bundle / MARKER).read_text(encoding="utf-8").strip()
            == source_run,
            "fetched_bundle_owner_mismatch",
        )
        metadata = json.loads(
            protected(bundle / "bundle.json").read_text(encoding="utf-8")
        )
        require(
            metadata.get("run_id") == source_run
            and metadata.get("format") == "acti-backup-v1",
            "invalid_fetched_bundle_metadata",
        )
        result = {
            "status": "fetched",
            "snapshot_id": snapshot_id,
            "bundle_source": str(bundle),
            "target": str(target),
            "run_id": target.name,
            "source_sha": metadata.get("source_sha"),
        }
        write_json(operation / "result.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(CONFIG_ROOT / "backup/config.json"))
    parser.add_argument("--runtime-env", default=str(CONFIG_ROOT / "runtime.env"))
    sub = parser.add_subparsers(dest="action", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--expected-sha")
    sub.add_parser("check")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--snapshot", required=True)
    args = parser.parse_args()
    try:
        require(os.name == "posix" and os.geteuid() == 0, "root_linux_required")
        config = read_config(args.config)
        sha, release = runtime_preflight(
            Path(args.runtime_env), getattr(args, "expected_sha", None)
        )
        with exclusive_lock():
            runner = Runner(config, sha, release, Path(args.runtime_env))
            result = (
                runner.fetch(args.snapshot)
                if args.action == "fetch"
                else getattr(runner, args.action)()
            )
        print(json.dumps(result, sort_keys=True))
    except (
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        OSError,
        subprocess.TimeoutExpired,
        configparser.Error,
    ):
        # No raw exception strings, paths, SSH output, credentials, SQL or subprocess argv.
        print(
            json.dumps({"status": "failed", "error": "external_backup_failed_closed"})
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
