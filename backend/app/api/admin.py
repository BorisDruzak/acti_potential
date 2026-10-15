import time

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import current_admin
from app.api.schemas import PlatformResponse

router = APIRouter()


@router.get(
    "/admin/platform", dependencies=[Depends(current_admin)], response_model=PlatformResponse
)
def platform(request: Request):
    heartbeat = request.app.state.redis.get("worker:heartbeat")
    return {
        "version": "0.1.0",
        "source_sha": request.app.state.settings.source_sha,
        "worker": "available"
        if heartbeat and time.time() - float(heartbeat) < 30
        else "unavailable",
    }
