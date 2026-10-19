#!/usr/bin/env python3
"""Manual certificate renewal with an unchanged CA/key and atomic leaf replacement."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import socket
import ssl
import stat
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, build_opener
from uuid import UUID, uuid4

TLS_ROOT = Path("/etc/acti-v3/tls")
STATE_ROOT = Path("/var/lib/acti-v3/tls-renewal")
RUNTIME_ENV = Path("/etc/acti-v3/runtime.env")
OWNER = "acti-tls-renewal-v1"
MIN_VALIDITY = 30 * 86400
TLS_FILES = {"ca.key", "server.key", "ca.crt", "server.crt", "server.csr", "server.ext"}


class RenewalError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RenewalError("https_redirect_forbidden")


def require(condition, code):
    if not condition:
        raise RenewalError(code)


def renewal_endpoint(values):
    """Keep the management leaf separate from an externally issued domain leaf."""
    origin = values.get("ACTI_TLS_RENEWAL_ORIGIN", values["ACTI_ORIGIN"])
    try:
        parsed = urlsplit(origin)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and parsed.port in {None, 443}
                 and not parsed.query and not parsed.fragment and parsed.path in {"", "/"})
    except ValueError:
        valid = False
    require(valid, "invalid_origin")
    return origin.rstrip("/"), parsed.hostname


def command(arguments, *, data=None, timeout=20):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("OPENSSL_") and key not in {"LD_PRELOAD", "LD_LIBRARY_PATH"}}
    env.update(LC_ALL="C", LANG="C")
    try:
        result = subprocess.run(arguments, input=data, capture_output=True,
                                timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise RenewalError("bounded_command_failed") from None
    require(result.returncode == 0, "bounded_command_failed")
    return result.stdout


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def spki(path, kind, binary):
    if kind == "key":
        return hashlib.sha256(command([binary, "pkey", "-in", str(path), "-passin", "pass:",
                                       "-pubout", "-outform", "DER"])).hexdigest()
    program = "req" if kind == "csr" else "x509"
    pem = command([binary, program, "-in", str(path), "-pubkey", "-noout"])
    return hashlib.sha256(command([binary, "pkey", "-pubin", "-outform", "DER"], data=pem)).hexdigest()


def certificate_profile(path, binary="/usr/bin/openssl"):
    try:
        decoded = ssl._ssl._test_decode_cert(str(path))
        raw = ssl.PEM_cert_to_DER_cert(Path(path).read_text(encoding="ascii"))
        result = {
            "subject": command([binary, "x509", "-in", str(path), "-subject", "-nameopt",
                                "RFC2253", "-noout"]).decode().strip(),
            "sans": sorted([list(value) for value in decoded.get("subjectAltName", ())]),
            "serial": str(int(decoded["serialNumber"], 16)),
            "not_before": ssl.cert_time_to_seconds(decoded["notBefore"]),
            "not_after": ssl.cert_time_to_seconds(decoded["notAfter"]),
            "der_sha256": hashlib.sha256(raw).hexdigest(),
            "spki_sha256": spki(path, "cert", binary),
        }
        for name in ("extendedKeyUsage", "keyUsage", "basicConstraints"):
            result[name] = " ".join(command([binary, "x509", "-in", str(path), "-noout",
                                             "-ext", name]).decode().split())
        return result
    except (OSError, ValueError, ssl.SSLError, KeyError):
        raise RenewalError("invalid_certificate") from None


def verify_hosts(path, ca, hosts, binary):
    require(bool(hosts), "missing_sans")
    for kind, value in hosts:
        require(kind in {"DNS", "IP Address"} and value and "*" not in value,
                "unsupported_san")
        flag = "-verify_hostname" if kind == "DNS" else "-verify_ip"
        command([binary, "verify", "-CAfile", str(ca), "-purpose", "sslserver",
                 flag, value, str(path)])


def validate_candidate(candidate, previous, ca, key, hostname, binary="/usr/bin/openssl"):
    old = certificate_profile(previous, binary)
    new = certificate_profile(candidate, binary)
    authority = certificate_profile(ca, binary)
    for name in ("subject", "sans", "spki_sha256", "extendedKeyUsage", "keyUsage", "basicConstraints"):
        require(new[name] == old[name], "certificate_identity_changed")
    require(new["serial"] != old["serial"], "serial_not_rotated")
    require(new["spki_sha256"] == spki(key, "key", binary), "certificate_key_mismatch")
    now = time.time()
    require(new["not_before"] <= now + 30 and new["not_after"] > now + MIN_VALIDITY,
            "certificate_validity_rejected")
    require(new["not_after"] >= old["not_after"], "certificate_lifetime_shortened")
    require(new["not_after"] <= authority["not_after"], "certificate_outlives_ca")
    verify_hosts(candidate, ca, new["sans"], binary)
    # Verify the actual deployment host as well as all SAN entries.
    try:
        import ipaddress
        ipaddress.ip_address(hostname)
        kind = "IP Address"
    except ValueError:
        kind = "DNS"
    verify_hosts(candidate, ca, [(kind, hostname)], binary)
    return new


def validate_csr(csr, certificate, key, binary="/usr/bin/openssl"):
    command([binary, "req", "-in", str(csr), "-verify", "-noout"])
    subject = command([binary, "req", "-in", str(csr), "-subject", "-nameopt",
                       "RFC2253", "-noout"]).decode().strip()
    profile = certificate_profile(certificate, binary)
    require(subject == profile["subject"], "csr_subject_mismatch")
    require(spki(csr, "csr", binary) == profile["spki_sha256"] == spki(key, "key", binary),
            "csr_key_mismatch")


def validate_receipt(receipt, run_id, expected_sha):
    fields = {"owner", "run_id", "source_sha", "frontend", "before", "status", "previous_sha256",
              "candidate_sha256", "previous_der_sha256", "candidate_der_sha256"}
    require(isinstance(receipt, dict) and set(receipt) in (fields, fields | {"error_code"}),
            "invalid_receipt_fields")
    require(receipt["owner"] == OWNER and receipt["run_id"] == run_id
            and receipt["source_sha"] == expected_sha, "foreign_receipt")
    require(isinstance(receipt["status"], str) and receipt["status"] in
            {"prepared", "applying", "applied", "rolling_back", "rolled_back", "failed"},
            "invalid_receipt_state")
    before, frontend = receipt["before"], receipt["frontend"]
    require(isinstance(before, dict) and set(before) == TLS_FILES, "invalid_tls_input_set")
    require(isinstance(frontend, dict) and set(frontend) == {"id", "image", "source_sha"},
            "invalid_frontend_receipt")
    require(isinstance(frontend["id"], str) and re.fullmatch(r"[0-9a-f]{64}", frontend["id"])
            and isinstance(frontend["image"], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", frontend["image"])
            and frontend["source_sha"] == expected_sha, "foreign_frontend_receipt")
    hashes = list(before.values()) + [receipt[key] for key in
        ("previous_sha256", "candidate_sha256", "previous_der_sha256", "candidate_der_sha256")]
    require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes),
            "invalid_receipt_hash")
    require(receipt["previous_sha256"] == before["server.crt"]
            and receipt["previous_sha256"] != receipt["candidate_sha256"]
            and receipt["previous_der_sha256"] != receipt["candidate_der_sha256"],
            "invalid_certificate_receipt")


def validate_readiness(body):
    require(len(body) <= 16384, "https_health_failed")
    try:
        healthy = json.loads(body)
    except ValueError:
        raise RenewalError("https_health_failed") from None
    require(isinstance(healthy, dict) and healthy.get("status") == "ready"
            and isinstance(healthy.get("checks"), dict)
            and set(healthy["checks"]) == {"database", "redis", "storage"}
            and all(value is True for value in healthy["checks"].values()), "https_health_failed")


def trusted(path, *, private=False, directory=False):
    path = Path(path).absolute()
    require(path == path.resolve(strict=True), "symlink_forbidden")
    info = path.stat()
    require(info.st_uid == 0 and not info.st_mode & (0o077 if private else 0o022),
            "unsafe_path_permissions")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            "unsafe_path_type")
    for parent in path.parents:
        info = parent.stat()
        require(info.st_uid == 0 and not info.st_mode & 0o022, "unsafe_parent_permissions")
    return path


def atomic_certificate(target, value, *, mode=0o644):
    target = Path(target)
    temporary = target.parent / ("." + target.name + "." + uuid4().hex + ".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        if os.name == "posix":
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def activate_with_rollback(target, candidate, previous, activate):
    atomic_certificate(target, candidate)
    try:
        activate()
    except BaseException:
        try:
            atomic_certificate(target, previous)
            activate()
        except BaseException:
            raise RenewalError("activation_and_rollback_failed") from None
        raise RenewalError("activation_failed_rolled_back") from None


def load_backup():
    source = Path(__file__).with_name("external-backup.py")
    spec = importlib.util.spec_from_file_location("acti_tls_backup_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Renewal:
    def __init__(self, expected_sha):
        require(re.fullmatch(r"[0-9a-f]{40}", expected_sha) is not None, "full_source_sha_required")
        self.expected_sha = expected_sha
        self.binary = "/usr/bin/openssl"
        self.backup = load_backup()

    def preflight(self):
        require(os.name == "posix" and os.geteuid() == 0, "root_linux_required")
        _, release = self.backup.runtime_preflight(RUNTIME_ENV, self.expected_sha)
        require(Path(__file__).resolve() == release / "deploy/renew-tls.py", "unexpected_helper_release")
        trusted(TLS_ROOT, private=True, directory=True)
        require(stat.S_IMODE(TLS_ROOT.stat().st_mode) == 0o700, "unsafe_tls_directory_mode")
        for name in ("ca.key", "server.key"):
            trusted(TLS_ROOT / name, private=True)
            require(stat.S_IMODE((TLS_ROOT / name).stat().st_mode) == 0o600, "unsafe_key_mode")
            command([self.binary, "pkey", "-in", str(TLS_ROOT / name), "-passin", "pass:", "-check", "-noout"])
        for name in ("ca.crt", "server.crt", "server.csr", "server.ext"):
            trusted(TLS_ROOT / name)
        require(spki(TLS_ROOT / "ca.crt", "cert", self.binary) == spki(TLS_ROOT / "ca.key", "key", self.binary),
                "ca_key_mismatch")
        env = self.backup.protected(RUNTIME_ENV).read_text(encoding="utf-8")
        # Reuse the literal, non-evaluating runtime parser from the restore contract.
        spec = importlib.util.spec_from_file_location("acti_tls_restore_contract", release / "deploy/restore-verify.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.origin, self.hostname = renewal_endpoint(module.parse_env(env))
        return self.frontend()

    def frontend(self):
        value = command(["/usr/bin/docker", "ps", "--no-trunc", "--filter",
                         "label=com.docker.compose.project=acti-v3", "--filter",
                         "label=com.docker.compose.service=frontend", "--format", "{{.ID}}"]).decode().split()
        require(len(value) == 1 and re.fullmatch(r"[0-9a-f]{64}", value[0]), "frontend_not_unique")
        output = command(["/usr/bin/docker", "inspect", "--format",
                          '{"id":{{json .Id}},"image":{{json .Image}},"source_sha":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}', value[0]])
        result = json.loads(output)
        require(result.get("id") == value[0] and result.get("source_sha") == self.expected_sha,
                "frontend_sha_mismatch")
        return result

    def snapshots(self):
        return {name: digest(TLS_ROOT / name) for name in sorted(TLS_FILES)}

    def state(self, run_id=None):
        if not STATE_ROOT.exists():
            trusted(STATE_ROOT.parent, directory=True)
            STATE_ROOT.mkdir(mode=0o700)
        trusted(STATE_ROOT, private=True, directory=True)
        if run_id is None:
            run_id = str(uuid4())
            path = STATE_ROOT / run_id
            path.mkdir(mode=0o700)
            return path
        require(str(UUID(run_id)) == run_id, "invalid_run_id")
        return trusted(STATE_ROOT / run_id, private=True, directory=True)

    def write_receipt(self, directory, receipt):
        target = directory / "receipt.json"
        if target.exists():
            trusted(target, private=True)
        atomic_certificate(target, json.dumps(receipt, sort_keys=True, indent=2).encode(), mode=0o600)

    def receipt(self, run_id):
        directory = self.state(run_id)
        receipt = json.loads(trusted(directory / "receipt.json", private=True).read_text())
        validate_receipt(receipt, run_id, self.expected_sha)
        return directory, receipt

    def probe(self, expected, frontend):
        context = ssl.create_default_context(cafile=str(TLS_ROOT / "ca.crt"))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.hostname, 443), timeout=3) as sock:
                    with context.wrap_socket(sock, server_hostname=self.hostname) as tls:
                        actual = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
                require(actual == expected, "served_leaf_not_updated")
                opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
                with opener.open(self.origin + "/api/v1/health/ready", timeout=4) as response:
                    body = response.read(16385)
                    require(response.status == 200, "https_health_failed")
                    validate_readiness(body)
                require(self.frontend() == frontend, "frontend_changed")
                return
            except (OSError, RenewalError):
                time.sleep(0.25)
        raise RenewalError("https_activation_probe_failed")

    def unchanged_inputs(self, receipt):
        for name, expected in receipt["before"].items():
            if name != "server.crt":
                require(digest(TLS_ROOT / name) == expected, "tls_input_changed")

    def activate(self, receipt):
        require(self.frontend() == receipt["frontend"], "frontend_changed")
        self.unchanged_inputs(receipt)
        profile = certificate_profile(TLS_ROOT / "server.crt", self.binary)
        require(profile["der_sha256"] in {receipt["previous_der_sha256"], receipt["candidate_der_sha256"]},
                "unexpected_active_leaf")
        identity = receipt["frontend"]["id"]
        command(["/usr/bin/docker", "exec", identity, "nginx", "-t"])
        command(["/usr/bin/docker", "exec", identity, "nginx", "-s", "reload"])
        self.probe(profile["der_sha256"], receipt["frontend"])
        self.unchanged_inputs(receipt)

    def prepare(self, force=False):
        frontend = self.preflight()
        previous = certificate_profile(TLS_ROOT / "server.crt", self.binary)
        self.probe(previous["der_sha256"], frontend)
        if not force and previous["not_after"] - time.time() > MIN_VALIDITY:
            return {"status": "not_due", "source_sha": self.expected_sha}
        validate_csr(TLS_ROOT / "server.csr", TLS_ROOT / "server.crt", TLS_ROOT / "server.key", self.binary)
        before = self.snapshots()
        directory = self.state()
        # These files contain public material only. The existing private key stays in place.
        (directory / "previous.crt").write_bytes((TLS_ROOT / "server.crt").read_bytes())
        os.chmod(directory / "previous.crt", 0o600)
        extension = "[acti_server]\n" + (TLS_ROOT / "server.ext").read_text()
        require("[acti_server]" not in extension[len("[acti_server]\n"):], "extension_section_conflict")
        (directory / "server.ext").write_text(extension, encoding="utf-8")
        os.chmod(directory / "server.ext", 0o600)
        candidate = directory / "candidate.crt"
        command([self.binary, "x509", "-req", "-in", str(TLS_ROOT / "server.csr"),
                 "-CA", str(TLS_ROOT / "ca.crt"), "-CAkey", str(TLS_ROOT / "ca.key"),
                 "-passin", "pass:", "-set_serial", "0x" + secrets.token_hex(16), "-days", "365",
                 "-sha256", "-extfile", str(directory / "server.ext"), "-extensions", "acti_server",
                 "-out", str(candidate)])
        os.chmod(candidate, 0o600)
        current = validate_candidate(candidate, directory / "previous.crt", TLS_ROOT / "ca.crt",
                                     TLS_ROOT / "server.key", self.hostname, self.binary)
        require(self.snapshots() == before and self.frontend() == frontend, "runtime_changed")
        receipt = {"owner": OWNER, "run_id": directory.name, "source_sha": self.expected_sha,
                   "frontend": frontend, "before": before, "status": "prepared",
                   "previous_sha256": before["server.crt"], "candidate_sha256": digest(candidate),
                   "previous_der_sha256": previous["der_sha256"], "candidate_der_sha256": current["der_sha256"]}
        self.write_receipt(directory, receipt)
        return {"status": "prepared", "run_id": directory.name, "source_sha": self.expected_sha}

    def change(self, run_id, rollback=False):
        frontend = self.preflight()
        directory, receipt = self.receipt(run_id)
        require(frontend == receipt["frontend"], "frontend_changed")
        self.unchanged_inputs(receipt)
        old = trusted(directory / "previous.crt", private=True)
        new = trusted(directory / "candidate.crt", private=True)
        require(digest(old) == receipt["previous_sha256"] and digest(new) == receipt["candidate_sha256"],
                "receipt_artifact_changed")
        validate_candidate(new, old, TLS_ROOT / "ca.crt", TLS_ROOT / "server.key", self.hostname, self.binary)
        active = digest(TLS_ROOT / "server.crt")
        expected = receipt["candidate_sha256"] if rollback else receipt["previous_sha256"]
        require(active == expected, "active_certificate_changed")
        require(receipt["status"] in ({"applied"} if rollback else {"prepared", "rolled_back"}),
                "invalid_receipt_state")
        candidate, previous = (old, new) if rollback else (new, old)
        receipt["status"] = "rolling_back" if rollback else "applying"
        self.write_receipt(directory, receipt)
        try:
            activate_with_rollback(TLS_ROOT / "server.crt", candidate.read_bytes(), previous.read_bytes(),
                                   lambda: self.activate(receipt))
        except RenewalError as error:
            receipt["status"] = "failed"
            receipt["error_code"] = str(error)
            self.write_receipt(directory, receipt)
            raise
        receipt["status"] = "rolled_back" if rollback else "applied"
        self.write_receipt(directory, receipt)
        return {"status": receipt["status"], "run_id": run_id, "source_sha": self.expected_sha}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--force", action="store_true")
    for action in ("apply", "rollback"):
        commands.add_parser(action).add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        renewal = Renewal(args.expected_sha)
        # Share the exact lock used by external backup, including its protected-file checks.
        with renewal.backup.exclusive_lock():
            result = renewal.prepare(args.force) if args.action == "prepare" else renewal.change(
                args.run_id, rollback=args.action == "rollback")
        print(json.dumps(result))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, RenewalError) else "tls_renewal_failed_closed"
        print(json.dumps({"status": "FAIL", "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
