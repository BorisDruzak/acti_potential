"""Run the FILESERVER watchdog on synthetic local metadata; no infrastructure calls."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/windows-backup-watchdog.ps1"
POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell 5.1 required")


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "owned-backup"
    root.mkdir()
    owner = str(uuid4())
    (root / ".acti-backup-owner").write_text(owner, encoding="utf-8")
    (root / "ops").mkdir()
    repo = root / "repository"
    (repo / "snapshots").mkdir(parents=True)
    (repo / "config").write_bytes(b"opaque encrypted config")
    (repo / "snapshots" / ("a" * 64)).write_bytes(b"opaque encrypted snapshot")
    (repo / "data").mkdir()
    (repo / "data" / "pack").write_bytes(b"encrypted pack")
    return root, owner


def run_watchdog(repository, *options):
    root, owner = repository
    assert SCRIPT.is_file(), "The watchdog implementation is required"
    defaults = [] if "-MinimumFreeGiB" in options else ["-MinimumFreeGiB", "0"]
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-RootPath",
            str(root),
            "-ExpectedOwnerId",
            owner,
            *defaults,
            "-CheckOnly",
            *options,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert not result.stderr, result.stderr
    report = json.loads(result.stdout)
    assert str(root).lower() not in result.stdout.lower()
    return result.returncode, report


def test_healthy_metadata_check_is_read_only_and_does_not_need_restic_password(repository):
    root, _ = repository
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    code, report = run_watchdog(repository)
    assert code == 0 and report["status"] == "healthy"
    assert report["event_id"] == 1000
    assert report["check_only"] is True
    assert report["snapshot_count"] == 1
    assert report["repository_bytes"] == 62
    assert report["issues"] == []
    assert list((root / "ops").iterdir()) == []
    assert all(p.stat().st_mtime_ns == modified for p, modified in before.items())


def test_foreign_owner_is_rejected_without_writing_under_root(repository):
    root, _ = repository
    (root / ".acti-backup-owner").write_text(str(uuid4()), encoding="utf-8")
    code, report = run_watchdog(repository)
    assert code != 0 and report["event_id"] == 1004
    assert "owner_mismatch" in report["issues"]
    assert list((root / "ops").iterdir()) == []


@pytest.mark.parametrize("age_hours", [31, -1])
def test_stale_or_future_snapshot_timestamp_fails(repository, age_hours):
    root, _ = repository
    snapshot = root / "repository" / "snapshots" / ("a" * 64)
    stamp = time.time() - age_hours * 3600
    os.utime(snapshot, (stamp, stamp))
    code, report = run_watchdog(repository)
    assert code != 0 and report["event_id"] == 1002


def test_missing_snapshot_is_stale_even_if_a_pack_is_fresh(repository):
    root, _ = repository
    (root / "repository" / "snapshots" / ("a" * 64)).unlink()
    code, report = run_watchdog(repository)
    assert code != 0 and "snapshot_missing" in report["issues"]
    assert report["event_id"] == 1002


def test_repository_size_and_free_space_thresholds_fail(repository):
    code, report = run_watchdog(repository, "-MaximumRepositoryGiB", "0.000000001")
    assert code != 0 and "repository_size_limit" in report["issues"]
    assert report["event_id"] == 1003
    code, report = run_watchdog(repository, "-MinimumFreeGiB", "1048576")
    assert code != 0 and "free_space_limit" in report["issues"]


def test_repository_traversal_cap_reports_unknown_without_success(repository):
    code, report = run_watchdog(repository, "-MaximumEntries", "1")
    assert code != 0 and report["repository_bytes"] is None
    assert "repository_scan_limit" in report["issues"]
    assert report["event_id"] == 1003


def test_missing_config_is_rejected_as_unexpected_repository(repository):
    root, _ = repository
    (root / "repository" / "config").unlink()
    code, report = run_watchdog(repository)
    assert code != 0 and report["event_id"] == 1004
    assert "repository_config_missing" in report["issues"]


def test_junction_cannot_escape_repository(repository, tmp_path):
    root, _ = repository
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "preserved.txt"
    sentinel.write_text("must remain untouched", encoding="utf-8")
    junction = root / "repository" / "data" / "escape"
    command = (
        "New-Item -ItemType Junction -Path '"
        + str(junction).replace("'", "''")
        + "' -Target '"
        + str(outside).replace("'", "''")
        + "' | Out-Null"
    )
    subprocess.run([POWERSHELL, "-NoProfile", "-Command", command], check=True)
    code, report = run_watchdog(repository)
    assert code != 0 and "reparse_point_refused" in report["issues"]
    assert report["event_id"] == 1004
    assert sentinel.read_text(encoding="utf-8") == "must remain untouched"
