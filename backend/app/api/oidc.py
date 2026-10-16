from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import select, update

from app.api.account_admin import audit
from app.api.auth import limit_login
from app.api.dependencies import COOKIE_NAME, check_origin, current_admin, db_session
from app.api.errors import fail
from app.api.middleware import source_address
from app.identity import account_service
from app.identity.models import AuthSession, ExternalIdentity, User
from app.identity.oidc import BINDING_COOKIE, TTL, OIDCError, begin, complete, configuration
from app.identity.sessions import issue_session
from app.platform.audit import AuditInput, record_audit

public = APIRouter()
admin = APIRouter()


class ProviderConfig(BaseModel):
    enabled: bool
    label: str


class LoginStart(BaseModel):
    authorization_url: str


class LinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=255)


class IdentityOutput(BaseModel):
    id: UUID
    issuer: str
    subject: str
    active: bool
    created_at: datetime


def serialize(identity):
    return {name: getattr(identity, name) for name in IdentityOutput.model_fields}


@public.get("/auth/oidc/config", response_model=ProviderConfig)
def provider_config(request: Request):
    settings = request.app.state.settings
    try:
        configuration(settings)
        enabled = True
    except OIDCError:
        enabled = False
    return {"enabled": enabled, "label": settings.oidc_label}


@public.post("/auth/oidc/start", response_model=LoginStart)
def start(request: Request, response: Response, origin=Depends(check_origin)):
    limit_login(request)
    try:
        url, binding = begin(request.app.state.settings, request.app.state.redis)
    except (OIDCError, RedisError):
        fail(503, "oidc_unavailable", "Корпоративный вход временно недоступен")
    response.set_cookie(
        BINDING_COOKIE, binding, max_age=TTL, secure=True, httponly=True,
        samesite="lax", path="/api/v1/auth/oidc",
    )
    return {"authorization_url": url}


def checked_identity_user(session, user_id):
    user = session.get(User, user_id)
    if not user or not user.active:
        return None
    from app.service_api.models import ServiceAccount
    if session.scalar(select(ServiceAccount.id).where(ServiceAccount.user_id == user_id)):
        return None
    roles = account_service.user_roles(session, user_id)
    if not roles or set(roles) - account_service.ROLE_CODES:
        return None
    return user


@public.get("/auth/oidc/callback", include_in_schema=False)
def callback(request: Request, session=Depends(db_session, scope="function")):
    settings = request.app.state.settings
    error_code = "oidc_failed"
    try:
        query = request.query_params
        if any(len(query.getlist(name)) != 1 for name in ("state", "code")) or "error" in query:
            raise OIDCError()
        subject = complete(
            settings, request.app.state.redis, state=query.get("state"),
            code=query.get("code"), binding=request.cookies.get(BINDING_COOKIE),
        )
        identity = session.scalar(select(ExternalIdentity).where(
            ExternalIdentity.issuer == settings.oidc_issuer,
            ExternalIdentity.subject == subject, ExternalIdentity.active.is_(True),
        ).with_for_update())
        user = checked_identity_user(session, identity.user_id) if identity else None
        if user is None:
            error_code = "identity_not_linked"
            raise OIDCError()
        issued = issue_session(
            session, user.id, datetime.now(UTC), external_identity_id=identity.id,
        )
        record_audit(session, AuditInput(
            user.id, "auth.oidc_login", "session", None, {},
            request.state.request_id, source_address(request),
        ))
        response = RedirectResponse(settings.origin + "/", status_code=302)
        response.set_cookie(
            COOKIE_NAME, issued.token, max_age=43200, expires=issued.expires_at,
            secure=True, httponly=True, samesite="lax", path="/",
        )
    except (OIDCError, RedisError):
        session.rollback()
        with request.app.state.factory.begin() as audit_session:
            record_audit(audit_session, AuditInput(
                None, "auth.oidc_login_failed", "user", None, {"reason_code": error_code},
                request.state.request_id, source_address(request),
            ))
        response = RedirectResponse(settings.origin + "/?auth_error=" + error_code, status_code=302)
    response.delete_cookie(
        BINDING_COOKIE, secure=True, httponly=True, samesite="lax", path="/api/v1/auth/oidc",
    )
    return response


@admin.get("/admin/users/{user_id}/external-identities", response_model=list[IdentityOutput])
def list_identities(
    user_id: UUID, actor=Depends(current_admin), session=Depends(db_session, scope="function"),
):
    account_service.get_user(session, user_id)
    return [serialize(identity) for identity in session.scalars(select(ExternalIdentity).where(
        ExternalIdentity.user_id == user_id,
    ).order_by(ExternalIdentity.created_at, ExternalIdentity.id))]


@admin.post("/admin/users/{user_id}/external-identities", response_model=IdentityOutput)
def link_identity(
    user_id: UUID, data: LinkInput, request: Request,
    actor=Depends(current_admin), session=Depends(db_session, scope="function"),
):
    account_service.lock_accounts(session)
    try:
        issuer = configuration(request.app.state.settings)
    except OIDCError:
        fail(503, "oidc_unavailable", "Корпоративный вход не настроен")
    if checked_identity_user(session, user_id) is None:
        fail(409, "identity_user_ineligible", "Пользователь не допускает корпоративный вход")
    if data.subject != data.subject.strip() or any(ord(c) < 32 for c in data.subject):
        fail(422, "invalid_subject", "Неверный идентификатор внешней учётной записи")
    identity = session.scalar(select(ExternalIdentity).where(
        ExternalIdentity.issuer == issuer, ExternalIdentity.subject == data.subject,
    ).with_for_update())
    if identity and identity.user_id != user_id:
        fail(409, "identity_already_linked", "Внешняя учётная запись уже связана с пользователем")
    if not identity:
        identity = ExternalIdentity(user_id=user_id, issuer=issuer, subject=data.subject)
        session.add(identity)
    else:
        identity.active = True
    session.flush()
    audit(session, request, actor, "identity.external_linked", user_id, {"identity_id": str(identity.id)})
    return serialize(identity)


@admin.delete("/admin/users/{user_id}/external-identities/{identity_id}", status_code=204)
def unlink_identity(
    user_id: UUID, identity_id: UUID, request: Request,
    actor=Depends(current_admin), session=Depends(db_session, scope="function"),
):
    account_service.lock_accounts(session)
    identity = session.scalar(select(ExternalIdentity).where(
        ExternalIdentity.id == identity_id, ExternalIdentity.user_id == user_id,
    ).with_for_update())
    if identity is None:
        fail(404, "identity_not_found", "Внешняя учётная запись не найдена")
    identity.active = False
    session.execute(update(AuthSession).where(
        AuthSession.external_identity_id == identity.id, AuthSession.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(UTC)))
    audit(session, request, actor, "identity.external_unlinked", user_id, {"identity_id": str(identity.id)})
    return Response(status_code=204)
