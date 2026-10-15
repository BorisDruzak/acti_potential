import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select, update

from app.api.account_admin import audit
from app.api.dependencies import current_actor, current_admin, db_session
from app.api.errors import fail
from app.identity.models import Role, User, UserRole
from app.registry.models import Registry, RegistryAccess
from app.registry.policy import permissions_for
from app.service_api.auth import token_hash
from app.service_api.contracts import (
    OPERATIONS,
    AccountInput,
    AccountRead,
    AccountUpdate,
    MintInput,
    Page,
    Reason,
    ScopeOptions,
    TimingInput,
    TokenIssued,
    TokenRead,
)
from app.service_api.models import ApiUsageBucket, ServiceAccount, ServiceToken

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Admin = Annotated[object, Depends(current_admin)]


def delegable(session, actor):
    return {
        row.id: row
        for row in session.scalars(select(Registry).order_by(Registry.name))
        if "read" in permissions_for(session, actor, row.id)
    }


def validate_scopes(session, actor, registries, operations):
    if len(set(registries)) != len(registries) or len(set(operations)) != len(operations):
        fail(422, "service_scope_invalid", "Области токена не должны повторяться")
    allowed = delegable(session, actor)
    if any(identity not in allowed for identity in registries):
        fail(403, "service_scope_forbidden", "Можно делегировать только текущий доступ к реестрам")


def get_account(session, actor, identity, *, lock=False):
    query = select(ServiceAccount).where(ServiceAccount.id == identity)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if (
        row is None
        or row.created_by != actor.id
        and not all(
            "read" in permissions_for(session, actor, UUID(value)) for value in row.registry_ids
        )
    ):
        fail(404, "service_account_not_found", "Сервисная учётная запись не найдена")
    return row


def token_read(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "prefix",
            "created_at",
            "expires_at",
            "revoked_at",
            "registry_ids",
            "operations",
        )
    }


def account_read(session, row):
    return {
        **{
            key: getattr(row, key)
            for key in (
                "id",
                "user_id",
                "name",
                "state",
                "registry_ids",
                "operations",
                "created_at",
            )
        },
        "tokens": [
            token_read(token)
            for token in session.scalars(
                select(ServiceToken)
                .where(ServiceToken.account_id == row.id)
                .order_by(ServiceToken.created_at.desc(), ServiceToken.id)
                .limit(100)
            )
        ],
    }


def apply_access(session, actor, row):
    assigned = set(row.registry_ids)
    for existing in session.scalars(
        select(RegistryAccess).where(RegistryAccess.user_id == row.user_id)
    ):
        if str(existing.registry_id) not in assigned:
            existing.permissions = []
    for identity in row.registry_ids:
        permissions = permissions_for(session, actor, UUID(identity)) & {"read", "download"}
        grant = session.get(RegistryAccess, (UUID(identity), row.user_id))
        if grant:
            grant.permissions = sorted(permissions)
        else:
            session.add(
                RegistryAccess(
                    registry_id=UUID(identity), user_id=row.user_id, permissions=sorted(permissions)
                )
            )


@router.get("/admin/service-accounts/options", response_model=ScopeOptions)
def options(session: Db, actor: Admin):
    return {
        "registries": [
            {
                "id": row.id,
                "name": row.name,
                "permissions": sorted(
                    permissions_for(session, actor, row.id) & {"read", "download"}
                ),
            }
            for row in delegable(session, actor).values()
        ],
        "operations": [{"id": key, "label": value} for key, value in OPERATIONS.items()],
    }


@router.get("/admin/service-accounts", response_model=Page[AccountRead])
def listing(
    session: Db,
    actor: Admin,
    registry_id: UUID | None = None,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    rows = []
    for row in session.scalars(
        select(ServiceAccount).order_by(ServiceAccount.created_at, ServiceAccount.id)
    ):
        if registry_id and str(registry_id) not in row.registry_ids:
            continue
        if row.created_by == actor.id or all(
            "read" in permissions_for(session, actor, UUID(value)) for value in row.registry_ids
        ):
            rows.append(row)
    offset = (page - 1) * page_size
    return {
        "items": [account_read(session, row) for row in rows[offset : offset + page_size]],
        "total": len(rows),
        "page": page,
        "page_size": page_size,
    }


@router.post("/admin/service-accounts", status_code=201, response_model=AccountRead)
def create(data: AccountInput, request: Request, session: Db, actor: Admin):
    validate_scopes(session, actor, data.registry_ids, data.operations)
    if not data.name.strip():
        fail(422, "service_name_invalid", "Укажите название")
    user = User(display_name=data.name.strip())
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.code == "viewer"))
    session.add(UserRole(user_id=user.id, role_id=role.id))
    row = ServiceAccount(
        user_id=user.id,
        name=data.name.strip(),
        registry_ids=[str(value) for value in data.registry_ids],
        operations=list(data.operations),
        created_by=actor.id,
    )
    session.add(row)
    session.flush()
    apply_access(session, actor, row)
    audit(session, request, actor, "service.account.created", row.id, object_type="service_account")
    return account_read(session, row)


@router.patch("/admin/service-accounts/{account_id}", response_model=AccountRead)
def change(account_id: UUID, data: AccountUpdate, request: Request, session: Db, actor: Admin):
    row = get_account(session, actor, account_id, lock=True)
    validate_scopes(session, actor, data.registry_ids, data.operations)
    row.name, row.state = data.name.strip(), data.state
    row.registry_ids, row.operations = (
        [str(value) for value in data.registry_ids],
        list(data.operations),
    )
    user = session.get(User, row.user_id)
    user.display_name, user.active = row.name, data.state == "active"
    if data.state == "disabled":
        session.execute(
            update(ServiceToken)
            .where(ServiceToken.account_id == row.id, ServiceToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
    apply_access(session, actor, row)
    audit(
        session,
        request,
        actor,
        "service.account.changed",
        row.id,
        {"reason": data.reason},
        object_type="service_account",
    )
    return account_read(session, row)


@router.get("/admin/service-accounts/{account_id}", response_model=AccountRead)
def detail(account_id: UUID, session: Db, actor: Admin):
    return account_read(session, get_account(session, actor, account_id))


def issue(session, actor, row, data):
    if row.state != "active" or not session.get(User, row.user_id).active:
        fail(409, "service_account_disabled", "Сервисная учётная запись отключена")
    registries = (
        [str(value) for value in data.registry_ids]
        if data.registry_ids is not None
        else list(row.registry_ids)
    )
    operations = list(data.operations) if data.operations is not None else list(row.operations)
    if set(registries) - set(row.registry_ids) or set(operations) - set(row.operations):
        fail(403, "service_scope_forbidden", "Токен не может расширить область учётной записи")
    validate_scopes(session, actor, [UUID(value) for value in registries], operations)
    identity = uuid4()
    secret = "acti_svc_" + identity.hex + "." + secrets.token_urlsafe(32)
    token = ServiceToken(
        id=identity,
        account_id=row.id,
        token_hash=token_hash(secret),
        prefix="svc_" + identity.hex[:10],
        registry_ids=registries,
        operations=operations,
        expires_at=datetime.now(UTC) + timedelta(days=data.expires_in_days),
    )
    session.add(token)
    session.flush()
    return {**token_read(token), "token": secret}


@router.get("/admin/service-accounts/{account_id}/tokens", response_model=Page[TokenRead])
def tokens(
    account_id: UUID,
    session: Db,
    actor: Admin,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    row = get_account(session, actor, account_id)
    query = select(ServiceToken).where(ServiceToken.account_id == row.id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            token_read(token)
            for token in session.scalars(
                query.order_by(ServiceToken.created_at.desc(), ServiceToken.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post(
    "/admin/service-accounts/{account_id}/tokens", status_code=201, response_model=TokenIssued
)
def mint(account_id: UUID, data: MintInput, request: Request, session: Db, actor: Admin):
    row = get_account(session, actor, account_id, lock=True)
    result = issue(session, actor, row, data)
    audit(
        session,
        request,
        actor,
        "service.token.created",
        row.id,
        {"reason": data.reason},
        object_type="service_account",
    )
    return result


def locked_token(session, account, identity):
    token = session.scalar(
        select(ServiceToken)
        .where(ServiceToken.id == identity, ServiceToken.account_id == account.id)
        .with_for_update()
    )
    if token is None:
        fail(404, "service_token_not_found", "Токен не найден")
    return token


@router.post(
    "/admin/service-accounts/{account_id}/tokens/{token_id}/rotate",
    status_code=201,
    response_model=TokenIssued,
)
def rotate(
    account_id: UUID, token_id: UUID, data: MintInput, request: Request, session: Db, actor: Admin
):
    row = get_account(session, actor, account_id, lock=True)
    token = locked_token(session, row, token_id)
    if token.revoked_at is not None or token.expires_at <= datetime.now(UTC):
        fail(409, "service_token_inactive", "Токен уже отозван или истёк")
    if data.registry_ids is None:
        data.registry_ids = [
            UUID(value) for value in token.registry_ids if value in row.registry_ids
        ]
    if data.operations is None:
        data.operations = [value for value in token.operations if value in row.operations]
    if not data.registry_ids or not data.operations:
        fail(409, "service_scope_empty", "Область прежнего токена больше недоступна")
    result = issue(session, actor, row, data)
    token.revoked_at = datetime.now(UTC)
    audit(
        session,
        request,
        actor,
        "service.token.rotated",
        row.id,
        {"reason": data.reason},
        object_type="service_account",
    )
    return result


@router.post("/admin/service-accounts/{account_id}/tokens/{token_id}/revoke", status_code=204)
def revoke(
    account_id: UUID, token_id: UUID, data: Reason, request: Request, session: Db, actor: Admin
):
    row = get_account(session, actor, account_id, lock=True)
    token = locked_token(session, row, token_id)
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
    audit(
        session,
        request,
        actor,
        "service.token.revoked",
        row.id,
        {"reason": data.reason},
        object_type="service_account",
    )
    return Response(status_code=204)


def usage(session, registry_id, operation, duration_ms):
    from sqlalchemy.dialects.postgresql import insert

    statement = insert(ApiUsageBucket).values(
        day=datetime.now(UTC).date(),
        registry_id=registry_id,
        operation=operation,
        requests=1,
        duration_ms_total=duration_ms,
    )
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_api_usage_bucket",
            set_={
                "requests": ApiUsageBucket.requests + 1,
                "duration_ms_total": ApiUsageBucket.duration_ms_total + duration_ms,
            },
        )
    )


@router.post("/usage/retrieval", status_code=204)
def timing(data: TimingInput, session: Db, actor=Depends(current_actor)):
    if "read" not in permissions_for(session, actor, data.registry_id):
        fail(404, "registry_not_found", "Реестр не найден")
    usage(session, data.registry_id, data.operation, data.duration_ms)
    return Response(status_code=204)
