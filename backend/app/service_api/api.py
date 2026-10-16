"""The versioned, explicitly typed external read contract."""

from fastapi import APIRouter, Depends, FastAPI
from fastapi.openapi.utils import get_openapi

from app.api.schemas import ERROR_RESPONSES, ErrorEnvelope
from app.service_api import events, retrieval, search
from app.service_api.auth import current_service

router = APIRouter(
    responses={**ERROR_RESPONSES, 404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}}
)
router.include_router(retrieval.router)
router.include_router(events.router)
router.include_router(search.router)


def schema():
    # Deliberately independent from Settings and host-specific application state.
    public = FastAPI()
    public.include_router(router, prefix="/api/service/v1")
    return get_openapi(title="ACTI knowledge service", version="1.0.0", routes=public.routes)


@router.get("/openapi.json", dependencies=[Depends(current_service)], include_in_schema=False)
def openapi():
    return schema()
