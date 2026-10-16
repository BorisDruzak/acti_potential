"""Read-only observer contract against bounded OS/Docker boundary fixtures."""

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "deploy/observe.py"
SERVICES = ("frontend", "api", "postgres", "redis", "worker", "keycloak")
NOW = 1_800_000_000.0
UUID_PG = "11111111-1111-4111-8111-111111111111"
UUID_DOCS = "22222222-2222-4222-8222-222222222222"
RUN_BACKUP = "33333333-3333-4333-8333-333333333333"
RUN_CHECK = "44444444-4444-4444-8444-444444444444"
EXPORT_RUN = "66666666-6666-4666-8666-666666666666"


def test_nonroot_cli_emits_closed_unknown_without_inspecting_the_host(monkeypatch, capsys):
    assert SOURCE.is_file(), "observer helper is missing"
    spec = importlib.util.spec_from_file_location("acti_observer_nonroot", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(module.sys, "argv", [str(SOURCE)])
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: pytest.fail("nonroot command"))
    assert module.main() == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert_closed_contract(report)
    assert not any(report["services"].values())
    assert not any(value for key, value in report.items() if key not in {"format", "services"})
    assert captured.err == ""


@pytest.fixture
def observer():
    assert SOURCE.is_file(), "observer helper is missing"
    spec = importlib.util.spec_from_file_location("acti_observer_unit", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def host(observer, monkeypatch, tmp_path):
    """Real file reads; POSIX metadata is an OS boundary on Windows hosts."""
    config = tmp_path / "etc/acti-v3/observer.json"
    state = tmp_path / "var/lib/acti-v3/external-backup"
    config.parent.mkdir(parents=True)
    state.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "format_version": 1,
                "expected_postgres_uuid": UUID_PG,
                "expected_documents_uuid": UUID_DOCS,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(observer, "CONFIG", config)
    monkeypatch.setattr(observer, "STATE_ROOT", state)
    monkeypatch.setattr(observer.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(observer.time, "time", lambda: NOW)
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(observer.time, "monotonic", lambda: clock.value)

    real_lstat = observer.os.lstat
    real_fstat = observer.os.fstat
    metadata_overrides = {}

    def metadata(path):
        info = real_lstat(path)
        leaf = Path(path)
        mode = stat.S_IFDIR | 0o700 if stat.S_ISDIR(info.st_mode) else stat.S_IFREG | 0o600
        if stat.S_ISLNK(info.st_mode):
            mode = info.st_mode
        values = {
            name: getattr(info, name)
            for name in ("st_dev", "st_ino", "st_size", "st_mtime", "st_nlink")
        }
        values.update(st_uid=0, st_mode=mode)
        values.update(metadata_overrides.get(leaf, {}))
        return SimpleNamespace(**values)

    def file_metadata(fd):
        info = real_fstat(fd)
        return SimpleNamespace(
            st_dev=info.st_dev,
            st_ino=info.st_ino,
            st_size=info.st_size,
            st_mtime=info.st_mtime,
            st_nlink=info.st_nlink,
            st_uid=0,
            st_mode=stat.S_IFREG | 0o600,
        )

    monkeypatch.setattr(observer.os, "lstat", metadata)
    monkeypatch.setattr(observer.os, "fstat", file_metadata)

    def receipt(run_id, value, age=60):
        directory = state / run_id
        directory.mkdir(exist_ok=True)
        path = directory / "result.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        os.utime(path, (NOW - age, NOW - age))
        return path

    backup = receipt(
        RUN_BACKUP,
        {
            "status": "backed_up",
            "snapshot_id": "a" * 64,
            "run_id": EXPORT_RUN,
            "source_sha": "b" * 40,
            "counts": {"files": 111},
        },
    )
    check = receipt(RUN_CHECK, {"status": "checked", "read_data": True})
    ids = {name: str(index + 1) * 64 for index, name in enumerate(SERVICES)}
    behavior = SimpleNamespace(
        docker_rows=[(ids[name], name) for name in SERVICES],
        states={name: {"Running": True, "Health": {"Status": "healthy"}} for name in SERVICES},
        heartbeat=str(NOW - 3),
        outbox="0|0|0\n",
        mounts={},
        timeout_on=None,
        error_on=None,
        oversize_on=None,
        advance=0.0,
        failed_backup=False,
        failed_check=False,
        inactive_timer=False,
    )
    commands = []

    def command(argv, **kwargs):
        commands.append((list(argv), kwargs))
        assert kwargs["timeout"] <= 5
        assert kwargs["shell"] is False
        assert kwargs["env"] == {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/root",
            "LC_ALL": "C.UTF-8",
        }
        assert kwargs["stdin"] == subprocess.DEVNULL
        clock.value += behavior.advance
        kind = Path(argv[0]).name
        if kind == behavior.timeout_on:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], stderr=b"private-error")
        if kind == behavior.error_on:
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"private-secret")
        if kind == behavior.oversize_on:
            return SimpleNamespace(returncode=0, stdout=b"x" * 65537, stderr=b"")
        if kind == "findmnt":
            target = argv[-1]
            value = UUID_PG if target.endswith("postgresql") else UUID_DOCS
            output = json.dumps(
                {"filesystems": [{"target": target, "uuid": behavior.mounts.get(target, value)}]}
            )
        elif kind == "docker" and "ps" in argv:
            output = "\n".join(cid + "\t" + service for cid, service in behavior.docker_rows)
        elif kind == "docker" and "inspect" in argv:
            selected = [item for item in argv if item in ids.values()]
            output = "\n".join(
                json.dumps(
                    behavior.states[next(name for name, cid in ids.items() if cid == identifier)]
                )
                for identifier in selected
            )
        elif kind == "docker" and "redis-cli" in argv:
            output = behavior.heartbeat + "\n"
        elif kind == "docker" and "psql" in argv:
            assert argv[argv.index("exec") + 1 : argv.index("exec") + 3] == [
                "--user",
                "postgres",
            ]
            sql = argv[-1]
            assert "BEGIN READ ONLY" in sql
            assert "statement_timeout = '2s'" in sql
            assert "lock_timeout = '500ms'" in sql
            assert "public.outbox_events" in sql and "public.content_extractions" in sql
            assert "ROLLBACK" in sql
            assert not any(
                word in sql.upper() for word in ("INSERT", "UPDATE", "DELETE", "NEXTVAL")
            )
            assert "--host=/var/run/postgresql" in argv
            output = behavior.outbox
        elif kind == "systemctl":
            units = []
            for name in ("acti-v3-backup", "acti-v3-backup-check"):
                failed = (
                    behavior.failed_backup if name == "acti-v3-backup" else behavior.failed_check
                )
                units.append(
                    "\n".join(
                        (
                            f"Id={name}.service",
                            "LoadState=loaded",
                            "ActiveState=inactive",
                            "Result=exit-code" if failed else "Result=success",
                            "ExecMainStatus=1" if failed else "ExecMainStatus=0",
                        )
                    )
                )
                units.append(
                    "\n".join(
                        (
                            f"Id={name}.timer",
                            "LoadState=loaded",
                            "ActiveState=inactive"
                            if behavior.inactive_timer
                            else "ActiveState=active",
                        )
                    )
                )
            output = "\n\n".join(units)
        else:
            pytest.fail("Unexpected observer subprocess")
        return SimpleNamespace(returncode=0, stdout=output.encode(), stderr=b"")

    monkeypatch.setattr(observer.subprocess, "run", command)
    capacities = {}
    disk_calls = []

    def disk_usage(path):
        disk_calls.append(str(path))
        return SimpleNamespace(total=1000, used=700, free=capacities.get(str(path), 300))

    monkeypatch.setattr(observer.shutil, "disk_usage", disk_usage)
    return SimpleNamespace(
        config=config,
        state=state,
        backup=backup,
        check=check,
        receipt=receipt,
        behavior=behavior,
        ids=ids,
        commands=commands,
        clock=clock,
        metadata=metadata_overrides,
        capacities=capacities,
        disk_calls=disk_calls,
    )


def assert_closed_contract(report):
    assert set(report) == {
        "format",
        "services",
        "worker_heartbeat",
        "mounts_verified",
        "storage_capacity",
        "outbox_healthy",
        "backup_fresh",
        "backup_check_fresh",
    }
    assert report["format"] == "acti-observer-v1"
    assert set(report["services"]) == set(SERVICES)
    assert all(type(value) is bool for value in report["services"].values())
    assert all(
        type(value) is bool for key, value in report.items() if key not in {"format", "services"}
    )


def test_healthy_host_returns_only_the_complete_boolean_contract(observer, host):
    result = observer.observe()
    assert_closed_contract(result)
    assert all(result["services"].values())
    assert all(value for key, value in result.items() if key not in {"format", "services"})


def test_backup_operation_uuid_can_differ_from_the_export_bundle_uuid(observer, host):
    value = json.loads(host.backup.read_text())
    assert value["run_id"] == EXPORT_RUN and host.backup.parent.name == RUN_BACKUP
    assert observer.observe()["backup_fresh"] is True


def test_postgres_probe_uses_the_existing_postgres_os_identity(observer, host):
    assert observer.observe()["outbox_healthy"] is True
    command = next(argv for argv, _ in host.commands if "psql" in argv)
    assert command[command.index("exec") + 1 : command.index("psql")] == [
        "--user",
        "postgres",
        host.ids["postgres"],
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "symlink",
        "owner",
        "writable_parent",
        "public_config",
        "hardlink",
        "unknown_key",
        "bool_version",
        "bad_uuid",
        "same_uuid",
        "duplicate_key",
        "oversized",
    ],
)
def test_unsafe_config_produces_unknown_without_running_commands(observer, host, mutation):
    if mutation == "missing":
        host.config.unlink()
    elif mutation == "symlink":
        host.metadata[host.config] = {"st_mode": stat.S_IFLNK | 0o777}
    elif mutation == "owner":
        host.metadata[host.config] = {"st_uid": 1000}
    elif mutation == "writable_parent":
        host.metadata[host.config.parent] = {"st_mode": stat.S_IFDIR | 0o770}
    elif mutation == "public_config":
        host.metadata[host.config] = {"st_mode": stat.S_IFREG | 0o644}
    elif mutation == "hardlink":
        host.metadata[host.config] = {"st_nlink": 2}
    elif mutation == "duplicate_key":
        host.config.write_text('{"format_version":1,"format_version":1}', encoding="utf-8")
    elif mutation == "oversized":
        host.config.write_text("x" * 65537, encoding="utf-8")
    else:
        value = json.loads(host.config.read_text())
        if mutation == "unknown_key":
            value["command"] = "private-secret"
        elif mutation == "bool_version":
            value["format_version"] = True
        elif mutation == "bad_uuid":
            value["expected_postgres_uuid"] = "private-secret"
        elif mutation == "same_uuid":
            value["expected_documents_uuid"] = UUID_PG
        host.config.write_text(json.dumps(value), encoding="utf-8")
    result = observer.observe()
    assert_closed_contract(result)
    assert not any(result["services"].values())
    assert not any(value for key, value in result.items() if key not in {"format", "services"})
    assert host.commands == []


def test_wrong_data_uuid_blocks_every_capacity_read(observer, host):
    host.behavior.mounts["/srv/acti/postgresql"] = UUID_DOCS
    result = observer.observe()
    assert result["mounts_verified"] is False and result["storage_capacity"] is False
    assert host.disk_calls == []


@pytest.mark.parametrize("path", ["/", "/srv/acti/postgresql", "/srv/acti/documents"])
def test_any_filesystem_below_fifteen_percent_is_unhealthy(observer, host, path):
    host.capacities[path] = 149
    result = observer.observe()
    assert result["mounts_verified"] is True and result["storage_capacity"] is False


def test_fifteen_percent_boundary_is_accepted(observer, host):
    host.capacities.update({"/": 150, "/srv/acti/postgresql": 150, "/srv/acti/documents": 150})
    assert observer.observe()["storage_capacity"] is True


@pytest.mark.parametrize("kind", ["missing", "duplicate", "stopped", "unhealthy", "no_health"])
def test_ambiguous_or_unhealthy_redis_cannot_supply_heartbeat(observer, host, kind):
    if kind == "missing":
        host.behavior.docker_rows = [x for x in host.behavior.docker_rows if x[1] != "redis"]
    elif kind == "duplicate":
        host.behavior.docker_rows.append(("a" * 64, "redis"))
    elif kind == "stopped":
        host.behavior.states["redis"]["Running"] = False
    elif kind == "unhealthy":
        host.behavior.states["redis"]["Health"]["Status"] = "unhealthy"
    else:
        host.behavior.states["redis"].pop("Health")
    result = observer.observe()
    assert result["services"]["redis"] is False
    assert result["worker_heartbeat"] is False


@pytest.mark.parametrize(
    ("age", "healthy"),
    [(-5, True), (-5.01, False), (30, True), (30.01, False), (0, True), (300, False)],
)
def test_worker_heartbeat_has_bounded_past_and_future_age(observer, host, age, healthy):
    host.behavior.heartbeat = str(NOW - age)
    assert observer.observe()["worker_heartbeat"] is healthy


def test_heartbeat_uses_probe_time_instead_of_time_before_slow_checks(observer, host, monkeypatch):
    wall_times = iter((NOW, NOW + 10))
    monkeypatch.setattr(observer.time, "time", lambda: next(wall_times))
    host.behavior.heartbeat = str(NOW + 9)
    assert observer.observe()["worker_heartbeat"] is True


@pytest.mark.parametrize("value", ["", "NaN", "Infinity", "-Infinity", "private-secret", "1\n2"])
def test_missing_or_nonfinite_heartbeat_is_unknown(observer, host, value):
    host.behavior.heartbeat = value
    assert observer.observe()["worker_heartbeat"] is False


@pytest.mark.parametrize(
    "value",
    ["1|0|0", "0|1|0", "0|0|1", "-1|0|0", "", "private-secret", "0|0|0\n0|0|0", "0|0", "0|0|NaN"],
)
def test_failed_due_or_expired_work_is_unhealthy(observer, host, value):
    host.behavior.outbox = value
    assert observer.observe()["outbox_healthy"] is False


@pytest.mark.parametrize(
    ("age", "healthy"), [(30 * 3600, True), (30 * 3600 + 1, False), (-300, True), (-301, False)]
)
def test_backup_freshness_rejects_stale_and_excessive_future_receipts(observer, host, age, healthy):
    os.utime(host.backup, (NOW - age, NOW - age))
    assert observer.observe()["backup_fresh"] is healthy


@pytest.mark.parametrize(
    ("age", "healthy"), [(8 * 86400, True), (8 * 86400 + 1, False), (-300, True), (-301, False)]
)
def test_full_backup_check_has_independent_freshness(observer, host, age, healthy):
    os.utime(host.check, (NOW - age, NOW - age))
    assert observer.observe()["backup_check_fresh"] is healthy


@pytest.mark.parametrize(
    "kind",
    [
        "unsafe_root",
        "unsafe_receipt",
        "symlink_directory",
        "bad_snapshot",
        "bad_source_sha",
        "wrong_run",
        "bad_json",
        "read_data_false",
        "read_data_numeric",
    ],
)
def test_untrusted_backup_evidence_never_becomes_a_success(observer, host, kind):
    expected_check = False
    if kind == "unsafe_root":
        host.metadata[host.state] = {"st_uid": 1000}
    elif kind == "unsafe_receipt":
        host.metadata[host.backup] = {"st_mode": stat.S_IFREG | 0o644}
    elif kind == "symlink_directory":
        host.metadata[host.backup.parent] = {"st_mode": stat.S_IFLNK | 0o777}
    elif kind == "bad_json":
        host.backup.write_text("private-secret", encoding="utf-8")
    elif kind in {"read_data_false", "read_data_numeric"}:
        host.check.write_text(
            json.dumps(
                {"status": "checked", "read_data": False if kind == "read_data_false" else 1}
            ),
            encoding="utf-8",
        )
    else:
        value = json.loads(host.backup.read_text())
        field = {
            "bad_snapshot": "snapshot_id",
            "bad_source_sha": "source_sha",
            "wrong_run": "run_id",
        }[kind]
        value[field] = "private-secret"
        host.backup.write_text(json.dumps(value), encoding="utf-8")
        expected_check = True
    result = observer.observe()
    if kind in {"read_data_false", "read_data_numeric"}:
        assert result["backup_check_fresh"] is False
    else:
        assert result["backup_fresh"] is False
        assert result["backup_check_fresh"] is expected_check


def test_missing_result_in_an_owned_in_progress_directory_is_not_a_new_success(observer, host):
    (host.state / "55555555-5555-4555-8555-555555555555").mkdir()
    host.backup.unlink()
    result = observer.observe()
    assert result["backup_fresh"] is False and result["backup_check_fresh"] is True


def test_receipt_scan_is_bounded_and_does_not_accept_partial_results(observer, host, monkeypatch):
    monkeypatch.setattr(observer, "MAX_STATE_ENTRIES", 1)
    result = observer.observe()
    assert result["backup_fresh"] is False and result["backup_check_fresh"] is False


@pytest.mark.parametrize("command", ["findmnt", "docker", "systemctl"])
def test_oversized_dependency_output_never_becomes_success(observer, host, command):
    host.behavior.oversize_on = command
    result = observer.observe()
    assert_closed_contract(result)
    if command == "findmnt":
        assert result["mounts_verified"] is False and host.disk_calls == []
    elif command == "docker":
        assert not any(result["services"].values())
    else:
        assert result["backup_fresh"] is False and result["backup_check_fresh"] is False


@pytest.mark.parametrize("identifier", ["--privileged", "a" * 63, "A" * 64])
def test_untrusted_container_identifiers_cannot_be_used_for_exec(observer, host, identifier):
    host.behavior.docker_rows[0] = (identifier, "frontend")
    result = observer.observe()
    assert not any(result["services"].values())
    assert result["outbox_healthy"] is False and result["worker_heartbeat"] is False


def test_private_health_log_output_is_not_exposed(observer, host):
    host.behavior.states["api"]["Health"]["Log"] = [{"Output": "private-secret"}]
    result = observer.observe()
    assert result["services"]["api"] is True
    assert "private" not in json.dumps(result)


def test_open_file_metadata_is_rechecked_before_parsing(observer, host, monkeypatch):
    original = observer.os.fstat

    def changed_owner(descriptor):
        info = original(descriptor)
        info.st_uid = 1000
        return info

    monkeypatch.setattr(observer.os, "fstat", changed_owner)
    result = observer.observe()
    assert not any(result["services"].values())
    assert host.commands == []


@pytest.mark.parametrize("failure", ["failed_backup", "failed_check", "inactive_timer"])
def test_recent_receipts_cannot_hide_failed_jobs_or_disabled_timers(observer, host, failure):
    setattr(host.behavior, failure, True)
    result = observer.observe()
    if failure != "failed_check":
        assert result["backup_fresh"] is False
    if failure != "failed_backup":
        assert result["backup_check_fresh"] is False


@pytest.mark.parametrize("command", ["findmnt", "docker", "systemctl"])
def test_dependency_timeouts_are_redacted_and_leave_a_valid_contract(observer, host, command):
    host.behavior.timeout_on = command
    result = observer.observe()
    assert_closed_contract(result)
    assert "private" not in json.dumps(result)


def test_total_budget_stops_new_commands_and_reduces_each_remaining_timeout(observer, host):
    host.behavior.advance = 4.9
    result = observer.observe()
    assert_closed_contract(result)
    assert len(host.commands) <= 4
    assert host.commands[-1][1]["timeout"] <= 3.4
    assert result["backup_fresh"] is False and result["backup_check_fresh"] is False


def test_no_ambient_environment_or_original_command_can_redirect_the_observer(
    observer,
    host,
    monkeypatch,
):
    monkeypatch.setenv("DOCKER_HOST", "tcp://private.invalid:2375")
    monkeypatch.setenv("ACTI_OBSERVER_CONFIG", "/private/foreign.json")
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "touch /private/foreign")
    result = observer.observe()
    assert result["services"]["api"] is True
    assert all("private" not in json.dumps(argv) for argv, _ in host.commands)
    assert all(
        "--host=unix:///var/run/docker.sock" in argv
        for argv, _ in host.commands
        if Path(argv[0]).name == "docker"
    )


@pytest.mark.parametrize(
    ("args", "uid", "exit_code"), [([], 0, 0), (["--config", "private"], 0, 2), ([], 1000, 1)]
)
def test_cli_is_root_only_argument_free_and_emits_only_closed_json(
    observer,
    host,
    monkeypatch,
    capsys,
    args,
    uid,
    exit_code,
):
    monkeypatch.setattr(observer.sys, "argv", [str(SOURCE), *args])
    monkeypatch.setattr(observer.os, "geteuid", lambda: uid, raising=False)
    assert observer.main() == exit_code
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert_closed_contract(result)
    assert captured.err == ""
    if exit_code:
        assert host.commands == []


def test_cli_does_not_reveal_subprocess_diagnostics_or_state_metadata(
    observer, host, monkeypatch, capsys
):
    host.behavior.error_on = "docker"
    monkeypatch.setattr(observer.sys, "argv", [str(SOURCE)])
    assert observer.main() == 0
    output = capsys.readouterr()
    assert "private" not in output.out and output.err == ""
    assert_closed_contract(json.loads(output.out))


def test_real_isolated_cli_ignores_sibling_and_environment_import_shadows(tmp_path):
    executable = tmp_path / "observe"
    executable.write_bytes(SOURCE.read_bytes())
    executable.chmod(0o755)
    (tmp_path / "json.py").write_text(
        "raise RuntimeError('private-import-shadow')\n", encoding="utf-8"
    )
    environment = dict(os.environ, PYTHONPATH=str(tmp_path), SSH_ORIGINAL_COMMAND="private-command")
    command = [str(executable)] if os.name == "posix" else [sys.executable, "-I", str(executable)]
    result = subprocess.run(
        [*command, "--foreign"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 2
    assert_closed_contract(json.loads(result.stdout))
    assert result.stderr == "" and "private" not in result.stdout
