"""The pilot must reject live targets before opening any network connection."""

import io
import os
from uuid import UUID

import pytest

from tools.task011_load_pilot import (
    PilotConfig,
    assert_cleanup_owner,
    assert_cleanup_resources,
    assert_schema_claim,
    copy_private_diagnostics,
    guarded_storage,
    validate_config,
)

RUN = UUID("00000000-1111-4222-8333-444444444444")


def config(**changes):
    values = dict(
        run_id=str(RUN),
        database_url=f"postgresql+psycopg://synthetic:synthetic@127.0.0.1:55433/acti_task011_{RUN.hex}_test",
        expected_database_oid=12345,
        redis_url="redis://127.0.0.1:56479/0",
        expected_redis_run_id="synthetic-redis-run",
        origin="https://localhost:18443",
        tls_cert="/private/existing-cert.pem",
        tls_key="/private/existing-key.pem",
        tls_ca="/private/existing-ca.pem",
        storage_mount="/srv/acti/documents",
        expected_storage_uuid="synthetic-storage-uuid",
        db_mount="/srv/acti/postgresql",
        expected_db_uuid="synthetic-db-uuid",
        cards=1000,
        clients=32,
        seconds=10,
        request_limit=1000,
        reindex_limit=20,
    )
    values.update(changes)
    return PilotConfig(**values)


def test_exact_fresh_test_namespace_and_secret_free_repr():
    cfg = config()
    validate_config(cfg)
    assert "synthetic:synthetic" not in repr(cfg)
    assert "existing-key.pem" not in repr(cfg)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+psycopg://synthetic@127.0.0.1/acti",
        "postgresql+psycopg://synthetic@127.0.0.1/acti_task003_test",
        f"postgresql+psycopg://synthetic@192.0.2.1/acti_task011_{RUN.hex}_test",
        f"postgresql+psycopg://synthetic@127.0.0.1/acti_task011_{RUN.hex}_test?host=192.0.2.1",
        "mssql+pyodbc://source/okrug",
        f"postgresql+psycopg://synthetic@127.0.0.1/acti_task011_{UUID(int=2).hex}_test",
    ],
)
def test_rejects_live_shared_remote_source_and_query_override_databases(dsn):
    with pytest.raises(ValueError):
        validate_config(config(database_url=dsn))


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "http://localhost:18443"},
        {"origin": "https://example.test:18443"},
        {"origin": "https://localhost:443"},
        {"redis_url": "redis://192.0.2.1:56479/0"},
        {"redis_url": "redis://127.0.0.1:6379/0"},
        {"redis_url": "redis://127.0.0.1:56479/1"},
        {"cards": 150001},
        {"clients": 65},
        {"seconds": 301},
        {"request_limit": 100001},
        {"reindex_limit": 1001},
        {"expected_database_oid": 0},
        {"expected_redis_run_id": ""},
        {"storage_mount": "/"},
        {"db_mount": "/srv/acti/documents"},
    ],
)
def test_resource_and_protocol_limits_are_fail_closed(changes):
    with pytest.raises(ValueError):
        validate_config(config(**changes))


def test_storage_is_only_exact_owned_subdirectory_and_rejects_symlinks(tmp_path):
    mount = tmp_path / "mount"
    mount.mkdir()
    owned = mount / ".task011-load" / RUN.hex
    assert guarded_storage(mount, RUN) == owned
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (mount / ".task011-load").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation requires OS permission")
    with pytest.raises(ValueError):
        guarded_storage(mount, RUN)


def test_cleanup_requires_exact_database_oid_marker_and_complete_owned_ids():
    cfg = config()
    receipt = {
        "run_id": str(RUN),
        "database_oid": 12345,
        "user_ids": ["u1", "u2"],
        "registry_ids": ["r1", "r2"],
    }
    assert_cleanup_owner(cfg, receipt, 12345, str(RUN), {"u1", "u2"}, {"r1", "r2"})
    for actual_oid, marker, users, registries in [
        (999, str(RUN), {"u1", "u2"}, {"r1", "r2"}),
        (12345, "another-run", {"u1", "u2"}, {"r1", "r2"}),
        (12345, str(RUN), {"u1", "u2", "foreign"}, {"r1", "r2"}),
        (12345, str(RUN), {"u1", "u2"}, {"r1", "r2", "foreign"}),
    ]:
        with pytest.raises(ValueError):
            assert_cleanup_owner(cfg, receipt, actual_oid, marker, users, registries)


def test_cleanup_rejects_foreign_redis_or_storage_before_mutation():
    cfg = config()
    assert_cleanup_resources(
        cfg, cfg.expected_redis_run_id, str(RUN), {"task011:load:owner"}, str(RUN)
    )
    for run, owner, keys, storage in [
        ("foreign-server", str(RUN), {"task011:load:owner"}, str(RUN)),
        (cfg.expected_redis_run_id, "foreign-owner", {"task011:load:owner"}, str(RUN)),
        (cfg.expected_redis_run_id, str(RUN), {"task011:load:owner", "foreign-key"}, str(RUN)),
        (cfg.expected_redis_run_id, str(RUN), {"task011:load:owner"}, "foreign-storage"),
    ]:
        with pytest.raises(ValueError):
            assert_cleanup_resources(cfg, run, owner, keys, storage)


def test_postgresql_default_public_comment_is_allowed_only_in_empty_fresh_exact_database():
    cfg = config()
    assert_schema_claim(cfg, 12345, "standard public schema", set(), False)
    assert_schema_claim(cfg, 12345, None, set(), False)
    assert_schema_claim(cfg, 12345, str(RUN), {"users"}, True)
    for oid, marker, tables, receipt in [
        (999, "standard public schema", set(), False),
        (12345, "standard public schema", {"users"}, False),
        (12345, "standard public schema", set(), True),
        (12345, "foreign-run", set(), False),
        (12345, str(RUN), {"users"}, False),
    ]:
        with pytest.raises(ValueError):
            assert_schema_claim(cfg, oid, marker, tables, receipt)


def test_private_diagnostics_are_bounded_but_input_is_fully_drained(tmp_path):
    stream = io.BytesIO(b"synthetic diagnostics\n" * 1000)
    destination = tmp_path / "private.log"
    descriptor = os.open(
        destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
    )
    copy_private_diagnostics(stream, descriptor, limit=100)
    assert destination.stat().st_size == 100
    assert stream.tell() == len(stream.getvalue())
