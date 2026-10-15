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
    try:
        if len(password) > 1024:
            raise AuthenticationError()
        hasher.verify(credential.password_hash if credential else dummy_hash, password)
    except (VerificationError, InvalidHashError):
        raise AuthenticationError() from None
    user = session.get(User, credential.user_id) if credential else None
    if user is None or not user.active:
        raise AuthenticationError()
    if hasher.check_needs_rehash(credential.password_hash):
        credential.password_hash = hasher.hash(password)
    return user
