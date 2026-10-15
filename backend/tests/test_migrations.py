import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, inspect, text


def test_fixture_refuses_non_test_database():
    from app.db.session import validate_test_database

    with pytest.raises(ValueError):
        validate_test_database("postgresql+psycopg://localhost/acti")
    validate_test_database("postgresql+psycopg://localhost/acti_test")


def test_clean_repeat_and_concurrent_upgrade():
    from app.db.migrate import upgrade_database

    engine = create_engine(os.environ["ACTI_DATABASE_URL"])
    from app.db.session import validate_test_database

    validate_test_database(str(engine.url))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: upgrade_database(engine), range(2)))
    upgrade_database(engine)
    assert {
        "users",
        "roles",
        "user_roles",
        "local_credentials",
        "auth_sessions",
        "audit_events",
        "outbox_events",
        "processed_events",
    } <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0006_document_relations"
        assert conn.scalar(text("SELECT count(*) FROM roles")) == 4
    engine.dispose()
