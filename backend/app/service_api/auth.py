import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.api.dependencies import db_session
from app.api.errors import fail
from app.identity.models import User
from app.identity.policy import Actor
from app.service_api.models import ServiceAccount, ServiceToken

bearer = HTTPBearer(auto_error=False, scheme_name="ServiceBearer")


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class ServiceContext:
    actor: Actor
    account: ServiceAccount
    token: ServiceToken


def current_service(
    request: Request,
    credential: HTTPAuthorizationCredentials | None = Depends(bearer),
    session=Depends(db_session, scope="function"),
):
    if (
        credential is None
        or credential.scheme.lower() != "bearer"
        or len(credential.credentials) > 500
    ):
        fail(401, "service_unauthorized", "Сервисный токен не принят")
    row = session.scalar(
        select(ServiceToken).where(ServiceToken.token_hash == token_hash(credential.credentials))
    )
    now = datetime.now(UTC)
    account = session.get(ServiceAccount, row.account_id) if row else None
    user = session.get(User, account.user_id) if account else None
    if (
        row is None
        or row.revoked_at is not None
        or row.expires_at <= now
        or account.state != "active"
        or not user.active
    ):
        fail(401, "service_unauthorized", "Сервисный токен не принят")
    registries = frozenset(str(value) for value in row.registry_ids) & frozenset(
        str(value) for value in account.registry_ids
    )
    operations = frozenset(row.operations) & frozenset(account.operations)
    actor = Actor(
        user.id,
        user.display_name,
        frozenset({"viewer"}),
        scoped_registry_ids=registries,
        scoped_operations=operations,
    )
    request.state.service_context = ServiceContext(actor, account, row)
    return request.state.service_context


def operation(context, name):
    if name not in context.actor.scoped_operations:
        fail(403, "service_scope_forbidden", "Операция не входит в область токена")
