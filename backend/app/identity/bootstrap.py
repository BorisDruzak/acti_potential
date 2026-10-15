from getpass import getpass
from uuid import UUID

from sqlalchemy import select, text

from app.identity.credentials import hash_password, normalize_login
from app.identity.models import LocalCredential, Role, User, UserRole
from app.platform.audit import AuditInput, record_audit
from app.platform.outbox import enqueue_event


class BootstrapExists(Exception):
    pass


def bootstrap_admin(session, login: str, display_name: str, password: str) -> UUID:
    login = normalize_login(login)
    if not 1 <= len(display_name.strip()) <= 200:
        raise ValueError("Invalid display name")
    password_hash = hash_password(password)
    session.execute(text("SELECT pg_advisory_xact_lock(731002)"))
    if session.scalar(select(User.id).limit(1)) is not None:
        raise BootstrapExists("Initial setup has already been completed")
    user = User(display_name=display_name.strip())
    session.add(user)
    session.flush()
    session.add(LocalCredential(user_id=user.id, login=login, password_hash=password_hash))
    role = session.scalar(select(Role).where(Role.code == "admin"))
    session.add(UserRole(user_id=user.id, role_id=role.id))
    session.flush()
    from uuid import uuid4

    record_audit(
        session,
        AuditInput(
            user.id,
            "identity.bootstrap",
            "user",
            user.id,
            {"role_codes": ["admin"]},
            uuid4().hex,
            "bootstrap-cli",
        ),
    )
    enqueue_event(session, "platform.admin_created", user.id, {"user_id": str(user.id)})
    return user.id


if __name__ == "__main__":
    from sqlalchemy.orm import sessionmaker

    from app.core.config import Settings
    from app.db.session import build_engine

    login = input("Administrator login: ")
    name = input("Display name: ")
    password = getpass("Password (14+ characters): ")
    if password != getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    try:
        with sessionmaker(build_engine(Settings())).begin() as db:
            bootstrap_admin(db, login, name, password)
    except BootstrapExists:
        raise SystemExit("Initial setup has already been completed") from None
    print("Administrator created")
