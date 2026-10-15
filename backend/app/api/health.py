import os
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.schemas import LiveResponse, ReadyResponse

router = APIRouter()


@router.get("/health/live", response_model=LiveResponse)
def live():
    return {"status": "live"}


@router.get(
    "/health/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse, "description": "Dependencies unavailable"}},
)
def ready(request: Request):
    checks = {}
    try:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        head = ScriptDirectory.from_config(config).get_current_head()
        with request.app.state.readiness_engine.connect() as conn:
            checks["database"] = (
                conn.scalar(text("SELECT version_num FROM alembic_version")) == head
            )
    except Exception:
        checks["database"] = False
    try:
        checks["redis"] = bool(request.app.state.redis.ping())
    except Exception:
        checks["redis"] = False
    root = request.app.state.settings.storage_root
    checks["storage"] = Path(root).is_dir() and os.access(root, os.R_OK | os.W_OK | os.X_OK)
    healthy = all(checks.values())
    return JSONResponse(
        {"status": "ready" if healthy else "unavailable", "checks": checks},
        status_code=200 if healthy else 503,
    )
