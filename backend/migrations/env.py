import os

from alembic import context
from sqlalchemy import create_engine

from app.content import models as content_models  # noqa: F401
from app.db.base import Base
from app.execution import models as execution_models  # noqa: F401
from app.identity import models as identity_models  # noqa: F401
from app.knowledge import models as knowledge_models  # noqa: F401
from app.numbering import models as numbering_models  # noqa: F401
from app.platform import models as platform_models  # noqa: F401
from app.processes import models as process_models  # noqa: F401
from app.registry import models as registry_models  # noqa: F401
from app.relations import models as relation_models  # noqa: F401

config = context.config


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    engine = create_engine(os.environ["ACTI_DATABASE_URL"], hide_parameters=True)
    with engine.connect() as conn:
        run(conn)
