import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.openapi.utils import get_openapi
from redis import Redis
from sqlalchemy.orm import sessionmaker

from app.api import account_admin, admin, auth, health, oidc, users
from app.api.dependencies import current_actor, current_admin
from app.api.errors import register_errors
from app.api.middleware import register_middleware
from app.api.schemas import ERROR_RESPONSES
from app.content import api as content_api
from app.content import operations as search_operations
from app.core.config import Settings
from app.db.session import build_engine
from app.execution import api as execution_api
from app.knowledge import api as knowledge_api
from app.numbering import api as numbering_api
from app.processes import api as processes_api
from app.registry import (
    config_api,
    document_api,
    file_api,
    office_api,
    office_sync_api,
    office_sync_distribution,
    selection_api,
)
from app.relations import api as relations_api
from app.service_api import admin as service_admin
from app.service_api import api as service_api
from app.service_api import operations as service_operations


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        yield
        app.state.redis.close()
        app.state.engine.dispose()
        app.state.readiness_engine.dispose()

    app = FastAPI(
        title="ACTI v3 API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        responses=ERROR_RESPONSES,
    )
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    app.state.settings = settings
    app.state.engine = build_engine(settings)
    app.state.readiness_engine = build_engine(settings, readiness=True)
    app.state.factory = sessionmaker(app.state.engine, expire_on_commit=False)
    app.state.redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    register_middleware(app)
    register_errors(app)
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.public, prefix="/api/v1")
    app.include_router(oidc.public, prefix="/api/v1")
    app.include_router(office_api.public, prefix="/api/v1")
    app.include_router(office_sync_api.client_router, prefix="/api/v1")
    app.include_router(office_sync_distribution.router, prefix="/api/v1")
    protected = APIRouter(dependencies=[Depends(current_actor)])
    for router in (
        auth.protected,
        users.router,
        admin.router,
        account_admin.router,
        oidc.admin,
        config_api.router,
        document_api.router,
        file_api.router,
        office_api.router,
        office_sync_api.router,
        selection_api.router,
        numbering_api.router,
        content_api.router,
        search_operations.router,
        relations_api.router,
        processes_api.router,
        knowledge_api.router,
        execution_api.router,
        service_admin.router,
        service_operations.router,
    ):
        protected.include_router(router)
    app.state.protected_router = protected
    app.include_router(protected, prefix="/api/v1")
    app.include_router(service_api.router, prefix="/api/service/v1")

    @app.get("/api/v1/openapi.json", dependencies=[Depends(current_admin)], include_in_schema=False)
    def openapi():
        return get_openapi(title=app.title, version=app.version, routes=app.routes)

    return app
