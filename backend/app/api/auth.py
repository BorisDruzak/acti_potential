import hashlib
import hmac
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from app.api.dependencies import COOKIE_NAME, check_origin, current_actor, db_session
from app.api.errors import fail
from app.api.middleware import source_address
from app.identity.service import AuthenticationError, authenticate_local
from app.identity.sessions import issue_session, revoke_session
from app.platform.audit import AuditInput, record_audit

public = APIRouter()
protected = APIRouter()


class LoginInput(BaseModel):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024, repr=False)


def limit_login(request):
    key = hmac.new(
        request.app.state.settings.session_secret.get_secret_value().encode(),
        source_address(request).encode(),
        hashlib.sha256,
    ).hexdigest()
    script = "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n"
    try:
        count = request.app.state.redis.eval(script, 1, "login:" + key)
    except RedisError:
        fail(503, "auth_unavailable", "Вход временно недоступен")
    if count > 10:
        fail(429, "too_many_attempts", "Слишком много попыток. Повторите через минуту")


@public.post("/auth/login")
def login(
    data: LoginInput,
    request: Request,
    response: Response,
    session=Depends(db_session, scope="function"),
    origin=Depends(check_origin),
):
    limit_login(request)
    try:
        user = authenticate_local(session, data.login, data.password)
    except AuthenticationError:
        with request.app.state.factory.begin() as audit_session:
            record_audit(
                audit_session,
                AuditInput(
                    None,
                    "auth.login_failed",
                    "user",
                    None,
                    {"reason_code": "credentials_rejected"},
                    request.state.request_id,
                    source_address(request),
                ),
            )
        raise
    issued = issue_session(session, user.id, datetime.now(UTC))
    record_audit(
        session,
        AuditInput(
            user.id,
            "auth.login",
            "session",
            None,
            {},
            request.state.request_id,
            source_address(request),
        ),
    )
    response.set_cookie(
        COOKIE_NAME,
        issued.token,
        max_age=43200,
        expires=issued.expires_at,
        secure=request.app.state.settings.secure_cookie,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"status": "authenticated"}


@protected.post("/auth/logout", status_code=204)
def logout(
    request: Request, actor=Depends(current_actor), session=Depends(db_session, scope="function")
):
    revoke_session(session, request.cookies.get(COOKIE_NAME, ""))
    record_audit(
        session,
        AuditInput(
            actor.id,
            "auth.logout",
            "session",
            None,
            {},
            request.state.request_id,
            source_address(request),
        ),
    )
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=request.app.state.settings.secure_cookie,
        httponly=True,
        samesite="lax",
    )
    return response
