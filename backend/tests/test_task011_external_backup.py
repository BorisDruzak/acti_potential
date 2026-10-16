"""External-backup failure gates; never use a real repository or production data."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "deploy/external-backup.py"
SPEC = importlib.util.spec_from_file_location("acti_external_backup", MODULE_PATH)
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    if os.name != "posix":
        monkeypatch.setattr(backup.os, "O_NOFOLLOW", 0, raising=False)
    config = {
        "expected_repository_id": "a" * 64,
        "password_file": "/private/password",
        "restic_repository": "rclone:acti-backup:/repository",
        "ssh_config": "/private/ssh_config",
        "cache_dir": str(tmp_path / "cache"),
    }
    result = backup.Runner(config, "b" * 40, Path("/release"), Path("/private/runtime.env"))
    monkeypatch.setattr(result, "start_state", lambda: tmp_path)
    return result


def exported_fixture(runner, monkeypatch):
    run_id = str(uuid4())
    bundle = backup.DOCUMENTS_ROOT / "restore-verification" / run_id / "bundle"
    calls = []

    def exporter(*args):
        calls.append(args)
        if args[0] == "export":
            return {
                "status": "exported",
                "run_id": run_id,
                "bundle_source": str(bundle),
                "source_sha": runner.sha,
                "counts": {"files": 2},
            }
        return {"status": "cleaned"}

    monkeypatch.setattr(runner, "exporter", exporter)
    monkeypatch.setattr(runner, "verify_export", lambda value: (run_id, bundle))
    monkeypatch.setattr(runner, "recovery", lambda value: None)
    return calls, bundle


def test_repository_id_mismatch_prevents_export_and_all_writes(runner, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "restic", lambda *a, **kw: json.dumps({"id": "c" * 64}))
    monkeypatch.setattr(runner, "start_state", lambda: calls.append("state"))
    monkeypatch.setattr(runner, "exporter", lambda *a: calls.append("export"))
    with pytest.raises(backup.BackupSafetyError, match="repository_id_mismatch"):
        runner.backup()
    assert calls == []


@pytest.mark.parametrize("failure", ["command_failed", "partial_source_read", "repository_corrupt"])
def test_failed_backup_never_prunes_and_cleans_only_export_receipt(runner, monkeypatch, failure):
    exporter_calls, _ = exported_fixture(runner, monkeypatch)
    restic_calls = []

    def restic(*args, **kwargs):
        restic_calls.append(args)
        if args[0] == "cat":
            return json.dumps({"id": "a" * 64})
        raise backup.BackupSafetyError(failure)

    monkeypatch.setattr(runner, "restic", restic)
    with pytest.raises(backup.BackupSafetyError):
        runner.backup()
    assert [call[0] for call in restic_calls] == ["cat", "backup"]
    assert [call[0] for call in exporter_calls] == ["export", "cleanup"]
    assert exporter_calls[-1][-2] == "--run-id"


@pytest.mark.parametrize("status", [1, 3])
def test_restic_nonzero_including_partial_backup_exit3_fails(runner, monkeypatch, status):
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=status, stdout=b'{"snapshot_id":"' + b"d" * 64 + b'"}', stderr=b"private"
        ),
    )
    with pytest.raises(backup.BackupSafetyError, match="command_failed"):
        runner.restic("backup", "/bundle")


@pytest.mark.parametrize("snapshot_id", [None, "d" * 8, "d" * 63, "d" * 65])
def test_missing_full_snapshot_id_blocks_retention(runner, monkeypatch, snapshot_id):
    calls, _ = exported_fixture(runner, monkeypatch)
    restic_calls = []

    def restic(*args, **kwargs):
        restic_calls.append(args[0])
        if args[0] == "cat":
            return json.dumps({"id": "a" * 64})
        return json.dumps({"message_type": "summary", "snapshot_id": snapshot_id})

    monkeypatch.setattr(runner, "restic", restic)
    with pytest.raises(backup.BackupSafetyError, match="missing_full_snapshot_id"):
        runner.backup()
    assert restic_calls == ["cat", "backup"]
    assert calls[-1][0] == "cleanup"


def test_confirmed_full_snapshot_required_before_retention(runner, monkeypatch):
    exporter_calls, bundle = exported_fixture(runner, monkeypatch)
    snapshot = {
        "id": "d" * 64,
        "hostname": backup.HOST,
        "tags": [backup.TAG],
        "paths": [str(bundle)],
    }
    calls = []

    def restic(*args, **kwargs):
        calls.append(args)
        if args[0] == "cat":
            return json.dumps({"id": "a" * 64})
        if args[0] == "backup":
            return json.dumps({"message_type": "summary", "snapshot_id": "d" * 64})
        if args[0] == "snapshots":
            return json.dumps([snapshot])
        assert args[0] == "forget"
        assert "--prune" in args and "host,tags" in args
        return "[]"

    monkeypatch.setattr(runner, "restic", restic)
    assert runner.backup()["snapshot_id"] == "d" * 64
    assert [call[0] for call in calls] == [
        "cat",
        "backup",
        "snapshots",
        "snapshots",
        "cat",
        "forget",
    ]
    assert exporter_calls[-1][0] == "cleanup"


def test_foreign_snapshot_blocks_retention(runner, monkeypatch):
    _, bundle = exported_fixture(runner, monkeypatch)
    snapshot = {
        "id": "d" * 64,
        "hostname": backup.HOST,
        "tags": [backup.TAG],
        "paths": [str(bundle)],
    }
    calls = []

    def restic(*args, **kwargs):
        calls.append(args[0])
        if args[0] == "cat":
            return json.dumps({"id": "a" * 64})
        if args[0] == "backup":
            return json.dumps({"message_type": "summary", "snapshot_id": "d" * 64})
        return json.dumps(
            [snapshot] if len(args) == 2 else [snapshot, dict(snapshot, hostname="foreign")]
        )

    monkeypatch.setattr(runner, "restic", restic)
    with pytest.raises(backup.BackupSafetyError, match="foreign_snapshot_forbidden"):
        runner.backup()
    assert "forget" not in calls


def test_weekly_check_reads_all_data_and_does_not_prune(runner, monkeypatch):
    calls = []

    def restic(*args, **kwargs):
        calls.append(args)
        if args[0] == "cat":
            return json.dumps({"id": "a" * 64})
        raise backup.BackupSafetyError("repository_corrupt")

    monkeypatch.setattr(runner, "restic", restic)
    with pytest.raises(backup.BackupSafetyError):
        runner.check()
    assert calls == [("cat", "config"), ("check", "--read-data")]


def test_restic_env_does_not_inherit_ambient_secret_or_repository(runner, monkeypatch):
    captured = {}
    monkeypatch.setenv("RESTIC_REPOSITORY", "unrelated")
    monkeypatch.setenv("RESTIC_PASSWORD", "never-forward")

    def command(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return b"[]"

    monkeypatch.setattr(runner, "command", command)
    runner.restic("snapshots")
    assert "RESTIC_PASSWORD" not in captured["env"]
    assert "RESTIC_REPOSITORY" not in captured["env"]
    assert "--no-cache" in captured["argv"]
    assert "never-forward" not in repr(captured)


def test_unsafe_config_path_fails_before_read(tmp_path):
    with pytest.raises(backup.BackupSafetyError, match="unexpected_config_path"):
        backup.read_config(tmp_path / "config.json")


@pytest.mark.skipif(os.name != "posix", reason="Linux UID/mode enforcement")
def test_protected_path_rejects_symlinks_and_public_file(tmp_path):
    secret = tmp_path / "password"
    secret.write_text("test-only", encoding="utf-8")
    secret.chmod(0o644)
    with pytest.raises(backup.BackupSafetyError, match="private_path_permissions"):
        backup.protected(secret)
    link = tmp_path / "link"
    link.symlink_to(secret)
    with pytest.raises(backup.BackupSafetyError, match="symlink_path_forbidden"):
        backup.protected(link)


def test_fetch_rejects_short_id_before_remote_call(runner, monkeypatch):
    monkeypatch.setattr(
        runner, "restic", lambda *a, **kw: pytest.fail("must not access repository")
    )
    with pytest.raises(backup.BackupSafetyError, match="full_snapshot_id_required"):
        runner.fetch("abcd1234")


@pytest.mark.parametrize("actual,code", [("wrong-volume", 0), ("expected-pg", 1), ("", 0)])
def test_actual_mount_uuid_and_findmnt_success_required(monkeypatch, actual, code):
    calls = []

    def findmnt(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=code, stdout=actual.encode(), stderr=b"")

    monkeypatch.setattr(backup.subprocess, "run", findmnt)
    with pytest.raises(backup.BackupSafetyError, match="mount_uuid_mismatch"):
        backup.verify_expected_mounts(
            {backup.POSTGRES_ROOT: "expected-pg", backup.DOCUMENTS_ROOT: "expected-docs"}
        )
    assert calls[0][:5] == ["findmnt", "-rn", "-o", "UUID", "--mountpoint"]


def test_mount_contract_cannot_skip_documents_volume(monkeypatch):
    monkeypatch.setattr(
        backup.subprocess, "run", lambda *a, **kw: pytest.fail("no partial preflight")
    )
    with pytest.raises(backup.BackupSafetyError, match="incomplete_mount_contract"):
        backup.verify_expected_mounts({backup.POSTGRES_ROOT: "expected-pg"})


@pytest.fixture
def rclone_config(tmp_path, monkeypatch):
    config_root = tmp_path / "etc-acti-v3"
    directory = config_root / "backup"
    directory.mkdir(parents=True)
    monkeypatch.setattr(backup, "CONFIG_ROOT", config_root)
    # Permission/symlink checks are tested separately; this fixture exercises parsing.
    checked = []

    def protected(path, **kwargs):
        checked.append(Path(path))
        return Path(path)

    monkeypatch.setattr(backup, "protected", protected)
    ssh = {
        "hostname": "backup.example.invalid",
        "user": "testbackup",
        "port": "22",
        "identityfile": str(directory / "identity"),
        "userknownhostsfile": str(directory / "known_hosts"),
    }
    values = {
        "type": "sftp",
        "host": ssh["hostname"],
        "user": ssh["user"],
        "port": "22",
        "key_file": ssh["identityfile"],
        "known_hosts_file": ssh["userknownhostsfile"],
        "shell_type": "none",
        "disable_hashcheck": "true",
    }
    path = directory / "rclone.conf"

    def save(extra=""):
        path.write_text(
            "[acti-backup]\n" + "\n".join(f"{k} = {v}" for k, v in values.items()) + "\n" + extra,
            encoding="utf-8",
        )

    return path, ssh, values, checked, save


def test_rclone_exact_sftp_identity_matches_protected_ssh(rclone_config):
    path, ssh, _, checked, save = rclone_config
    save()
    backup.validate_rclone_config(path, ssh)
    assert checked == [path, Path(ssh["identityfile"]), Path(ssh["userknownhostsfile"])]


@pytest.mark.parametrize(
    "key,value",
    [
        ("host", "other.example.invalid"),
        ("user", "other"),
        ("port", "2222"),
        ("key_file", "/untrusted/key"),
        ("known_hosts_file", "/untrusted/known_hosts"),
    ],
)
def test_rclone_rejects_identity_drift(rclone_config, key, value):
    path, ssh, values, _, save = rclone_config
    values[key] = value
    save()
    with pytest.raises(backup.BackupSafetyError, match="rclone_ssh_identity_mismatch"):
        backup.validate_rclone_config(path, ssh)


@pytest.mark.parametrize(
    "key,value",
    [
        ("shell_type", "powershell"),
        ("disable_hashcheck", "false"),
        ("type", "local"),
        ("host", "${HOST}"),
        ("user", "%USER%"),
    ],
)
def test_rclone_rejects_shell_hashprobe_and_expansion(rclone_config, key, value):
    path, ssh, values, _, save = rclone_config
    values[key] = value
    save()
    with pytest.raises(backup.BackupSafetyError):
        backup.validate_rclone_config(path, ssh)


@pytest.mark.parametrize(
    "extra",
    [
        "ssh = powershell -Command anything\n",
        "pass = encrypted-password\n",
        "[other]\ntype = local\n",
        "[DEFAULT]\nuser = external\n",
    ],
)
def test_rclone_rejects_extra_exec_password_remote_or_defaults(rclone_config, extra):
    path, ssh, _, _, save = rclone_config
    save(extra)
    with pytest.raises(backup.BackupSafetyError):
        backup.validate_rclone_config(path, ssh)


def test_restic_uses_only_fixed_rclone_bridge(runner, monkeypatch):
    captured = {}

    def command(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return b"[]"

    monkeypatch.setenv("RCLONE_CONFIG", "/unsafe/config")
    monkeypatch.setenv("RCLONE_SFTP_SSH", "unsafe exec")
    monkeypatch.setattr(runner, "command", command)
    runner.restic("snapshots")
    argv = captured["argv"]
    assert argv[argv.index("--repo") + 1] == "rclone:acti-backup:/repository"
    assert "rclone.program=/usr/bin/rclone" in argv
    assert (
        "rclone.args=--config "
        + str(backup.CONFIG_ROOT / "backup/rclone.conf")
        + " serve restic --stdio"
        in argv
    )
    assert not any(item.startswith("sftp.command=") for item in argv)
    assert not any(key.startswith("RCLONE_") for key in captured["env"])
    assert "RESTIC_PASSWORD" not in captured["env"]
