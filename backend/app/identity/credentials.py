import secrets
import unicodedata

from argon2 import PasswordHasher

hasher = PasswordHasher()
dummy_hash = hasher.hash(secrets.token_urlsafe(32))


def normalize_login(login: str) -> str:
    normalized = unicodedata.normalize("NFKC", login).strip().casefold()
    if not 1 <= len(normalized) <= 128 or any(ord(c) < 32 for c in normalized):
        raise ValueError("Invalid login")
    return normalized


def hash_password(password: str) -> str:
    if not 14 <= len(password) <= 1024:
        raise ValueError("Password must contain between 14 and 1024 characters")
    return hasher.hash(password)
