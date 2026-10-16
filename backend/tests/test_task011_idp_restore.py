"""Real PostgreSQL coverage for the IdP portion of external backup manifests."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "deploy/restore-verify.py"
spec = importlib.util.spec_from_file_location("acti_idp_restore_verify", MODULE_PATH)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def test_real_postgresql_creates_owned_clone_with_connections_closed_before_acl(
    factory, monkeypatch
):
    engine = factory.kw["bind"]
    run_id = str(uuid4())
    target = "acti_restore_create_" + run_id.replace("-", "") + "_test"
    marker = restore.PURPOSE + ":" + run_id
    monkeypatch.setattr(restore, "TARGET_DATABASE", target)
    verifier = object.__new__(restore.Verifier)
    verifier.receipt = {"run_id": run_id}
    monkeypatch.setattr(verifier, "save", lambda: None)
    monkeypatch.setattr(verifier, "stage", lambda value: None)
    monkeypatch.setattr(
        verifier, "docker", lambda *args, **kwargs: pytest.fail("Database creation must use SQL")
    )

    class CreationObserved(Exception):
        pass

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        assert connection.exec_driver_sql("SELECT current_database()").scalar_one().endswith("_test")
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM pg_database WHERE datname='acti'"
        ).scalar_one() == 0, "Never create/drop test databases in the ACTI production cluster"
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM pg_database WHERE datname=" + restore.literal(target)
        ).scalar_one() == 0

        def sql(database, query):
            assert database == "postgres"
            result = connection.exec_driver_sql(query)
            value = result.scalar_one_or_none() if result.returns_rows else None
            return json.dumps(value).encode() if value is not None else b""

        def inspect_before_acl():
            row = connection.exec_driver_sql(
                "SELECT datconnlimit,shobj_description(oid,'pg_database'),oid::text "
                "FROM pg_database WHERE datname=" + restore.literal(target)
            ).one()
            assert row[0] == 0, "Non-superusers must be blocked before grants are restricted"
            assert row[1] == marker
            assert row[2] == verifier.receipt["database_ownership"]["oid"]
            raise CreationObserved()

        monkeypatch.setattr(verifier, "sql", sql)
        monkeypatch.setattr(verifier, "restrict_clone_access", inspect_before_acl)
        try:
            with pytest.raises(CreationObserved):
                verifier.restore(Path("not-opened-before-database-isolation.dump"))
        finally:
            row = connection.exec_driver_sql(
                "SELECT oid::text,shobj_description(oid,'pg_database') FROM pg_database WHERE datname="
                + restore.literal(target)
            ).one_or_none()
            if row is not None:
                assert target == "acti_restore_create_" + run_id.replace("-", "") + "_test"
                assert row[1] == marker, "Never drop a database without this run's owner marker"
                assert verifier.receipt["database_ownership"]["oid"] == row[0]
                connection.exec_driver_sql("DROP DATABASE " + restore.identifier(target))
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM pg_database WHERE datname=" + restore.literal(target)
        ).scalar_one() == 0


def test_real_catalog_includes_keycloak_data_and_schema_with_quoted_names(
    factory, monkeypatch
):
    # The factory validates the disposable test database. Everything below rolls back.
    verifier = object.__new__(restore.Verifier)
    engine = factory.kw["bind"]
    with engine.begin() as connection:
        def sql(database, query):
            rows = []
            for statement in query.splitlines():
                if not statement.strip() or statement.startswith(("BEGIN ", "COMMIT;")):
                    continue
                # Catalog SELECTs span lines; execute them together below.
                if statement.startswith("SET LOCAL "):
                    connection.exec_driver_sql(statement)
            query = "\n".join(
                line for line in query.splitlines()
                if line.strip() and not line.startswith(("BEGIN ", "COMMIT;", "SET LOCAL "))
            )
            # Each manifest SELECT is one line; schema SELECT spans multiple lines.
            statements = query.splitlines() if query.startswith("SELECT json_build_object('kind'") else [query]
            for statement in statements:
                value = connection.exec_driver_sql(statement).scalar_one()
                rows.append(json.dumps(value))
            return "\n".join(rows).encode()

        monkeypatch.setattr(verifier, "sql", sql)
        before = verifier.schema(restore.SOURCE_DATABASE)
        connection.exec_driver_sql("CREATE SCHEMA keycloak")
        connection.exec_driver_sql(
            'CREATE TABLE keycloak."realm.with""quote" '
            '(id integer PRIMARY KEY, label text NOT NULL DEFAULT \'active\')'
        )
        connection.exec_driver_sql(
            'INSERT INTO keycloak."realm.with""quote" (id) VALUES (1)'
        )
        connection.exec_driver_sql('CREATE SEQUENCE keycloak."seq.with.dot" START 7')
        connection.exec_driver_sql('SELECT nextval(\'keycloak."seq.with.dot"\')')
        unrelated = "restore_ignored_" + uuid4().hex
        connection.exec_driver_sql('CREATE SCHEMA "' + unrelated + '"')
        connection.exec_driver_sql('CREATE TABLE "' + unrelated + '".unrelated(id integer)')

        manifest = verifier.manifest(restore.SOURCE_DATABASE)
        assert "users" in manifest["tables"]
        assert manifest["tables"]['keycloak.realm.with"quote']["count"] == 1
        assert manifest["sequences"]["keycloak.seq.with.dot"] == {
            "last_value": "7", "is_called": True,
        }
        assert not any(unrelated in key for values in manifest.values() for key in values)
        current = verifier.schema(restore.SOURCE_DATABASE)
        assert before != current
        connection.exec_driver_sql(
            'ALTER TABLE keycloak."realm.with""quote" ALTER COLUMN label SET DEFAULT \'changed\''
        )
        assert current != verifier.schema(restore.SOURCE_DATABASE)
        connection.exec_driver_sql(
            'UPDATE keycloak."realm.with""quote" SET label=\'changed\''
        )
        assert manifest != verifier.manifest(restore.SOURCE_DATABASE)
        # Force rollback even on success; the fixture never commits IdP test objects.
        connection.rollback()
