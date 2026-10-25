from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select

from app.identity.credentials import dummy_hash, hasher, normalize_login
from app.identity.models import LocalCredential, User


class AuthenticationError(Exception):
    def __init__(self):
        super().__init__("Не удалось выполнить вход")


def authenticate_local(session, login: str, password: str) -> User:
    try:
        login = normalize_login(login)
    except ValueError:
        raise AuthenticationError() from None
    credential = session.scalar(select(LocalCredential).where(LocalCredential.login == login))
    verified_hash = credential.password_hash if credential else dummy_hash
    verified_user_id = credential.user_id if credential else None
    try:
        if len(password) > 1024:
            raise AuthenticationError()
        hasher.verify(verified_hash, password)
    except (VerificationError, InvalidHashError):
        raise AuthenticationError() from None
    if verified_user_id is None:
        raise AuthenticationError()
    replacement_hash = hasher.hash(password) if hasher.check_needs_rehash(verified_hash) else None

    from app.identity.account_service import lock_accounts

    # Keep expensive verification/rehashing concurrent. After waiting for account
    # changes, accept only the exact credential verified above; the same transaction
    # retains this lock through session issuance so rotation cannot miss the session.
    lock_accounts(session)
    credential = session.scalar(select(LocalCredential).where(LocalCredential.login == login))
    if (
        credential is None
        or credential.user_id != verified_user_id
        or credential.password_hash != verified_hash
    ):
        raise AuthenticationError()
    user = session.get(User, credential.user_id)
    if user is None or not user.active:
        raise AuthenticationError()
    if replacement_hash is not None:
        credential.password_hash = replacement_hash
    return user
