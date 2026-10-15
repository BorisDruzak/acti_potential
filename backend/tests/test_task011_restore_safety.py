"""Pure safety checks; these never contact Docker, PostgreSQL or ACTI infrastructure."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "deploy/restore-verify.py"
spec = importlib.util.spec_from_file_location("acti_restore_verify", MODULE_PATH)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def sample_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(restore, "POSTGRES_ROOT", tmp_path / "pg")
    monkeypatch.setattr(restore, "DOCUMENTS_ROOT", tmp_path / "documents")
    run_id = str(uuid4())
    state_root = tmp_path / "private"
    receipt = {
        "run_id": run_id,
        "purpose": restore.PURPOSE,
        "target_database": restore.TARGET_DATABASE,
        "source_sha": "a" * 40,
        "state_root": str(state_root),
        "names": restore.run_names(run_id),
        "pg_copy": str(restore.owned_directory(restore.POSTGRES_ROOT, run_id)),
        "documents_copy": str(restore.owned_directory(restore.DOCUMENTS_ROOT, run_id)),
        "containers": {},
        "postgres_container": "b" * 64,
        "status": "initializing",
        "mounts": {
            str(restore.POSTGRES_ROOT): "expected-pg",
            str(restore.DOCUMENTS_ROOT): "expected-documents",
        },
    }
    return receipt, state_root / run_id / "receipt.json"


def test_url_changes_only_database_and_preserves_encoded_credentials():
    url = "postgresql+psycopg://acti_app:p%40ss%2Fword@postgres:5432/acti?sslmode=require"
    assert restore.clone_database_url(url) == url.replace("/acti?", "/acti_restore_test?")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pass@postgres/another",
        "postgresql://user:pass@postgres/acti_restore_test",
        "mssql://user:pass@source/acti",
        "postgresql://user:pass@postgres/acti?dbname=acti",
        "postgresql://user:pass@postgres/acti?options=-csearch_path=private",
        "postgresql://user:pass@postgres/acti#fragment",
        "postgresql://user:pass@postgres/acti\nACTI_ORIGIN=https://elsewhere.invalid",
    ],
)
def test_non_acti_database_and_driver_override_fail_closed(url):
    with pytest.raises(restore.RestoreSafetyError):
        restore.clone_database_url(url)


@pytest.mark.parametrize(
    "text",
    [
        "ACTI_X=$(touch /tmp/never)",
        "ACTI_X=`id`",
        "ACTI_X=one; rm -rf /",
        "ACTI_X=one\nACTI_X=two",
        "source /etc/profile",
    ],
)
def test_env_parser_cannot_execute_shell_or_accept_ambiguous_assignments(text):
    with pytest.raises(restore.RestoreSafetyError):
        restore.parse_env(text)


def test_env_quotes_are_parsed_as_literal_values():
    assert restore.parse_env(
        "# comment\nACTI_ORIGIN='https://example.invalid'\nexport ACTI_SOURCE_SHA=abc\n"
    ) == {"ACTI_ORIGIN": "https://example.invalid", "ACTI_SOURCE_SHA": "abc"}


@pytest.mark.parametrize(
    "key", ["../escape", "/absolute", ".", "..", "a/b", "a\\b", "C:escape", "a\nkey", "x" * 129]
)
def test_storage_references_cannot_escape_copy_root(key):
    with pytest.raises(restore.RestoreSafetyError):
        restore.storage_reference({"storage_key": key, "size": 1, "sha256": "a" * 64})


def test_manifest_detects_sequence_state_change_even_when_tables_match():
    manifest = {
        "tables": {"documents": {"count": 2, "digest": "a" * 64}},
        "sequences": {"outbox_delivery_ordinal_seq": {"last_value": "5", "is_called": True}},
    }
    different = json.loads(json.dumps(manifest))
    different["sequences"]["outbox_delivery_ordinal_seq"]["is_called"] = False
    with pytest.raises(restore.RestoreSafetyError, match="table_or_sequence_manifest_mismatch"):
        restore.assert_same_manifest(manifest, different)
    restore.assert_same_manifest(manifest, json.loads(json.dumps(manifest)))


def test_manifest_sql_covers_every_table_and_sequence_without_changing_source():
    sql = restore.manifest_sql(["documents", 'quoted"name'], ["outbox_delivery_ordinal_seq"])
    assert "REPEATABLE READ READ ONLY" in sql and 'public."quoted""name"' in sql
    assert "sha256" in sql and "is_called" in sql and "last_value" in sql
    assert not any(
        word in sql.upper() for word in ("INSERT ", "UPDATE ", "DELETE ", "NEXTVAL(", "SETVAL(")
    )


def test_foreign_toc_and_duplicate_or_incomplete_manifest_rejected():
    with pytest.raises(restore.RestoreSafetyError):
        restore.verify_toc(b"not an ACTI dump")
    with pytest.raises(restore.RestoreSafetyError):
        restore.parse_manifest(b'{"kind":"table","name":"users","count":1,"digest":"fake"}\n')


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_database", "acti"),
        ("purpose", "another-purpose"),
        ("pg_copy", "/"),
        ("documents_copy", "/srv/acti/documents/objects"),
        ("source_sha", "latest"),
    ],
)
def test_cleanup_receipt_cannot_select_production_or_arbitrary_paths(
    tmp_path, monkeypatch, field, value
):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    receipt[field] = value
    with pytest.raises(restore.RestoreSafetyError):
        restore.validate_receipt(receipt, path)


def test_cleanup_foreign_container_label_is_refused_before_delete(tmp_path, monkeypatch):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    verifier = restore.Verifier(receipt, path)
    monkeypatch.setattr(restore, "verify_expected_mounts", lambda expected: None)
    commands = []
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"some-container", returncode=0),
    )

    def docker(*args, **kwargs):
        commands.append(args)
        return b'{"org.acti.restore-verification.run":"another-run"}'

    monkeypatch.setattr(verifier, "docker", docker)
    with pytest.raises(restore.RestoreSafetyError, match="foreign_container_refused"):
        verifier.cleanup()
    assert not any(command[0] == "rm" for command in commands)


def test_unproven_preexisting_database_is_never_dropped_or_claimed_clean(tmp_path, monkeypatch):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    receipt["database_creation_started"] = True
    verifier = restore.Verifier(receipt, path)
    monkeypatch.setattr(restore, "verify_expected_mounts", lambda expected: None)
    monkeypatch.setattr(
        restore.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=b"", returncode=0)
    )
    monkeypatch.setattr(
        verifier, "database_marker", lambda: {"oid": "42", "marker": "preexisting-unowned"}
    )
    commands = []
    monkeypatch.setattr(verifier, "docker", lambda *args, **kwargs: commands.append(args))
    monkeypatch.setattr(verifier, "save", lambda: None)
    monkeypatch.setattr(restore, "remove_owned_directory", lambda *args: None)
    with pytest.raises(restore.RestoreSafetyError, match="foreign_database_refused"):
        verifier.cleanup()
    assert not any("dropdb" in command for command in commands)
    assert receipt.get("cleanup_verified") is not True


def test_cleanup_rechecks_mount_uuid_before_any_delete(tmp_path, monkeypatch):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    verifier = restore.Verifier(receipt, path)
    commands = []
    monkeypatch.setattr(restore, "run", lambda command, **kwargs: b"wrong-mounted-volume\n")
    monkeypatch.setattr(verifier, "docker", lambda *args, **kwargs: commands.append(args))
    with pytest.raises(restore.RestoreSafetyError, match="mount_uuid_mismatch"):
        verifier.cleanup()
    assert commands == [] and receipt.get("cleanup_verified") is not True


def test_clone_frontend_edge_is_owned_and_noninternal_while_backend_network_stays_internal(
    tmp_path, monkeypatch
):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    verifier = restore.Verifier(receipt, path)
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        return ("d" * 64).encode()

    monkeypatch.setattr(verifier, "docker", docker)
    monkeypatch.setattr(verifier, "save", lambda: None)
    verifier.create_network("network", internal=True)
    verifier.create_network("edge", internal=False)
    assert "--internal" in commands[0] and "--internal" not in commands[1]
    assert all(restore.LABEL + "=" + receipt["run_id"] in command for command in commands)
    assert receipt["network_id"] == receipt["edge_network_id"] == "d" * 64


def test_only_owned_frontend_can_join_edge_network(tmp_path, monkeypatch):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    receipt["containers"] = {"api": "a" * 64, "frontend": "f" * 64}
    receipt["edge_network_id"] = "e" * 64
    verifier = restore.Verifier(receipt, path)
    monkeypatch.setattr(verifier, "save", lambda: None)
    commands = []
    monkeypatch.setattr(verifier, "docker", lambda *args, **kwargs: commands.append(args))
    with pytest.raises(restore.RestoreSafetyError, match="invalid_edge_target"):
        verifier.connect_frontend_edge(receipt["containers"]["api"])
    assert commands == []
    verifier.connect_frontend_edge(receipt["containers"]["frontend"])
    assert commands == [("network", "connect", receipt["names"]["edge"], "f" * 64)]


@pytest.mark.parametrize("kind", ["edge", "network"])
def test_both_network_cleanup_ids_must_match_ownership_receipt(tmp_path, monkeypatch, kind):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    receipt["edge_network_id" if kind == "edge" else "network_id"] = "e" * 64
    verifier = restore.Verifier(receipt, path)
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"found", returncode=0),
    )
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        return (
            json.dumps({restore.LABEL: receipt["run_id"]}).encode()
            if ".Labels" in args[3]
            else ("f" * 64).encode()
        )

    monkeypatch.setattr(verifier, "docker", docker)
    with pytest.raises(restore.RestoreSafetyError, match="network_id_changed"):
        verifier.cleanup_network(kind)
    assert not any(command[:2] == ("network", "rm") for command in commands)


def test_failed_command_stderr_is_private_capped_and_not_exposed_in_error(
    tmp_path, monkeypatch, capsys
):
    diagnostic = tmp_path / "command-stderr.log"
    monkeypatch.setattr(restore, "DIAGNOSTIC_FILE", diagnostic)
    marker = b"SYNTHETIC-PRIVATE-STDERR"
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr=marker + b"x" * (2 * restore.DIAGNOSTIC_LIMIT),
            stdout=b"DO-NOT-STORE-STDOUT",
        ),
    )
    with pytest.raises(restore.RestoreSafetyError, match="^bounded_command_failed$"):
        restore.run(["synthetic-tool", "DO-NOT-STORE-ARGUMENT"])
    data = diagnostic.read_bytes()
    assert len(data) == restore.DIAGNOSTIC_LIMIT and data.startswith(marker)
    assert b"DO-NOT-STORE" not in data
    assert capsys.readouterr().out == ""
    if os.name == "posix":
        assert diagnostic.stat().st_mode & 0o077 == 0
    restore.save_private_stderr(b"not appended beyond cap")
    assert diagnostic.stat().st_size == restore.DIAGNOSTIC_LIMIT


@pytest.mark.parametrize(
    "response", [b"<html>502</html>\n502", b"\n502", b"[]\n503", b"{}\nnot-status", b"{}\n999"]
)
def test_transient_proxy_or_invalid_health_response_retries_safely(tmp_path, monkeypatch, response):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    verifier = restore.Verifier(receipt, path)
    monkeypatch.setattr(restore, "run", lambda *args, **kwargs: response)
    with pytest.raises(restore.RestoreSafetyError, match="^health_response_not_ready$"):
        verifier.https("ready")


def test_readonly_clone_expected_readiness_json_is_preserved(tmp_path, monkeypatch):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    verifier = restore.Verifier(receipt, path)
    body = {"status": "unavailable", "checks": {"database": True, "redis": True, "storage": False}}
    monkeypatch.setattr(
        restore, "run", lambda *args, **kwargs: json.dumps(body).encode() + b"\n503"
    )
    assert verifier.https("ready") == (503, body)


def test_cloned_credentials_revoked_only_in_owned_target_after_exact_manifest(
    tmp_path, monkeypatch
):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    ownership = {"oid": "424242", "marker": restore.PURPOSE + ":" + receipt["run_id"]}
    receipt.update(restored_manifest_verified=True, database_ownership=ownership)
    verifier = restore.Verifier(receipt, path)
    monkeypatch.setattr(verifier, "save", lambda: None)
    monkeypatch.setattr(verifier, "database_marker", lambda: ownership)
    calls = []

    def sql(database, query):
        calls.append((database, query))
        return b'{"auth_sessions_revoked":2,"service_tokens_revoked":3}\n{"auth_sessions_remaining":0,"service_tokens_remaining":0}\n'

    monkeypatch.setattr(verifier, "sql", sql)
    verifier.revoke_cloned_credentials()
    assert len(calls) == 1 and calls[0][0] == restore.TARGET_DATABASE
    query = calls[0][1]
    assert "current_database()" in query and "424242" in query and ownership["marker"] in query
    assert "UPDATE public.auth_sessions" in query and "UPDATE public.service_tokens" in query
    assert "local_credentials" not in query and "UPDATE public.users" not in query
    assert "revoked_at IS NULL" in query
    assert receipt["cloned_credentials_revoked"] is True
    assert receipt["credential_revocations"] == {"auth_sessions": 2, "service_tokens": 3}


@pytest.mark.parametrize("verified,foreign", [(False, False), (True, True)])
def test_credential_revoke_refuses_unverified_manifest_or_foreign_database(
    tmp_path, monkeypatch, verified, foreign
):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    ownership = {"oid": "424242", "marker": restore.PURPOSE + ":" + receipt["run_id"]}
    receipt.update(restored_manifest_verified=verified, database_ownership=ownership)
    verifier = restore.Verifier(receipt, path)
    monkeypatch.setattr(verifier, "save", lambda: None)
    monkeypatch.setattr(
        verifier, "database_marker", lambda: {**ownership, "oid": "9"} if foreign else ownership
    )
    calls = []
    monkeypatch.setattr(verifier, "sql", lambda *args: calls.append(args))
    with pytest.raises(restore.RestoreSafetyError):
        verifier.revoke_cloned_credentials()
    assert calls == [] and receipt.get("cloned_credentials_revoked") is not True


def test_clone_api_cannot_launch_before_credentials_are_revoked(tmp_path, monkeypatch):
    receipt, path = sample_receipt(tmp_path, monkeypatch)
    verifier = restore.Verifier(receipt, path)
    calls = []
    monkeypatch.setattr(verifier, "docker", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(restore.RestoreSafetyError, match="cloned_credentials_not_revoked"):
        verifier.launch({})
    assert calls == []


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX descriptor/no-follow enforcement is exercised on Linux"
)
def test_copy_is_exact_excludes_unreferenced_files_and_never_modifies_original(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "copies"
    source.mkdir()
    destination.mkdir()
    data = b"Synthetic immutable original\x00\xff"
    (source / "allowed").write_bytes(data)
    (source / "not-referenced").write_bytes(b"Do not copy")
    original_stat = (source / "allowed").stat()
    result = restore.copy_references(
        source,
        destination,
        [{"storage_key": "allowed", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
        app_uid=None,
    )
    assert result == {"files": 1, "bytes": len(data)}
    assert (destination / "allowed").read_bytes() == data
    assert not (destination / "not-referenced").exists()
    assert (source / "allowed").stat().st_mtime_ns == original_stat.st_mtime_ns
    assert (source / "allowed").read_bytes() == data


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX symlink/no-follow enforcement is exercised on Linux"
)
def test_symlink_original_and_unowned_cleanup_directory_refused(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "copies"
    source.mkdir()
    destination.mkdir()
    secret = tmp_path / "outside"
    secret.write_bytes(b"not copied")
    (source / "linked").symlink_to(secret)
    with pytest.raises(OSError):
        restore.copy_references(
            source,
            destination,
            [{"storage_key": "linked", "size": 10, "sha256": "a" * 64}],
            app_uid=None,
        )
    assert not (destination / "linked").exists()
    run_id = str(uuid4())
    own = restore.owned_directory(tmp_path, run_id)
    own.mkdir(parents=True)
    (own / restore.MARKER).write_text(str(uuid4()), encoding="utf-8")
    with pytest.raises(restore.RestoreSafetyError, match="cleanup_owner_mismatch"):
        restore.remove_owned_directory(tmp_path, run_id)
    assert own.exists() and secret.read_bytes() == b"not copied"
