import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.migrate import upgrade_database
from app.db.session import validate_test_database


@pytest.fixture
def factory():
    url = os.environ["ACTI_DATABASE_URL"]
    validate_test_database(url)
    engine = create_engine(url, hide_parameters=True)
    upgrade_database(engine)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE users, outbox_events RESTART IDENTITY CASCADE"))
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()
