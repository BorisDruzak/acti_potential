import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from app.identity.models import AuthSession, ExternalIdentity, LocalCredential, Role, User, UserRole
from app.identity.policy import Actor
from app.identity.service import AuthenticationError


def csrf_for_token(token: str) -> str:
    return hashlib.sha256(("acti-csrf:" + token).encode()).hexdigest()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: datetime


def issue_session(session, user_id, now: datetime, *, external_identity_id=None) -> IssuedSession:
    token = secrets.token_urlsafe(32)
    expires = now + timedelta(hours=12)
    session.add(
        AuthSession(
            user_id=user_id, token_hash=token_digest(token), created_at=now, expires_at=expires,
            external_identity_id=external_identity_id,
        )
    )
    session.flush()
    return IssuedSession(token, csrf_for_token(token), expires)


def resolve_actor(session, token: str, now: datetime) -> Actor:
    if len(token) != 43:
        raise AuthenticationError()
    return resolve_actor_by_hash(session, token_digest(token), now)


def resolve_actor_by_hash(session, digest: str, now: datetime) -> Actor:
    """Revalidate an existing session; only server-issued capabilities carry its digest."""
    row = session.scalar(select(AuthSession).where(AuthSession.token_hash == digest))
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise AuthenticationError()
    user = session.get(User, row.user_id)
    if not user or not user.active:
        raise AuthenticationError()
    if row.external_identity_id is not None:
        identity = session.get(ExternalIdentity, row.external_identity_id)
        if not identity or not identity.active or identity.user_id != user.id:
            raise AuthenticationError()
    roles = session.scalars(select(Role.code).join(UserRole).where(UserRole.user_id == user.id))
    credential = session.get(LocalCredential, user.id)
    return Actor(
        user.id, user.display_name, frozenset(roles),
        bool(credential and credential.must_change_password),
    )


def revoke_session(session, token: str) -> None:
    from datetime import UTC

    row = session.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(token)))
    if row is not None:
        row.revoked_at = datetime.now(UTC)
