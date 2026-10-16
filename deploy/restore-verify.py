#!/usr/bin/env python3
"""Guarded backup bundles and same-VM disposable restore/read proof.

Run as root on the ACTI VM. ``prepare --expected-sha SHA`` leaves a protected
receipt for the coordinator's HTTPS browser smoke. ``cleanup --receipt PATH
--run-id UUID`` removes only resources bearing that receipt's ownership marker.
No worker, scheduler, source mutation, production stop, or schema downgrade.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
from uuid import UUID, uuid4

POSTGRES_ROOT = Path("/srv/acti/postgresql")
DOCUMENTS_ROOT = Path("/srv/acti/documents")
STATE_ROOT = Path("/var/lib/acti-v3/restore-verification")
TARGET_DATABASE = "acti_restore_test"
SOURCE_DATABASE = "acti"
LABEL = "org.acti.restore-verification.run"
PURPOSE = "same-vm-readonly-restore"
MARKER = ".acti-restore-owner"
PORT = 8444
ORIGIN = "https://localhost:9444"
APP_UID = 10001
DIAGNOSTIC_FILE = None
DIAGNOSTIC_LIMIT = 1024**2
BUNDLE_MARKER = ".acti-backup-owner"
BUNDLE_FORMAT = "acti-backup-v1"
DATABASE_SCHEMAS = {"public", "keycloak"}


class RestoreSafetyError(RuntimeError):
    """Messages are deliberately safe for an operator log."""


def require(condition, code):
    if not condition:
        raise RestoreSafetyError(code)


def canonical_uuid(value):
    require(isinstance(value, str), "invalid_run_id")
    try:
        result = str(UUID(value))
    except ValueError:
        raise RestoreSafetyError("invalid_run_id") from None
    require(result == value, "noncanonical_run_id")
    return result


def identifier(value):
    require(isinstance(value, str) and "\x00" not in value, "invalid_sql_identifier")
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    require(isinstance(value, str) and "\x00" not in value, "invalid_sql_literal")
    return "'" + value.replace("'", "''") + "'"


def relation_identity(value):
    """Keep legacy public manifest keys; never parse a dotted name as SQL."""
    if isinstance(value, str):
        schema, name = "public", value
    else:
        require(
            isinstance(value, (list, tuple)) and len(value) == 2,
            "invalid_manifest_relation",
        )
        schema, name = value
    require(isinstance(schema, str) and schema in DATABASE_SCHEMAS, "foreign_schema")
    require(isinstance(name, str) and bool(name), "invalid_manifest_relation")
    qualified = identifier(schema) + "." + identifier(name)
    key = name if schema == "public" else schema + "." + name
    return key, qualified


def clone_database_url(value):
    require(
        isinstance(value, str) and not any(c in value for c in "\r\n\x00"),
        "invalid_database_url",
    )
    parsed = urlsplit(value)
    require(
        parsed.scheme in {"postgresql", "postgresql+psycopg"},
        "unsupported_database_driver",
    )
    require(
        parsed.hostname and parsed.username and parsed.path == "/" + SOURCE_DATABASE,
        "unexpected_source_database",
    )
    require(not parsed.fragment, "database_url_fragment_forbidden")
    allowed = {"sslmode", "connect_timeout", "application_name", "client_encoding"}
    require(
        all(key in allowed for key, _ in parse_qsl(parsed.query)),
        "database_url_override_forbidden",
    )
    return urlunsplit(parsed._replace(path="/" + TARGET_DATABASE))


def parse_env(text):
    """Parse literal shell-style assignments without evaluating shell expansions."""
    result = {}
    for line in text.splitlines():
        try:
            parts = shlex.split(line, comments=True, posix=True)
        except ValueError:
            raise RestoreSafetyError("invalid_runtime_env") from None
        if not parts:
            continue
        if parts[0] == "export":
            parts = parts[1:]
        require(len(parts) == 1 and "=" in parts[0], "nonliteral_runtime_env")
        key, value = parts[0].split("=", 1)
        require(
            re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is not None, "invalid_runtime_key"
        )
        require(
            key not in result and not any(c in value for c in "\r\n\x00`$"),
            "unsafe_runtime_assignment",
        )
        result[key] = value
    return result


def no_symlinks(path):
    path = Path(path).absolute()
    require(path == path.resolve(strict=True), "symlink_path_forbidden")
    return path


def protected(path, *, directory=False, shared_read=False):
    path = no_symlinks(path)
    info = path.stat()
    require(
        info.st_uid == 0 and not info.st_mode & (0o022 if shared_read else 0o077),
        "private_path_permissions",
    )
    require(
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
        "unexpected_private_path_type",
    )
    return path


def private_root(path):
    path = Path(path).absolute()
    if not path.exists():
        parent = path.parent
        require(parent == parent.resolve(strict=True), "symlink_parent_forbidden")
        path.mkdir(mode=0o700, parents=False)
    return protected(path, directory=True)


def verify_expected_mounts(expected):
    require(
        set(expected) == {str(POSTGRES_ROOT), str(DOCUMENTS_ROOT)},
        "incomplete_mount_contract",
    )
    for path, uuid in expected.items():
        require(bool(uuid), "empty_mount_uuid")
        actual = (
            run(["findmnt", "-rn", "-o", "UUID", "--mountpoint", path], timeout=10)
            .decode()
            .strip()
        )
        require(actual == uuid, "mount_uuid_mismatch")


def write_json(path, value):
    temporary = path.with_suffix(".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def save_private_stderr(data):
    """One protected, capped file; never persist stdout, command arguments or env."""
    if DIAGNOSTIC_FILE is None or not data:
        return
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    fd = os.open(
        DIAGNOSTIC_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(fd, "ab") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode), "diagnostic_not_regular_file")
        if os.name == "posix":
            require(
                info.st_uid == 0 and not info.st_mode & 0o077, "diagnostic_permissions"
            )
        stream.write(data[: max(0, DIAGNOSTIC_LIMIT - info.st_size)])
        stream.flush()
        os.fsync(stream.fileno())


def run(command, *, data=None, stdin=None, stdout=None, timeout=120, env=None):
    """Never return subprocess diagnostics containing URLs, SQL or environment values."""
    try:
        result = subprocess.run(
            command,
            input=data,
            stdin=stdin,
            stdout=stdout or subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        save_private_stderr(getattr(error, "stderr", None))
        raise RestoreSafetyError("bounded_command_unavailable_or_timeout") from None
    if result.returncode != 0:
        save_private_stderr(result.stderr)
    require(result.returncode == 0, "bounded_command_failed")
    return result.stdout or b""


def manifest_sql(tables, sequences):
    commands = [
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;",
        "SET LOCAL TIME ZONE 'UTC';",
        "SET LOCAL extra_float_digits=3;",
    ]
    for table in tables:
        key, name = relation_identity(table)
        commands.append(
            "SELECT json_build_object('kind','table','name',"
            + literal(key)
            + ",'count',count(*),'digest',encode(sha256(convert_to(COALESCE(string_agg(h,'' ORDER BY h COLLATE \"C\"),''),'UTF8')),'hex')) "
            + "FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS h FROM "
            + name
            + " t) hashed;"
        )
    for sequence in sequences:
        key, name = relation_identity(sequence)
        commands.append(
            "SELECT json_build_object('kind','sequence','name',"
            + literal(key)
            + ",'last_value',last_value::text,'is_called',is_called) FROM "
            + name
            + ";"
        )
    commands.append("COMMIT;")
    return "\n".join(commands)


def parse_manifest(output):
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    result = {"tables": {}, "sequences": {}}
    for row in rows:
        kind = row.pop("kind")
        key = (
            "tables" if kind == "table" else "sequences" if kind == "sequence" else None
        )
        require(key is not None, "unknown_manifest_kind")
        name = row.pop("name")
        require(name not in result[key], "duplicate_manifest_resource")
        result[key][name] = row
    require(
        "alembic_version" in result["tables"] and "document_files" in result["tables"],
        "not_current_acti_schema",
    )
    return result


def assert_same_manifest(expected, actual):
    require(expected == actual, "table_or_sequence_manifest_mismatch")


def constraint_tokens(expression):
    """Token positions; quoted SQL text and comments are opaque to normalization."""
    tokens, cursor = [], 0
    while cursor < len(expression):
        start = cursor
        char = expression[cursor]
        if char.isspace():
            cursor += 1
            continue
        kind = "symbol"
        if expression.startswith("--", cursor):
            end = expression.find("\n", cursor + 2)
            cursor = len(expression) if end == -1 else end
            kind = "opaque"
        elif expression.startswith("/*", cursor):
            depth, cursor = 1, cursor + 2
            while cursor < len(expression) and depth:
                if expression.startswith("/*", cursor):
                    depth, cursor = depth + 1, cursor + 2
                elif expression.startswith("*/", cursor):
                    depth, cursor = depth - 1, cursor + 2
                else:
                    cursor += 1
            kind = "opaque"
        elif char in "'\"" or (
            char in "Ee" and expression[cursor : cursor + 2].lower() == "e'"
        ):
            escaped = char in "Ee"
            if escaped:
                cursor += 1
            quote = expression[cursor]
            cursor += 1
            while cursor < len(expression):
                if escaped and expression[cursor] == "\\":
                    cursor += 2
                elif expression[cursor] == quote:
                    cursor += 1
                    if cursor < len(expression) and expression[cursor] == quote:
                        cursor += 1
                    else:
                        break
                else:
                    cursor += 1
            kind = "literal" if quote == "'" and not escaped else "opaque"
        elif char == "$" and (
            match := re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", expression[cursor:])
        ):
            delimiter = match.group()
            end = expression.find(delimiter, cursor + len(delimiter))
            cursor = len(expression) if end == -1 else end + len(delimiter)
            kind = "opaque"
        elif match := re.match(r"[A-Za-z_][A-Za-z0-9_$]*", expression[cursor:]):
            cursor += len(match.group())
            kind = "word"
        elif expression.startswith("::", cursor):
            cursor += 2
        else:
            cursor += 1
        tokens.append((kind, expression[start:cursor], start, cursor))
    return tokens


def canonical_constraint(expression):
    """Canonicalize only PostgreSQL's proven varchar-literal-array cast rewrite.

    Dump/restore may distribute a text[] cast over each varchar literal. Values,
    order, operators and all other casts remain significant. This is intentionally
    a closed grammar, rather than general SQL expression simplification.
    """
    tokens = constraint_tokens(expression)
    replacements, index = [], 0
    while index < len(tokens):
        if [token[1] for token in tokens[index : index + 3]] != ["(", "ARRAY", "["]:
            index += 1
            continue
        cursor, literals, valid = index + 3, [], True
        while cursor + 4 < len(tokens):
            if tokens[cursor][0] != "literal" or [
                token[1] for token in tokens[cursor + 1 : cursor + 4]
            ] != ["::", "character", "varying"]:
                valid = False
                break
            literals.append(tokens[cursor][1])
            cursor += 4
            if tokens[cursor][1] != ",":
                break
            cursor += 1
        if (
            valid
            and literals
            and [token[1] for token in tokens[cursor : cursor + 6]]
            == [
                "]",
                ")",
                "::",
                "text",
                "[",
                "]",
            ]
        ):
            replacement = (
                "ARRAY["
                + ", ".join(
                    "(" + literal + "::character varying)::text" for literal in literals
                )
                + "]"
            )
            replacements.append((tokens[index][2], tokens[cursor + 5][3], replacement))
            index = cursor + 6
        else:
            index += 1
    for start, end, replacement in reversed(replacements):
        expression = expression[:start] + replacement + expression[end:]
    return expression


def verify_toc(data):
    require(
        b"Format: CUSTOM" in data and b"TABLE DATA public document_files" in data,
        "invalid_or_foreign_dump_toc",
    )


def storage_reference(row):
    key, size, digest = row["storage_key"], row["size"], row["sha256"]
    require(
        isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", key),
        "unsafe_storage_key",
    )
    require(type(size) is int and size >= 0, "invalid_file_size")
    require(
        isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
        "invalid_file_digest",
    )
    return key, size, digest


def validate_bundle_metadata(metadata, expected_sha):
    require(isinstance(metadata, dict), "invalid_bundle_metadata")
    require(metadata.get("format") == BUNDLE_FORMAT, "foreign_bundle_format")
    canonical_uuid(metadata.get("run_id"))
    require(metadata.get("source_sha") == expected_sha, "bundle_source_sha_mismatch")
    require(
        re.fullmatch(r"[0-9a-f]{64}", metadata.get("dump_sha256", "")),
        "invalid_dump_digest",
    )
    manifest = metadata.get("manifest")
    require(
        isinstance(manifest, dict) and set(manifest) == {"tables", "sequences"},
        "invalid_bundle_manifest",
    )
    require(
        all(isinstance(manifest[key], dict) for key in manifest),
        "invalid_bundle_manifest",
    )
    require(
        {"alembic_version", "document_files"} <= set(manifest["tables"]),
        "not_current_acti_schema",
    )
    for name, row in manifest["tables"].items():
        identifier(name)
        require(
            isinstance(row, dict)
            and set(row) == {"count", "digest"}
            and type(row["count"]) is int
            and row["count"] >= 0
            and isinstance(row["digest"], str)
            and re.fullmatch(r"[0-9a-f]{64}", row["digest"]),
            "invalid_bundle_table",
        )
    for name, row in manifest["sequences"].items():
        identifier(name)
        require(
            isinstance(row, dict)
            and set(row) == {"last_value", "is_called"}
            and isinstance(row["last_value"], str)
            and re.fullmatch(r"-?[0-9]+", row["last_value"])
            and type(row["is_called"]) is bool,
            "invalid_bundle_sequence",
        )
    schema = metadata.get("schema")
    require(
        isinstance(schema, dict)
        and set(schema) == {"revision", "digest"}
        and isinstance(schema["revision"], list)
        and bool(schema["revision"])
        and all(
            isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_]+", value)
            for value in schema["revision"]
        )
        and isinstance(schema["digest"], str)
        and re.fullmatch(r"[0-9a-f]{64}", schema["digest"]),
        "invalid_bundle_schema",
    )
    references = metadata.get("references")
    require(isinstance(references, list), "invalid_bundle_references")
    require(
        all(
            isinstance(row, dict) and set(row) == {"storage_key", "size", "sha256"}
            for row in references
        ),
        "invalid_bundle_references",
    )
    checked = [storage_reference(row) for row in references]
    require(
        len({row[0] for row in checked}) == len(checked), "duplicate_storage_reference"
    )
    require(
        metadata.get("copies")
        == {"files": len(checked), "bytes": sum(row[1] for row in checked)},
        "bundle_totals_mismatch",
    )
    require(
        manifest["tables"]["document_files"]["count"] == len(checked),
        "bundle_reference_count_mismatch",
    )
    return metadata


def verify_reference_files(source, references):
    """Verify a closed set of files without copying, extraction or source writes."""
    source = protected(source, directory=True)
    checked = [storage_reference(row) for row in references]
    require(
        {path.name for path in source.iterdir()} == {row[0] for row in checked},
        "bundle_object_set_mismatch",
    )
    for key, size, digest in checked:
        path = protected(source / key, shared_read=True)
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(
                stat.S_ISREG(before.st_mode) and before.st_size == size,
                "bundle_object_size_mismatch",
            )
            require(
                hashlib.file_digest(stream, "sha256").hexdigest() == digest,
                "bundle_object_digest_mismatch",
            )
            after = os.fstat(stream.fileno())
            require(
                (before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_ino, after.st_size, after.st_mtime_ns),
                "bundle_object_changed",
            )


def load_bundle(source, expected_sha):
    source = Path(source)
    require(source.is_absolute(), "relative_bundle_source")
    source = protected(source, directory=True)
    allowed = {"bundle.json", BUNDLE_MARKER, "snapshot.dump", "objects"}
    names = {path.name for path in source.iterdir()}
    require(
        names in (allowed, allowed | {"recovery"}),
        "bundle_layout_mismatch",
    )
    if "recovery" in names:
        recovery = protected(source / "recovery", directory=True)
        for path in recovery.rglob("*"):
            protected(path, directory=path.is_dir())
    metadata_path = protected(source / "bundle.json")
    require(metadata_path.stat().st_size <= 64 * 1024**2, "bundle_metadata_too_large")
    metadata = validate_bundle_metadata(
        json.loads(metadata_path.read_text(encoding="utf-8")), expected_sha
    )
    require(
        protected(source / BUNDLE_MARKER).read_text(encoding="utf-8")
        == metadata["run_id"],
        "bundle_owner_mismatch",
    )
    with protected(source / "snapshot.dump").open("rb") as stream:
        require(
            hashlib.file_digest(stream, "sha256").hexdigest()
            == metadata["dump_sha256"],
            "bundle_dump_digest_mismatch",
        )
    verify_reference_files(source / "objects", metadata["references"])
    return metadata


def copy_references(source, destination, references, *, app_uid=APP_UID):
    """Open originals read-only/no-follow, create only new copies, verify bytes then fsync."""
    source, destination = no_symlinks(source), no_symlinks(destination)
    require(
        source != destination
        and source not in destination.parents
        and destination not in source.parents,
        "copy_roots_overlap",
    )
    checked = [storage_reference(row) for row in references]
    require(
        len({item[0] for item in checked}) == len(checked),
        "duplicate_storage_reference",
    )
    total = sum(item[1] for item in checked)
    require(
        shutil.disk_usage(destination).free >= total + max(total // 10, 64 * 1024**2),
        "insufficient_copy_space",
    )
    source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    destination_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for key, size, expected in checked:
            original_fd = os.open(
                key, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=source_fd
            )
            with os.fdopen(original_fd, "rb") as original:
                before = os.fstat(original.fileno())
                require(
                    stat.S_ISREG(before.st_mode) and before.st_size == size,
                    "original_type_or_size_mismatch",
                )
                copied_fd = os.open(
                    key,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o440,
                    dir_fd=destination_fd,
                )
                digest, copied = hashlib.sha256(), 0
                with os.fdopen(copied_fd, "wb") as target:
                    while chunk := original.read(1024**2):
                        copied += len(chunk)
                        require(copied <= size, "original_changed_during_copy")
                        target.write(chunk)
                        digest.update(chunk)
                    require(
                        copied == size and digest.hexdigest() == expected,
                        "original_digest_mismatch",
                    )
                    after = os.fstat(original.fileno())
                    require(
                        (before.st_ino, before.st_size, before.st_mtime_ns)
                        == (after.st_ino, after.st_size, after.st_mtime_ns),
                        "original_changed_during_copy",
                    )
                    target.flush()
                    os.fsync(target.fileno())
                    if app_uid is not None:
                        os.fchown(target.fileno(), app_uid, app_uid)
            # Independently verify the stored copy, not only the stream read from its source.
            with (destination / key).open("rb") as copied_file:
                require(
                    hashlib.file_digest(copied_file, "sha256").hexdigest() == expected,
                    "copied_digest_mismatch",
                )
        os.fsync(destination_fd)
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    return {"files": len(checked), "bytes": total}


def run_names(run_id):
    compact = UUID(canonical_uuid(run_id)).hex
    return {
        kind: "acti-restore-" + compact + "-" + kind
        for kind in ("api", "redis", "frontend", "network", "edge")
    }


def owned_directory(root, run_id):
    return Path(root) / "restore-verification" / canonical_uuid(run_id)


def validate_receipt(receipt, receipt_path):
    run_id = canonical_uuid(receipt["run_id"])
    require(
        receipt.get("purpose") == PURPOSE
        and receipt.get("target_database") == TARGET_DATABASE,
        "foreign_restore_receipt",
    )
    state = Path(receipt["state_root"]) / run_id
    require(
        Path(receipt_path).absolute() == state / "receipt.json", "receipt_path_mismatch"
    )
    expected_names = run_names(run_id)
    legacy_names = {
        key: value for key, value in expected_names.items() if key != "edge"
    }
    require(
        receipt["names"] in (expected_names, legacy_names), "resource_name_mismatch"
    )
    require(
        Path(receipt["pg_copy"]) == owned_directory(POSTGRES_ROOT, run_id),
        "pg_copy_path_mismatch",
    )
    require(
        Path(receipt["documents_copy"]) == owned_directory(DOCUMENTS_ROOT, run_id),
        "documents_copy_path_mismatch",
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", receipt.get("source_sha", "")),
        "invalid_receipt_source",
    )
    require(Path(receipt["state_root"]).is_absolute(), "relative_state_root")
    require(
        set(receipt.get("mounts", {})) == {str(POSTGRES_ROOT), str(DOCUMENTS_ROOT)},
        "incomplete_mount_contract",
    )
    return run_id


def remove_owned_directory(root, run_id):
    directory = owned_directory(root, run_id)
    if not directory.exists() and not directory.is_symlink():
        return
    require(
        directory == directory.resolve(strict=True)
        and directory.parent
        == (Path(root) / "restore-verification").resolve(strict=True),
        "cleanup_path_escape",
    )
    marker = directory / MARKER
    require(marker.is_file() and not marker.is_symlink(), "cleanup_owner_mismatch")
    require(
        marker.read_text(encoding="utf-8") == canonical_uuid(run_id),
        "cleanup_owner_mismatch",
    )
    shutil.rmtree(directory)


class Verifier:
    def __init__(self, receipt, receipt_path):
        self.receipt, self.receipt_path = receipt, Path(receipt_path)
        validate_receipt(receipt, self.receipt_path)

    def save(self):
        write_json(self.receipt_path, self.receipt)

    def stage(self, name):
        require(re.fullmatch(r"[a-z_.]+", name), "invalid_static_stage")
        self.receipt["stage"] = name
        self.save()

    def docker(self, *arguments, **kwargs):
        return run(["docker", *arguments], **kwargs)

    def sql(self, database, query):
        require(
            database in {SOURCE_DATABASE, TARGET_DATABASE, "postgres"},
            "unexpected_database",
        )
        return self.docker(
            "exec",
            "-i",
            self.receipt["postgres_container"],
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            database,
            data=query.encode(),
            timeout=240,
        )

    def database_marker(self):
        rows = self.sql(
            "postgres",
            "SELECT json_build_object('oid',oid::text,'marker',shobj_description(oid,'pg_database')) FROM pg_database WHERE datname="
            + literal(TARGET_DATABASE)
            + ";",
        )
        return json.loads(rows) if rows.strip() else None

    def manifest(self, database):
        catalog = json.loads(
            self.sql(
                database,
                "SELECT json_build_object('tables',COALESCE(json_agg(json_build_array(n.nspname,c.relname) ORDER BY n.nspname,c.relname) FILTER (WHERE relkind IN ('r','p','m')),'[]'::json),'sequences',COALESCE(json_agg(json_build_array(n.nspname,c.relname) ORDER BY n.nspname,c.relname) FILTER (WHERE relkind='S'),'[]'::json)) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','keycloak');",
            )
        )
        keys = {
            kind: [relation_identity(value)[0] for value in catalog[kind]]
            for kind in ("tables", "sequences")
        }
        require(
            all(len(values) == len(set(values)) for values in keys.values()),
            "ambiguous_manifest_relation",
        )
        result = parse_manifest(
            self.sql(database, manifest_sql(catalog["tables"], catalog["sequences"]))
        )
        require(
            set(result["tables"]) == set(keys["tables"])
            and set(result["sequences"]) == set(keys["sequences"]),
            "incomplete_manifest",
        )
        return result

    def references(self, database):
        return [
            json.loads(line)
            for line in self.sql(
                database,
                "SELECT json_build_object('storage_key',storage_key,'size',size,'sha256',sha256) FROM public.document_files ORDER BY storage_key;",
            ).splitlines()
        ]

    def schema(self, database):
        # Semantic ACTI/IdP fingerprint: no owner/OID/physical-storage identities.
        # Public-only bundles retain the previous definition and digest exactly.
        # pg_get_* renders against a fixed search path, independent of connection defaults.
        query = """
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL search_path = public, pg_catalog;
SELECT json_build_object(
 'revision', (SELECT json_agg(version_num ORDER BY version_num) FROM public.alembic_version),
 'relations', (SELECT json_agg(json_build_array(CASE WHEN n.nspname='public' THEN c.relname::text ELSE n.nspname||'.'||c.relname END,c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,c.relreplident,c.reloptions) ORDER BY n.nspname,c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','keycloak') AND c.relkind IN ('r','p','v','m','S')),
 'columns', (SELECT json_agg(json_build_array(CASE WHEN n.nspname='public' THEN c.relname::text ELSE n.nspname||'.'||c.relname END,a.attname,a.attnum,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid)) ORDER BY n.nspname,c.relname,a.attnum) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname IN ('public','keycloak') AND a.attnum>0 AND NOT a.attisdropped AND c.relkind IN ('r','p','v','m')),
 'constraints', (SELECT json_agg(json_build_array(CASE WHEN n.nspname='public' THEN c.relname::text ELSE n.nspname||'.'||c.relname END,k.conname,pg_get_constraintdef(k.oid,false),k.convalidated,k.condeferrable,k.condeferred) ORDER BY n.nspname,c.relname,k.conname) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','keycloak')),
 'indexes', (SELECT json_agg(pg_get_indexdef(i.indexrelid) ORDER BY n.nspname,c.relname,ic.relname) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_class ic ON ic.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','keycloak')),
 'triggers', (SELECT json_agg(json_build_array(CASE WHEN n.nspname='public' THEN c.relname::text ELSE n.nspname||'.'||c.relname END,t.tgname,pg_get_triggerdef(t.oid,false),t.tgenabled) ORDER BY n.nspname,c.relname,t.tgname) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','keycloak') AND NOT t.tgisinternal),
 'functions', (SELECT json_agg(pg_get_functiondef(p.oid) ORDER BY n.nspname,p.proname,pg_get_function_identity_arguments(p.oid)) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('public','keycloak') AND p.prokind IN ('f','p')),
 'views', (SELECT json_agg(json_build_array(CASE WHEN n.nspname='public' THEN c.relname::text ELSE n.nspname||'.'||c.relname END,pg_get_viewdef(c.oid,false)) ORDER BY n.nspname,c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','keycloak') AND c.relkind IN ('v','m')),
 'enums', (SELECT json_agg(json_build_array(CASE WHEN n.nspname='public' THEN t.typname::text ELSE n.nspname||'.'||t.typname END,e.enumlabel,e.enumsortorder) ORDER BY n.nspname,t.typname,e.enumsortorder) FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname IN ('public','keycloak')),
 'sequences', (SELECT json_agg(json_build_array(CASE WHEN sequence_schema='public' THEN sequence_name::text ELSE sequence_schema||'.'||sequence_name END,data_type,start_value,minimum_value,maximum_value,increment,cycle_option) ORDER BY sequence_schema,sequence_name) FROM information_schema.sequences WHERE sequence_schema IN ('public','keycloak')),
 'policies', (SELECT json_agg(json_build_array(CASE WHEN schemaname='public' THEN tablename::text ELSE schemaname||'.'||tablename END,policyname,permissive,roles,cmd,qual,with_check) ORDER BY schemaname,tablename,policyname) FROM pg_policies WHERE schemaname IN ('public','keycloak'))
);
COMMIT;
"""
        definition = json.loads(self.sql(database, query))
        for constraint in definition.get("constraints") or []:
            constraint[2] = canonical_constraint(constraint[2])
        return {
            "revision": definition["revision"],
            "digest": hashlib.sha256(
                json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }

    def stable_dump(self):
        target = Path(self.receipt["pg_copy"]) / "snapshot.dump"
        for attempt in range(3):
            self.stage("dump.before_manifest")
            before = self.manifest(SOURCE_DATABASE)
            schema_before = self.schema(SOURCE_DATABASE)
            partial = target.with_suffix(".partial")
            fd = os.open(
                partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            try:
                with os.fdopen(fd, "wb") as output:
                    self.stage("dump.pg_dump")
                    self.docker(
                        "exec",
                        self.receipt["postgres_container"],
                        "pg_dump",
                        "-U",
                        "postgres",
                        "-Fc",
                        SOURCE_DATABASE,
                        stdout=output,
                        timeout=600,
                    )
                    output.flush()
                    os.fsync(output.fileno())
                self.stage("dump.after_manifest")
                schema_after = self.schema(SOURCE_DATABASE)
                after = self.manifest(SOURCE_DATABASE)
                if before == after and schema_before == schema_after:
                    with partial.open("rb") as source:
                        self.stage("dump.toc")
                        toc = self.docker(
                            "exec",
                            "-i",
                            self.receipt["postgres_container"],
                            "pg_restore",
                            "--list",
                            stdin=source,
                        )
                    verify_toc(toc)
                    os.replace(partial, target)
                    with target.open("rb") as source:
                        self.receipt["dump_sha256"] = hashlib.file_digest(
                            source, "sha256"
                        ).hexdigest()
                    self.receipt["manifest"] = before
                    self.receipt["schema"] = schema_before
                    self.receipt["stable_dump_attempts"] = attempt + 1
                    self.save()
                    return target
            finally:
                partial.unlink(missing_ok=True)
            time.sleep(0.5)
        raise RestoreSafetyError("source_changed_during_all_dump_attempts")

    def export_bundle(self):
        dump = self.stable_dump()
        bundle = Path(self.receipt["documents_copy"]) / "bundle"
        bundle.mkdir(mode=0o700)
        objects = bundle / "objects"
        objects.mkdir(mode=0o700)
        references = self.references(SOURCE_DATABASE)
        required = (
            sum(storage_reference(row)[1] for row in references) + dump.stat().st_size
        )
        require(
            shutil.disk_usage(bundle).free
            >= required + max(required // 10, 64 * 1024**2),
            "insufficient_bundle_space",
        )
        self.stage("export.copy_originals")
        copies = copy_references(
            DOCUMENTS_ROOT / "objects", objects, references, app_uid=None
        )
        # Cover the interval used to enumerate and copy originals, including sequence state.
        assert_same_manifest(self.receipt["manifest"], self.manifest(SOURCE_DATABASE))
        require(
            self.receipt["schema"] == self.schema(SOURCE_DATABASE),
            "source_schema_changed",
        )
        require(
            references == self.references(SOURCE_DATABASE), "source_references_changed"
        )
        with dump.open("rb") as source:
            fd = os.open(
                bundle / "snapshot.dump",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(fd, "wb") as target:
                shutil.copyfileobj(source, target, length=1024**2)
                target.flush()
                os.fsync(target.fileno())
        metadata = {
            "format": BUNDLE_FORMAT,
            "run_id": self.receipt["run_id"],
            "source_sha": self.receipt["source_sha"],
            "dump_sha256": self.receipt["dump_sha256"],
            "manifest": self.receipt["manifest"],
            "schema": self.receipt["schema"],
            "references": references,
            "copies": copies,
        }
        validate_bundle_metadata(metadata, self.receipt["source_sha"])
        marker_fd = os.open(
            bundle / BUNDLE_MARKER,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(marker_fd, "w", encoding="utf-8") as stream:
            stream.write(self.receipt["run_id"])
            stream.flush()
            os.fsync(stream.fileno())
        # Metadata is the final publication marker. No partial export can be loaded.
        write_json(bundle / "bundle.json", metadata)
        load_bundle(bundle, self.receipt["source_sha"])
        directory_fd = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self.receipt.update(status="exported", bundle_source=str(bundle), copies=copies)
        self.save()

    def restore_bundle(self, source):
        bundle = load_bundle(source, self.receipt["source_sha"])
        self.receipt.update(
            manifest=bundle["manifest"],
            schema=bundle["schema"],
            dump_sha256=bundle["dump_sha256"],
            external_bundle=True,
            external_bundle_run_id=bundle["run_id"],
        )
        self.save()
        dump = Path(source) / "snapshot.dump"
        with dump.open("rb") as stream:
            verify_toc(
                self.docker(
                    "exec",
                    "-i",
                    self.receipt["postgres_container"],
                    "pg_restore",
                    "--list",
                    stdin=stream,
                )
            )
        self.restore(
            dump,
            originals_source=Path(source) / "objects",
            expected_references=bundle["references"],
        )

    def restore(self, dump, *, originals_source=None, expected_references=None):
        self.stage("restore.database_create")
        require(self.database_marker() is None, "restore_database_already_exists")
        self.receipt["database_creation_started"] = True
        self.save()
        self.sql(
            "postgres",
            "CREATE DATABASE "
            + identifier(TARGET_DATABASE)
            + " TEMPLATE template0 CONNECTION LIMIT 0;",
        )
        marker = PURPOSE + ":" + self.receipt["run_id"]
        self.sql(
            "postgres",
            "COMMENT ON DATABASE "
            + identifier(TARGET_DATABASE)
            + " IS "
            + literal(marker)
            + ";",
        )
        ownership = self.database_marker()
        require(
            ownership and ownership["marker"] == marker, "database_ownership_not_proven"
        )
        self.receipt["database_ownership"] = ownership
        self.save()
        self.restrict_clone_access()
        self.stage("restore.pg_restore")
        with dump.open("rb") as source:
            self.docker(
                "exec",
                "-i",
                self.receipt["postgres_container"],
                "pg_restore",
                "-U",
                "postgres",
                "--no-owner",
                "--exit-on-error",
                "-d",
                TARGET_DATABASE,
                stdin=source,
                timeout=600,
            )
        self.restrict_clone_access()
        self.stage("restore.manifest_compare")
        assert_same_manifest(self.receipt["manifest"], self.manifest(TARGET_DATABASE))
        if "schema" in self.receipt:
            require(
                self.receipt["schema"] == self.schema(TARGET_DATABASE),
                "restored_schema_mismatch",
            )
        references = self.references(TARGET_DATABASE)
        if expected_references is not None:
            require(references == expected_references, "restored_references_mismatch")
        require(
            not self.receipt.get("external_bundle") or originals_source is not None,
            "external_originals_required",
        )
        objects = Path(self.receipt["documents_copy"]) / "objects"
        objects.mkdir(mode=0o750)
        self.stage("restore.copy_originals")
        self.receipt["copies"] = copy_references(
            originals_source
            if originals_source is not None
            else DOCUMENTS_ROOT / "objects",
            objects,
            references,
        )
        os.chown(objects, APP_UID, APP_UID)
        self.receipt["restored_manifest_verified"] = True
        self.save()

    def restrict_clone_access(self):
        """The running IdP must never connect to the restored copy of its schema."""
        ownership = self.receipt.get("database_ownership")
        require(
            ownership and ownership == self.database_marker(),
            "database_ownership_not_proven",
        )
        role = self.receipt.get("database_app_role")
        require(
            isinstance(role, str) and role and role != "acti_keycloak",
            "invalid_clone_app_role",
        )
        database = identifier(TARGET_DATABASE)
        self.sql(
            "postgres",
            "BEGIN;\nDO $owned$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_database WHERE datname="
            + literal(TARGET_DATABASE)
            + " AND oid="
            + literal(ownership["oid"])
            + "::oid AND shobj_description(oid,'pg_database')="
            + literal(ownership["marker"])
            + ") THEN RAISE EXCEPTION 'restore_database_not_owned'; END IF; END $owned$;\n"
            + "REVOKE CONNECT ON DATABASE "
            + database
            + " FROM PUBLIC;\nGRANT CONNECT ON DATABASE "
            + database
            + " TO "
            + identifier(role)
            + ";\nDO $isolate$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='acti_keycloak') THEN EXECUTE "
            + literal("REVOKE CONNECT ON DATABASE " + database + ' FROM "acti_keycloak"')
            + "; IF has_database_privilege('acti_keycloak',"
            + literal(TARGET_DATABASE)
            + ",'CONNECT') THEN RAISE EXCEPTION 'identity_provider_clone_access'; END IF; END IF; END $isolate$;\n"
            + "ALTER DATABASE "
            + database
            + " CONNECTION LIMIT -1;\nCOMMIT;",
        )
        self.receipt["identity_provider_clone_access_blocked"] = True
        self.save()

    def revoke_cloned_credentials(self):
        require(
            self.receipt.get("restored_manifest_verified") is True,
            "restore_manifest_not_verified",
        )
        ownership = self.database_marker()
        require(
            ownership == self.receipt.get("database_ownership")
            and ownership is not None
            and ownership["marker"] == PURPOSE + ":" + self.receipt["run_id"],
            "foreign_database_refused",
        )
        self.stage("restore.revoke_cloned_credentials")
        query = (
            "BEGIN;\nDO $restore_guard$ BEGIN IF current_database() <> "
            + literal(TARGET_DATABASE)
            + " OR (SELECT oid::text FROM pg_database WHERE datname=current_database()) IS DISTINCT FROM "
            + literal(ownership["oid"])
            + " OR (SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()) IS DISTINCT FROM "
            + literal(ownership["marker"])
            + " THEN RAISE EXCEPTION 'clone_ownership_required'; END IF; END; $restore_guard$;\n"
            + "WITH sessions AS (UPDATE public.auth_sessions SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL RETURNING 1), "
            + "tokens AS (UPDATE public.service_tokens SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL RETURNING 1) "
            + "SELECT json_build_object('auth_sessions_revoked',(SELECT count(*) FROM sessions),'service_tokens_revoked',(SELECT count(*) FROM tokens));\n"
            + "SELECT json_build_object('auth_sessions_remaining',(SELECT count(*) FROM public.auth_sessions WHERE revoked_at IS NULL),"
            + "'service_tokens_remaining',(SELECT count(*) FROM public.service_tokens WHERE revoked_at IS NULL));\nCOMMIT;"
        )
        rows = [
            json.loads(line)
            for line in self.sql(TARGET_DATABASE, query).splitlines()
            if line.strip()
        ]
        require(
            len(rows) == 2
            and rows[1]
            == {"auth_sessions_remaining": 0, "service_tokens_remaining": 0},
            "cloned_credentials_still_active",
        )
        counts = rows[0]
        require(
            set(counts) == {"auth_sessions_revoked", "service_tokens_revoked"}
            and all(type(value) is int and value >= 0 for value in counts.values()),
            "invalid_credential_revocation_counts",
        )
        self.receipt["credential_revocations"] = {
            "auth_sessions": counts["auth_sessions_revoked"],
            "service_tokens": counts["service_tokens_revoked"],
        }
        self.receipt["cloned_credentials_revoked"] = True
        self.save()

    def create_container(self, kind, arguments, image, command=()):
        require(kind in {"api", "redis", "frontend"}, "invalid_container_kind")
        self.stage("container.create." + kind)
        name = self.receipt["names"][kind]
        cid = (
            self.docker(
                "create",
                "--name",
                name,
                "--label",
                LABEL + "=" + self.receipt["run_id"],
                "--label",
                "org.acti.restore-verification.purpose=" + PURPOSE,
                "--restart",
                "no",
                "--security-opt",
                "no-new-privileges:true",
                *arguments,
                image,
                *command,
            )
            .decode()
            .strip()
        )
        require(re.fullmatch(r"[0-9a-f]{64}", cid), "invalid_created_container_id")
        self.receipt["containers"][kind] = cid
        self.save()
        return cid

    def create_network(self, kind, *, internal):
        require(
            kind in {"network", "edge"} and internal == (kind == "network"),
            "invalid_network_kind",
        )
        self.stage("network.create." + kind)
        network = (
            self.docker(
                "network",
                "create",
                *(["--internal"] if internal else []),
                "--label",
                LABEL + "=" + self.receipt["run_id"],
                "--label",
                "org.acti.restore-verification.purpose=" + PURPOSE,
                self.receipt["names"][kind],
            )
            .decode()
            .strip()
        )
        require(re.fullmatch(r"[0-9a-f]{64}", network), "invalid_created_network_id")
        self.receipt["network_id" if kind == "network" else "edge_network_id"] = network
        self.save()
        return network

    def connect_frontend_edge(self, container):
        require(
            container == self.receipt["containers"].get("frontend")
            and bool(self.receipt.get("edge_network_id")),
            "invalid_edge_target",
        )
        self.stage("frontend.edge_connect")
        self.docker("network", "connect", self.receipt["names"]["edge"], container)

    def launch(self, env):
        require(
            self.receipt.get("cloned_credentials_revoked") is True,
            "cloned_credentials_not_revoked",
        )
        names = self.receipt["names"]
        self.create_network("network", internal=True)
        self.create_network("edge", internal=False)
        redis = self.create_container(
            "redis",
            [
                "--network",
                names["network"],
                "--memory",
                "64m",
                "--cpus",
                ".25",
                "--pids-limit",
                "32",
                "--read-only",
                "--tmpfs",
                "/data:rw,noexec,nosuid,size=8m",
            ],
            self.receipt["images"]["redis"],
            [
                "redis-server",
                "--save",
                "",
                "--appendonly",
                "no",
                "--maxmemory",
                "32mb",
                "--maxmemory-policy",
                "noeviction",
            ],
        )
        app_env = {
            "ACTI_DATABASE_URL": clone_database_url(env["ACTI_DATABASE_URL"]),
            "ACTI_REDIS_URL": "redis://" + names["redis"] + ":6379/0",
            "ACTI_SESSION_SECRET": secrets.token_hex(32),
            "ACTI_ORIGIN": ORIGIN,
            "ACTI_STORAGE_ROOT": "/documents",
            "ACTI_SOURCE_SHA": self.receipt["source_sha"],
            "ACTI_TRUSTED_PROXY_CIDRS": "",
            "ACTI_OIDC_ENABLED": "false",
        }
        env_path = self.receipt_path.parent / "clone.env"
        fd = os.open(
            env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(
                "".join(key + "=" + value + "\n" for key, value in app_env.items())
            )
        api = self.create_container(
            "api",
            [
                "--network",
                names["network"],
                "--memory",
                "512m",
                "--cpus",
                ".5",
                "--pids-limit",
                "64",
                "--read-only",
                "--user",
                "10001:10001",
                "--cap-drop",
                "ALL",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--env-file",
                str(env_path),
                "--mount",
                "type=bind,source="
                + str(Path(self.receipt["documents_copy"]) / "objects")
                + ",target=/documents,readonly",
            ],
            self.receipt["images"]["api"],
        )
        self.stage("api.private_pg_connect")
        self.docker("network", "connect", self.receipt["production_network"], api)
        nginx = Path(env["ACTI_RELEASE_DIR"]) / "deploy/nginx.conf"
        config = nginx.read_text(encoding="utf-8")
        require(
            config.count("server api:8000 resolve;") == 1, "unexpected_nginx_upstream"
        )
        config = config.replace(
            "server api:8000 resolve;", "server " + names["api"] + ":8000 resolve;"
        )
        nginx_path = self.receipt_path.parent / "nginx.conf"
        nginx_path.write_text(config, encoding="utf-8")
        os.chmod(nginx_path, 0o600)
        frontend = self.create_container(
            "frontend",
            [
                "--network",
                names["network"],
                "--memory",
                "128m",
                "--cpus",
                ".25",
                "--pids-limit",
                "64",
                "--read-only",
                "--tmpfs",
                "/var/cache/nginx:rw,noexec,nosuid,size=16m",
                "--tmpfs",
                "/var/run:rw,noexec,nosuid,size=4m",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=8m",
                "--publish",
                "127.0.0.1:8444:443",
                "--mount",
                "type=bind,source="
                + str(nginx_path)
                + ",target=/etc/nginx/conf.d/default.conf,readonly",
                "--mount",
                "type=bind,source=/etc/acti-v3/tls,target=/etc/nginx/tls,readonly",
            ],
            self.receipt["images"]["frontend"],
        )
        self.connect_frontend_edge(frontend)
        for kind, cid in (("redis", redis), ("api", api), ("frontend", frontend)):
            self.stage("container.start." + kind)
            self.docker("start", cid)
        self.stage("health.strict_https")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                live = self.https("live")
                ready = self.https("ready")
                if (
                    live[0] == 200
                    and ready[0] == 503
                    and ready[1].get("checks")
                    == {"database": True, "redis": True, "storage": False}
                ):
                    break
            except RestoreSafetyError:
                pass
            time.sleep(1)
        else:
            raise RestoreSafetyError("readonly_clone_health_not_confirmed")
        self.stage("storage.readonly_probe")
        self.docker(
            "exec",
            api,
            "python",
            "-c",
            "import os; assert os.access('/documents',os.R_OK|os.X_OK); assert not os.access('/documents',os.W_OK); print('readonly-copy-readable')",
        )
        self.receipt.update(
            status="prepared",
            clone_live=200,
            clone_readiness=503,
            clone_checks=ready[1]["checks"],
            origin=ORIGIN,
            external_disaster_recovery=False,
        )
        self.save()

    def https(self, check):
        require(check in {"live", "ready"}, "invalid_health_probe")
        data = run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--cacert",
                "/etc/acti-v3/tls/ca.crt",
                "--connect-timeout",
                "3",
                "--max-time",
                "10",
                "--write-out",
                "\n%{http_code}",
                "https://localhost:8444/api/v1/health/" + check,
            ],
            timeout=15,
        )
        try:
            body, status_code = data.rsplit(b"\n", 1)
            status_code, body = int(status_code), json.loads(body)
        except (ValueError, TypeError):
            raise RestoreSafetyError("health_response_not_ready") from None
        require(
            100 <= status_code <= 599 and isinstance(body, dict),
            "health_response_not_ready",
        )
        return status_code, body

    def cleanup(self):
        validate_receipt(self.receipt, self.receipt_path)
        if self.receipt.get("status") == "cleaned":
            return
        verify_expected_mounts(self.receipt["mounts"])
        for kind in ("frontend", "api", "redis"):
            name = self.receipt["names"][kind]
            found = subprocess.run(
                [
                    "docker",
                    "container",
                    "ls",
                    "-aq",
                    "--filter",
                    "name=^/" + name + "$",
                ],
                capture_output=True,
                timeout=15,
                check=True,
            ).stdout.strip()
            if not found:
                continue
            info = json.loads(
                self.docker("inspect", "--format", "{{json .Config.Labels}}", name)
            )
            require(
                info.get(LABEL) == self.receipt["run_id"]
                and info.get("org.acti.restore-verification.purpose") == PURPOSE,
                "foreign_container_refused",
            )
            cid = self.docker("inspect", "--format", "{{.Id}}", name).decode().strip()
            require(
                kind not in self.receipt["containers"]
                or self.receipt["containers"][kind] == cid,
                "container_id_changed",
            )
            self.docker("rm", "--force", cid)
        for kind in ("edge", "network"):
            if kind in self.receipt["names"]:
                self.cleanup_network(kind)
        ownership = (
            self.database_marker()
            if self.receipt.get("database_creation_started")
            else None
        )
        if ownership:
            expected = self.receipt.get("database_ownership")
            require(
                (expected is None or ownership == expected)
                and ownership["marker"] == PURPOSE + ":" + self.receipt["run_id"],
                "foreign_database_refused",
            )
            self.docker(
                "exec",
                self.receipt["postgres_container"],
                "dropdb",
                "-U",
                "postgres",
                TARGET_DATABASE,
            )
            require(
                self.database_marker() is None, "restore_database_cleanup_not_confirmed"
            )
        for root in (DOCUMENTS_ROOT, POSTGRES_ROOT):
            remove_owned_directory(root, self.receipt["run_id"])
        for name in ("clone.env", "nginx.conf"):
            (self.receipt_path.parent / name).unlink(missing_ok=True)
        self.receipt.update(status="cleaned", cleanup_verified=True)
        self.save()

    def cleanup_network(self, kind):
        require(kind in {"edge", "network"}, "invalid_network_kind")
        network_name = self.receipt["names"][kind]
        found_network = subprocess.run(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                "name=^" + network_name + "$",
            ],
            capture_output=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        if found_network:
            info = json.loads(
                self.docker(
                    "network", "inspect", "--format", "{{json .Labels}}", network_name
                )
            )
            require(
                info.get(LABEL) == self.receipt["run_id"], "foreign_network_refused"
            )
            network_id = (
                self.docker("network", "inspect", "--format", "{{.Id}}", network_name)
                .decode()
                .strip()
            )
            require(
                not self.receipt.get(
                    "network_id" if kind == "network" else "edge_network_id"
                )
                or network_id
                == self.receipt[
                    "network_id" if kind == "network" else "edge_network_id"
                ],
                "network_id_changed",
            )
            self.docker("network", "rm", network_id)


def prepare(
    expected_sha, runtime_env, state_root, *, bundle_source=None, export_only=False
):
    global DIAGNOSTIC_FILE
    require(os.name == "posix" and os.geteuid() == 0, "root_linux_required")
    require(re.fullmatch(r"[0-9a-f]{40}", expected_sha), "invalid_expected_sha")
    require(
        not (export_only and bundle_source is not None), "export_bundle_input_forbidden"
    )
    bundle = (
        load_bundle(bundle_source, expected_sha) if bundle_source is not None else None
    )
    env = parse_env(protected(runtime_env).read_text(encoding="utf-8"))
    require(env.get("ACTI_SOURCE_SHA") == expected_sha, "runtime_source_sha_mismatch")
    release = no_symlinks(env["ACTI_RELEASE_DIR"])
    require(
        release.stat().st_uid == 0 and not release.stat().st_mode & 0o022,
        "unprotected_release",
    )
    clone_database_url(env["ACTI_DATABASE_URL"])
    run(
        ["python3", str(release / "deploy/preflight.py")],
        timeout=15,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "ACTI_POSTGRES_UUID": env.get("ACTI_POSTGRES_UUID", ""),
            "ACTI_DOCUMENTS_UUID": env.get("ACTI_DOCUMENTS_UUID", ""),
        },
    )
    expected_mounts = {
        str(POSTGRES_ROOT): env.get("ACTI_POSTGRES_UUID", ""),
        str(DOCUMENTS_ROOT): env.get("ACTI_DOCUMENTS_UUID", ""),
    }
    verify_expected_mounts(expected_mounts)
    state_root = Path(state_root).absolute()
    require(
        state_root != release and release not in state_root.parents,
        "state_inside_release_forbidden",
    )
    state_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_root(state_root)
    run_id = str(uuid4())
    state = state_root / run_id
    state.mkdir(mode=0o700)
    receipt = {
        "run_id": run_id,
        "purpose": PURPOSE,
        "target_database": TARGET_DATABASE,
        "source_sha": expected_sha,
        "state_root": str(state_root),
        "names": run_names(run_id),
        "pg_copy": str(owned_directory(POSTGRES_ROOT, run_id)),
        "documents_copy": str(owned_directory(DOCUMENTS_ROOT, run_id)),
        "containers": {},
        "status": "initializing",
        "mounts": expected_mounts,
        "export_only": export_only,
        "external_bundle": bundle is not None,
        "database_app_role": unquote(urlsplit(env["ACTI_DATABASE_URL"]).username),
    }
    verifier = Verifier(receipt, state / "receipt.json")
    verifier.save()
    DIAGNOSTIC_FILE = state / "command-stderr.log"
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(release / "deploy/images.lock.env"),
        "--env-file",
        str(runtime_env),
        "-f",
        str(release / "deploy/compose.yaml"),
    ]
    try:
        verifier.stage("production_validation")
        run([*compose, "config", "--quiet"])
        ids = {}
        for kind in ("postgres", "api", "frontend"):
            cid = run([*compose, "ps", "-q", kind]).decode().strip()
            require(
                re.fullmatch(r"[0-9a-f]{64}", cid), "production_container_not_unique"
            )
            ids[kind] = cid
        receipt["postgres_container"] = ids["postgres"]
        if not export_only:
            require(
                verifier.database_marker() is None, "restore_database_already_exists"
            )
        receipt["images"] = {}
        for kind in ("api", "frontend"):
            image = (
                verifier.docker("inspect", "--format", "{{.Image}}", ids[kind])
                .decode()
                .strip()
            )
            label = (
                verifier.docker(
                    "image",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                    image,
                )
                .decode()
                .strip()
            )
            require(label == expected_sha, "production_image_source_mismatch")
            receipt["images"][kind] = image
        require(
            verifier.docker(
                "image",
                "inspect",
                "--format",
                "{{.Config.User}}",
                receipt["images"]["api"],
            )
            .decode()
            .strip()
            == "10001:10001",
            "unexpected_api_image_user",
        )
        images = parse_env(
            (release / "deploy/images.lock.env").read_text(encoding="utf-8")
        )
        require(
            re.fullmatch(
                r"postgres@sha256:[0-9a-f]{64}", images.get("POSTGRES_IMAGE", "")
            ),
            "postgres_image_not_pinned",
        )
        postgres_image = (
            verifier.docker("inspect", "--format", "{{.Image}}", ids["postgres"])
            .decode()
            .strip()
        )
        require(
            postgres_image
            == verifier.docker(
                "image", "inspect", "--format", "{{.Id}}", images["POSTGRES_IMAGE"]
            )
            .decode()
            .strip(),
            "postgres_image_digest_mismatch",
        )
        require(
            re.fullmatch(r"redis@sha256:[0-9a-f]{64}", images.get("REDIS_IMAGE", "")),
            "redis_image_not_pinned",
        )
        receipt["images"]["redis"] = (
            verifier.docker(
                "image", "inspect", "--format", "{{.Id}}", images["REDIS_IMAGE"]
            )
            .decode()
            .strip()
        )
        networks = json.loads(
            verifier.docker(
                "inspect",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                ids["postgres"],
            )
        )
        internal = [
            name
            for name in networks
            if verifier.docker(
                "network", "inspect", "--format", "{{.Internal}}", name
            ).strip()
            == b"true"
        ]
        require(len(internal) == 1, "production_internal_network_not_unique")
        receipt["production_network"] = internal[0]
        if not export_only:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", PORT))
        size = int(
            verifier.sql(
                SOURCE_DATABASE, "SELECT pg_database_size(current_database());"
            )
        )
        require(
            shutil.disk_usage(POSTGRES_ROOT).free >= size * 3 + 64 * 1024**2,
            "insufficient_restore_space",
        )
        for root in (POSTGRES_ROOT, DOCUMENTS_ROOT):
            parent = root / "restore-verification"
            if not parent.exists():
                parent.mkdir(mode=0o700)
            protected(parent, directory=True)
            directory = owned_directory(root, run_id)
            directory.mkdir(mode=0o700)
            (directory / MARKER).write_text(run_id, encoding="utf-8")
            os.chmod(directory / MARKER, 0o600)
        verifier.save()
        if export_only:
            verifier.export_bundle()
        else:
            if bundle is None:
                verifier.restore(verifier.stable_dump())
            else:
                # Revalidate after setup; use the downloaded closed set exclusively.
                verifier.restore_bundle(bundle_source)
            verifier.revoke_cloned_credentials()
            verifier.launch(env)
        return verifier
    except Exception as error:
        receipt["failure_stage"] = receipt.get("stage", "initializing")
        receipt["failure_code"] = (
            str(error)
            if isinstance(error, RestoreSafetyError)
            else type(error).__name__
        )
        verifier.save()
        try:
            verifier.cleanup()
        except Exception:
            receipt["status"] = "cleanup_required"
            verifier.save()
        raise RestoreSafetyError("prepare_failed_check_private_receipt") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "export"):
        create = sub.add_parser(command)
        create.add_argument("--expected-sha", required=True)
        create.add_argument(
            "--runtime-env", type=Path, default=Path("/etc/acti-v3/runtime.env")
        )
        create.add_argument("--state-root", type=Path, default=STATE_ROOT)
        if command == "prepare":
            create.add_argument("--bundle-source", type=Path)
    remove = sub.add_parser("cleanup")
    remove.add_argument("--receipt", required=True, type=Path)
    remove.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        if args.command in {"prepare", "export"}:
            verifier = prepare(
                args.expected_sha,
                args.runtime_env,
                args.state_root,
                bundle_source=getattr(args, "bundle_source", None),
                export_only=args.command == "export",
            )
        else:
            require(os.name == "posix" and os.geteuid() == 0, "root_linux_required")
            receipt_path = protected(args.receipt)
            protected(receipt_path.parent, directory=True)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            require(
                receipt["run_id"] == canonical_uuid(args.run_id),
                "cleanup_run_id_mismatch",
            )
            verifier = Verifier(receipt, receipt_path)
            verifier.cleanup()
        receipt = verifier.receipt
        print(
            json.dumps(
                {
                    "status": receipt["status"],
                    "run_id": receipt["run_id"],
                    "source_sha": receipt["source_sha"],
                    "tables": len(receipt.get("manifest", {}).get("tables", {})),
                    "sequences": len(receipt.get("manifest", {}).get("sequences", {})),
                    "files": receipt.get("copies", {}).get("files", 0),
                    "bytes": receipt.get("copies", {}).get("bytes", 0),
                    "external_disaster_recovery": False,
                    "external_bundle": receipt.get("external_bundle", False),
                    "bundle_source": receipt.get("bundle_source"),
                    "receipt": str(verifier.receipt_path),
                    "schema": receipt.get("schema"),
                    "dump_sha256": receipt.get("dump_sha256"),
                    "cleanup_verified": receipt.get("cleanup_verified", False),
                    "cloned_credentials_revoked": receipt.get(
                        "cloned_credentials_revoked", False
                    ),
                    "credential_revocations": receipt.get("credential_revocations", {}),
                }
            )
        )
    except Exception as error:
        code = (
            str(error)
            if isinstance(error, RestoreSafetyError)
            else "restore_verification_failed"
        )
        print(
            json.dumps(
                {"status": "FAIL", "code": code, "external_disaster_recovery": False}
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
