from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from app.api.dependencies import COOKIE_NAME, current_actor, current_admin, db_session
from app.api.errors import fail
from app.api.middleware import source_address
from app.identity import account_service
from app.identity.models import User
from app.identity.sessions import issue_session
from app.platform.audit import AuditInput, record_audit
from app.platform.models import AuditEvent
from app.registry.models import Registry, RegistryAccess
from app.registry.policy import ACTIONS

router = APIRouter()


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountCreate(StrictInput):
    login: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=14, max_length=1024, repr=False)
    roles: list[str] = Field(min_length=1, max_length=4)

    @field_validator("display_name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("Display name is required")
        return value.strip()


class AccountUpdate(StrictInput):
    login: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None
    roles: list[str] | None = Field(default=None, min_length=1, max_length=4)

    @field_validator("login", "display_name", "active", "roles")
    @classmethod
    def nonnull_value(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("Value cannot be empty")
        return value


class PasswordReset(StrictInput):
    password: str = Field(min_length=14, max_length=1024, repr=False)


class PasswordChange(StrictInput):
    current_password: str = Field(min_length=1, max_length=1024, repr=False)
    new_password: str = Field(min_length=14, max_length=1024, repr=False)


class AccountOutput(BaseModel):
    id: UUID
    login: str | None
    display_name: str
    active: bool
    roles: list[str]
    must_change_password: bool
    created_at: datetime


class PasswordChanged(BaseModel):
    status: Literal["password_changed"]


class AccessEntry(StrictInput):
    user_id: UUID
    permissions: list[str] = Field(max_length=6)


class AccessInput(StrictInput):
    entries: list[AccessEntry] = Field(max_length=1000)


class AuditOutput(BaseModel):
    id: UUID
    created_at: datetime
    actor_id: UUID | None
    action: str
    object_type: str
    object_id: UUID | None
    details: dict
    request_id: str


class AuditPage(BaseModel):
    items: list[AuditOutput]
    total: int
    page: int
    page_size: int


def audit(session, request, actor, action, object_id, details=None, object_type="user"):
    record_audit(
        session,
        AuditInput(
            actor.id,
            action,
            object_type,
            object_id,
            details or {},
            request.state.request_id,
            source_address(request),
        ),
    )


@router.get("/admin/users", response_model=list[AccountOutput])
def list_accounts(actor=Depends(current_admin), session=Depends(db_session, scope="function")):
    return [
        account_service.serialize_user(session, user)
        for user in session.scalars(select(User).order_by(User.display_name, User.id))
    ]


@router.post("/admin/users", status_code=201, response_model=AccountOutput)
def create_account(
    data: AccountCreate,
    request: Request,
    actor=Depends(current_admin),
    session=Depends(db_session, scope="function"),
):
    user = account_service.create_account(session, **data.model_dump())
    audit(session, request, actor, "identity.user_created", user.id, {"role_codes": data.roles})
    return account_service.serialize_user(session, user)


@router.patch("/admin/users/{user_id}", response_model=AccountOutput)
def update_account(
    user_id: UUID,
    data: AccountUpdate,
    request: Request,
    actor=Depends(current_admin),
    session=Depends(db_session, scope="function"),
):
    user = account_service.update_account(session, user_id, data.model_dump(exclude_unset=True))
    audit(
        session,
        request,
        actor,
        "identity.user_updated",
        user.id,
        {"role_codes": account_service.user_roles(session, user.id)},
    )
    return account_service.serialize_user(session, user)


@router.post("/admin/users/{user_id}/password", status_code=204)
def reset_password(
    user_id: UUID,
    data: PasswordReset,
    request: Request,
    actor=Depends(current_admin),
    session=Depends(db_session, scope="function"),
):
    account_service.reset_password(session, user_id, data.password)
    audit(session, request, actor, "identity.password_reset", user_id)
    return Response(status_code=204)


@router.post("/users/me/password", response_model=PasswordChanged)
def change_password(
    data: PasswordChange,
    request: Request,
    response: Response,
    actor=Depends(current_actor),
    session=Depends(db_session, scope="function"),
):
    account_service.change_password(session, actor.id, data.current_password, data.new_password)
    issued = issue_session(session, actor.id, datetime.now(UTC))
    audit(session, request, actor, "identity.password_changed", actor.id)
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
    return {"status": "password_changed"}


def registry_exists(session, registry_id, *, lock=False):
    query = select(Registry.id).where(Registry.id == registry_id)
    if lock:
        query = query.with_for_update()
    if session.scalar(query) is None:
        fail(404, "registry_not_found", "Реестр не найден")


@router.get("/admin/access/{registry_id}", response_model=AccessInput)
def get_access(
    registry_id: UUID, actor=Depends(current_admin), session=Depends(db_session, scope="function")
):
    registry_exists(session, registry_id)
    return {
        "entries": [
            {"user_id": row.user_id, "permissions": sorted(row.permissions)}
            for row in session.scalars(
                select(RegistryAccess)
                .where(RegistryAccess.registry_id == registry_id)
                .order_by(RegistryAccess.user_id)
            )
            if row.permissions
        ]
    }


@router.put("/admin/access/{registry_id}", response_model=AccessInput)
def set_access(
    registry_id: UUID,
    data: AccessInput,
    request: Request,
    actor=Depends(current_admin),
    session=Depends(db_session, scope="function"),
):
    registry_exists(session, registry_id, lock=True)
    users = {entry.user_id for entry in data.entries}
    if len(users) != len(data.entries):
        fail(422, "duplicate_access_user", "Пользователь указан несколько раз")
    known_users = set(session.scalars(select(User.id).where(User.id.in_(users))))
    if users != known_users or any(set(entry.permissions) - ACTIONS for entry in data.entries):
        fail(422, "invalid_access", "Проверьте пользователей и разрешения")
    for existing in session.scalars(
        select(RegistryAccess).where(RegistryAccess.registry_id == registry_id)
    ):
        existing.permissions = []
    for entry in data.entries:
        row = session.get(RegistryAccess, (registry_id, entry.user_id))
        if row is None:
            row = RegistryAccess(registry_id=registry_id, user_id=entry.user_id)
            session.add(row)
        row.permissions = sorted(set(entry.permissions))
    audit(
        session,
        request,
        actor,
        "identity.registry_access_changed",
        registry_id,
        object_type="registry",
    )
    session.flush()
    return get_access(registry_id, actor, session)


@router.get("/admin/audit", response_model=AuditPage)
def list_audit(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    actor=Depends(current_admin),
    session=Depends(db_session, scope="function"),
):
    rows = session.scalars(
        select(AuditEvent)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [
            {
                "id": row.id,
                "created_at": row.created_at,
                "actor_id": row.actor_id,
                "action": row.action,
                "object_type": row.object_type,
                "object_id": row.object_id,
                "details": {
                    key: value
                    for key, value in row.details.items()
                    if key in {"reason_code", "role_codes"}
                },
                "request_id": row.request_id,
            }
            for row in rows
        ],
        "total": session.scalar(select(func.count()).select_from(AuditEvent)),
        "page": page,
        "page_size": page_size,
    }
