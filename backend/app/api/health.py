import os
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter()


@router.get("/health/live")
def live():
    return {"status": "live"}


@router.get("/health/ready", responses={503: {"description": "Dependencies unavailable"}})
def ready(request: Request):
    checks = {}
    try:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        head = ScriptDirectory.from_config(config).get_current_head()
        with request.app.state.engine.connect() as conn:
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
