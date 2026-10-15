from datetime import UTC, datetime
from uuid import UUID

from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select, text, update

from app.api.errors import fail
from app.identity.credentials import hash_password, hasher, normalize_login
from app.identity.models import AuthSession, LocalCredential, Role, User, UserRole
from app.identity.service import AuthenticationError

ROLE_CODES = frozenset({"admin", "user", "editor", "viewer"})


def lock_accounts(session):
    # All account changes share a lock, including concurrent last-admin changes.
    session.execute(text("SELECT pg_advisory_xact_lock(731003)"))
    # Authentication may have loaded credentials before waiting for this lock.
    # Re-read them so a concurrent password change cannot reuse an old hash.
    session.expire_all()


def get_user(session, user_id: UUID):
    user = session.get(User, user_id)
    if user is None:
        fail(404, "user_not_found", "Пользователь не найден")
    return user


def user_roles(session, user_id):
    return sorted(
        session.scalars(select(Role.code).join(UserRole).where(UserRole.user_id == user_id))
    )


def serialize_user(session, user):
    credential = session.get(LocalCredential, user.id)
    return {
        "id": user.id,
        "display_name": user.display_name,
        "active": user.active,
        "created_at": user.created_at,
        "login": credential.login if credential else None,
        "roles": user_roles(session, user.id),
        "must_change_password": bool(credential and credential.must_change_password),
    }


def validate_roles(session, codes):
    if not codes or set(codes) - ROLE_CODES or len(set(codes)) != len(codes):
        fail(422, "invalid_roles", "Выберите допустимые роли пользователя")
    rows = list(session.scalars(select(Role).where(Role.code.in_(codes))))
    if len(rows) != len(codes):
        fail(422, "invalid_roles", "Выберите допустимые роли пользователя")
    return rows


def normalized_login(value):
    try:
        return normalize_login(value)
    except ValueError:
        fail(422, "invalid_login", "Неверное имя для входа")


def checked_password(password):
    try:
        return hash_password(password)
    except ValueError:
        fail(422, "invalid_password", "Пароль должен содержать от 14 до 1024 символов")


def ensure_unique_login(session, login, user_id=None):
    exists = session.scalar(select(LocalCredential.user_id).where(LocalCredential.login == login))
    if exists is not None and exists != user_id:
        fail(409, "login_exists", "Это имя для входа уже используется")


def create_account(session, *, login, display_name, password, roles):
    lock_accounts(session)
    login = normalized_login(login)
    ensure_unique_login(session, login)
    assigned = validate_roles(session, roles)
    hashed = checked_password(password)
    user = User(display_name=display_name.strip())
    session.add(user)
    session.flush()
    session.add(
        LocalCredential(
            user_id=user.id, login=login, password_hash=hashed, must_change_password=True
        )
    )
    session.add_all(UserRole(user_id=user.id, role_id=role.id) for role in assigned)
    session.flush()
    return user


def revoke_user_sessions(session, user_id):
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def update_account(session, user_id, changes):
    lock_accounts(session)
    user = get_user(session, user_id)
    existing_roles = user_roles(session, user_id)
    new_roles = changes.get("roles", existing_roles)
    assigned = validate_roles(session, new_roles) if "roles" in changes else None
    if (
        user.active
        and "admin" in existing_roles
        and (not changes.get("active", user.active) or "admin" not in new_roles)
    ):
        count = session.scalar(
            select(func.count())
            .select_from(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.active.is_(True), Role.code == "admin")
        )
        if count <= 1:
            fail(409, "last_admin_required", "Нельзя отключить последнего активного администратора")
    if "login" in changes:
        credential = session.get(LocalCredential, user_id)
        if credential is None:
            fail(409, "local_account_required", "Для пользователя не настроен локальный вход")
        login = normalized_login(changes["login"])
        ensure_unique_login(session, login, user_id)
        credential.login = login
    if "display_name" in changes:
        user.display_name = changes["display_name"].strip()
    if "active" in changes:
        user.active = changes["active"]
        if not user.active:
            revoke_user_sessions(session, user_id)
    if assigned is not None:
        session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        session.add_all(UserRole(user_id=user_id, role_id=role.id) for role in assigned)
    session.flush()
    return user


def reset_password(session, user_id, password):
    lock_accounts(session)
    get_user(session, user_id)
    credential = session.get(LocalCredential, user_id)
    if credential is None:
        fail(409, "local_account_required", "Для пользователя не настроен локальный вход")
    credential.password_hash = checked_password(password)
    credential.must_change_password = True
    revoke_user_sessions(session, user_id)
    session.flush()


def change_password(session, user_id, current_password, new_password):
    lock_accounts(session)
    credential = session.get(LocalCredential, user_id)
    if credential is None:
        raise AuthenticationError()
    try:
        hasher.verify(credential.password_hash, current_password)
    except (VerificationError, InvalidHashError):
        fail(422, "current_password_incorrect", "Текущий пароль указан неверно")
    if current_password == new_password:
        fail(422, "password_unchanged", "Новый пароль должен отличаться от текущего")
    credential.password_hash = checked_password(new_password)
    credential.must_change_password = False
    revoke_user_sessions(session, user_id)
    session.flush()
