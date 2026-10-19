"""Real OpenSSL certificate checks; never connect to an application or modify its TLS."""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "deploy/renew-tls.py"
SPEC = importlib.util.spec_from_file_location("acti_tls_renewal", SOURCE)
renew = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renew)


def test_management_certificate_renewal_can_keep_its_original_endpoint():
    public = "https://documents.example.invalid"
    assert renew.renewal_endpoint({"ACTI_ORIGIN": public}) == (
        public, "documents.example.invalid",
    )
    assert renew.renewal_endpoint({
        "ACTI_ORIGIN": public, "ACTI_TLS_RENEWAL_ORIGIN": "https://127.0.0.1/",
    }) == ("https://127.0.0.1", "127.0.0.1")


@pytest.mark.parametrize("origin", [
    "http://localhost", "https://user:password@localhost", "https://localhost:444",
    "https://localhost/path", "https://localhost?query", "https://localhost#fragment",
])
def test_management_renewal_endpoint_rejects_unsafe_overrides(origin):
    with pytest.raises(renew.RenewalError, match="invalid_origin"):
        renew.renewal_endpoint({"ACTI_ORIGIN": "https://example.invalid",
                                "ACTI_TLS_RENEWAL_ORIGIN": origin})


def openssl_binary():
    command = shutil.which("openssl")
    windows = Path("C:/Program Files/Git/usr/bin/openssl.exe")
    return command or (str(windows) if windows.is_file() else None)


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    binary = openssl_binary()
    if not binary:
        pytest.skip("OpenSSL is required for real certificate verification")
    root = tmp_path_factory.mktemp("tls-synthetic")

    def run(*args):
        subprocess.run([binary, *map(str, args)], capture_output=True, check=True, timeout=20)

    for name in ("ca", "wrong-ca"):
        run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "730", "-sha256",
            "-subj", "/CN=Synthetic test authority", "-keyout", root / (name + ".key"),
            "-out", root / (name + ".crt"))
    run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
        "-keyout", root / "server.key", "-out", root / "server.csr")
    run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
        "-keyout", root / "wrong.key", "-out", root / "wrong.csr")
    run("req", "-new", "-key", root / "server.key", "-subj", "/CN=other.invalid",
        "-out", root / "wrong-subject.csr")
    extensions = {
        "good": "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\nbasicConstraints=CA:FALSE\n",
        "wrong-san": "subjectAltName=DNS:other.invalid\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\nbasicConstraints=CA:FALSE\n",
        "wrong-eku": "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=clientAuth\nkeyUsage=digitalSignature,keyEncipherment\nbasicConstraints=CA:FALSE\n",
    }
    serial = 1
    for name, extension, csr, ca, days in (
        ("old", "good", "server", "ca", "365"),
        ("new", "good", "server", "ca", "365"),
        ("wrong-san", "wrong-san", "server", "ca", "365"),
        ("wrong-eku", "wrong-eku", "server", "ca", "365"),
        ("wrong-key", "good", "wrong", "ca", "365"),
        ("wrong-subject", "good", "wrong-subject", "ca", "365"),
        ("wrong-ca", "good", "server", "wrong-ca", "365"),
        ("short-lived", "good", "server", "ca", "1"),
        ("shorter", "good", "server", "ca", "180"),
        ("outlives-ca", "good", "server", "ca", "731"),
    ):
        ext = root / (extension + ".ext")
        ext.write_text("[server]\n" + extensions[extension], encoding="utf-8")
        run("x509", "-req", "-in", root / (csr + ".csr"), "-CA", root / (ca + ".crt"),
            "-CAkey", root / (ca + ".key"), "-set_serial", hex(serial), "-days", days,
            "-sha256", "-extfile", ext, "-extensions", "server", "-out", root / (name + ".crt"))
        serial += 1
    return root, binary


def test_real_renewed_certificate_keeps_key_and_identity(certificates):
    root, binary = certificates
    result = renew.validate_candidate(root / "new.crt", root / "old.crt",
                                      root / "ca.crt", root / "server.key", "localhost", binary)
    assert result["serial"] != renew.certificate_profile(root / "old.crt", binary)["serial"]


@pytest.mark.parametrize("candidate", ["wrong-san", "wrong-eku", "wrong-key", "wrong-subject", "wrong-ca", "short-lived", "shorter", "outlives-ca", "old"])
def test_real_mismatched_or_unsafe_certificate_is_rejected(certificates, candidate):
    root, binary = certificates
    with pytest.raises(renew.RenewalError):
        renew.validate_candidate(root / (candidate + ".crt"), root / "old.crt",
                                 root / "ca.crt", root / "server.key", "localhost", binary)


def test_csr_must_match_existing_identity(certificates):
    root, binary = certificates
    renew.validate_csr(root / "server.csr", root / "old.crt", root / "server.key", binary)
    with pytest.raises(renew.RenewalError):
        renew.validate_csr(root / "wrong.csr", root / "old.crt", root / "server.key", binary)


def test_failed_activation_rolls_back_matching_old_certificate(tmp_path):
    target = tmp_path / "server.crt"
    target.write_bytes(b"old")
    events = []

    def activate():
        events.append(target.read_bytes())
        if target.read_bytes() == b"new":
            raise renew.RenewalError("new_probe_failed")

    with pytest.raises(renew.RenewalError, match="activation_failed_rolled_back"):
        renew.activate_with_rollback(target, b"new", b"old", activate)
    assert target.read_bytes() == b"old"
    assert events == [b"new", b"old"]


def test_failed_rollback_never_reports_success(tmp_path):
    target = tmp_path / "server.crt"
    target.write_bytes(b"old")

    def fail():
        raise renew.RenewalError("probe_failed")

    with pytest.raises(renew.RenewalError, match="activation_and_rollback_failed"):
        renew.activate_with_rollback(target, b"new", b"old", fail)


def test_atomic_install_leaves_only_complete_certificate(tmp_path):
    target = tmp_path / "server.crt"
    target.write_bytes(b"old")
    renew.atomic_certificate(target, b"new")
    assert target.read_bytes() == b"new"
    assert not any(p.name.endswith(".partial") for p in tmp_path.iterdir())


def valid_receipt():
    run_id, sha = str(uuid4()), "a" * 40
    value = {"owner": renew.OWNER, "run_id": run_id, "source_sha": sha,
             "frontend": {"id": "b" * 64, "image": "sha256:" + "c" * 64, "source_sha": sha},
             "before": {name: "d" * 64 for name in renew.TLS_FILES}, "status": "prepared",
             "previous_sha256": "d" * 64, "candidate_sha256": "e" * 64,
             "previous_der_sha256": "f" * 64, "candidate_der_sha256": "0" * 64}
    return value, run_id, sha


@pytest.mark.parametrize("change", ["owner", "sha", "state", "escape", "hash", "frontend", "extra"])
def test_foreign_or_tampered_receipt_fails_before_any_file_access(change):
    receipt, run_id, sha = valid_receipt()
    renew.validate_receipt(receipt, run_id, sha)
    if change == "owner":
        receipt["owner"] = "another-operation"
    elif change == "sha":
        receipt["source_sha"] = "1" * 40
    elif change == "state":
        receipt["status"] = "arbitrary"
    elif change == "escape":
        receipt["before"]["../../unrelated"] = "a" * 64
    elif change == "hash":
        receipt["candidate_sha256"] = "not-a-digest"
    elif change == "frontend":
        receipt["frontend"]["source_sha"] = "2" * 40
    else:
        receipt["replacement_key"] = "/unrelated"
    with pytest.raises(renew.RenewalError):
        renew.validate_receipt(receipt, run_id, sha)


@pytest.mark.parametrize("body", [b"<html>ready</html>", b"[]", b'{"status":"live"}',
    b'{"status":"ready","checks":{"database":true,"redis":false,"storage":true}}',
    b'{"status":"ready","checks":{"database":1,"redis":1,"storage":1}}'])
def test_readiness_requires_real_positive_boolean_checks(body):
    with pytest.raises(renew.RenewalError):
        renew.validate_readiness(body)
    renew.validate_readiness(json.dumps({"status": "ready", "checks": {
        "database": True, "redis": True, "storage": True}}).encode())


def test_probe_never_follows_redirects():
    with pytest.raises(renew.RenewalError, match="https_redirect_forbidden"):
        renew.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.invalid/")


@pytest.mark.skipif(os.name != "posix", reason="Real Unix permissions and symlinks")
def test_real_symlink_and_publicly_writable_private_file_are_denied(tmp_path):
    key = tmp_path / "key"
    key.write_text("synthetic")
    key.chmod(0o666)
    with pytest.raises(renew.RenewalError, match="unsafe_path_permissions"):
        renew.trusted(key, private=True)
    link = tmp_path / "link"
    link.symlink_to(key)
    with pytest.raises(renew.RenewalError, match="symlink_forbidden"):
        renew.trusted(link, private=True)


def test_foreign_file_owner_is_denied(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("synthetic")
    original = Path.stat

    def wrong_owner(path, *args, **kwargs):
        if path == key:
            return SimpleNamespace(st_uid=12345, st_mode=stat.S_IFREG | 0o600)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", wrong_owner)
    with pytest.raises(renew.RenewalError, match="unsafe_path_permissions"):
        renew.trusted(key, private=True)


def test_changed_receipt_artifact_cannot_reach_activation(tmp_path, monkeypatch):
    instance = object.__new__(renew.Renewal)
    receipt, run_id, sha = valid_receipt()
    instance.expected_sha, instance.binary = sha, "unused"
    (tmp_path / "previous.crt").write_bytes(b"tampered")
    (tmp_path / "candidate.crt").write_bytes(b"new")
    monkeypatch.setattr(instance, "preflight", lambda: receipt["frontend"])
    monkeypatch.setattr(instance, "receipt", lambda run: (tmp_path, receipt))
    monkeypatch.setattr(instance, "unchanged_inputs", lambda value: None)
    monkeypatch.setattr(renew, "trusted", lambda path, **kwargs: Path(path))
    with pytest.raises(renew.RenewalError, match="receipt_artifact_changed"):
        instance.change(run_id)


def test_changed_frontend_container_cannot_reach_tls_files(tmp_path, monkeypatch):
    instance = object.__new__(renew.Renewal)
    receipt, run_id, sha = valid_receipt()
    instance.expected_sha = sha
    monkeypatch.setattr(instance, "preflight", lambda: receipt["frontend"] | {"id": "9" * 64})
    monkeypatch.setattr(instance, "receipt", lambda run: (tmp_path, receipt))
    with pytest.raises(renew.RenewalError, match="frontend_changed"):
        instance.change(run_id)
