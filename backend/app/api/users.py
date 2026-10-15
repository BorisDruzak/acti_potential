from fastapi import APIRouter, Depends, Request

from app.api.dependencies import COOKIE_NAME, current_actor
from app.api.schemas import CurrentUserResponse
from app.identity.sessions import csrf_for_token

router = APIRouter()


@router.get("/users/me", response_model=CurrentUserResponse)
def me(request: Request, actor=Depends(current_actor)):
    return {
        "id": str(actor.id),
        "display_name": actor.display_name,
        "roles": sorted(actor.roles),
        "csrf_token": csrf_for_token(request.cookies[COOKIE_NAME]),
    }
