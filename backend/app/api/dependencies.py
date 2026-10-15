import secrets
from datetime import UTC, datetime

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie

from app.api.errors import fail
from app.identity.policy import require_role
from app.identity.service import AuthenticationError
from app.identity.sessions import csrf_for_token, resolve_actor

COOKIE_NAME = "acti_session"
cookie_scheme = APIKeyCookie(name=COOKIE_NAME, auto_error=False)


def db_session(request: Request):
    with request.app.state.factory.begin() as session:
        yield session


def check_origin(request: Request):
    if request.headers.get("Origin") != request.app.state.settings.origin:
        fail(403, "origin_rejected", "Источник запроса не разрешён")


def current_actor(
    request: Request, session=Depends(db_session, scope="function"), token=Depends(cookie_scheme)
):
    if not token:
        raise AuthenticationError()
    actor = resolve_actor(session, token, datetime.now(UTC))
    if actor.must_change_password and request.url.path not in {
        '/api/v1/users/me', '/api/v1/users/me/password', '/api/v1/auth/logout'
    }:
        fail(403, 'password_change_required', 'Сначала смените первоначальный пароль')
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if not secrets.compare_digest(
            request.headers.get("X-CSRF-Token", ""), csrf_for_token(token)
        ):
            fail(403, "csrf_rejected", "Проверка безопасности запроса не пройдена")
    return actor


def current_admin(actor=Depends(current_actor)):
    require_role(actor, "admin")
    return actor
