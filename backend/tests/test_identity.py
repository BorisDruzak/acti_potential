from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.identity.models import AuthSession, LocalCredential, User, UserRole

PASSWORD = "synthetic-password-only-for-tests"


def test_credentials_sessions_roles_and_revocation(factory):
    from app.identity.bootstrap import bootstrap_admin
    from app.identity.policy import PermissionDenied, require_role
    from app.identity.service import AuthenticationError, authenticate_local
    from app.identity.sessions import issue_session, resolve_actor, revoke_session

    now = datetime.now(UTC)
    with factory.begin() as db:
        uid = bootstrap_admin(db, " Admin ", "Administrator", PASSWORD)
        credential = db.get(LocalCredential, uid)
        assert credential.password_hash.startswith("$argon2id$")
        assert PASSWORD not in credential.password_hash
        assert authenticate_local(db, "ADMIN", PASSWORD).id == uid
        issued = issue_session(db, uid, now)
        assert issued.expires_at - now == timedelta(hours=12)
        assert db.scalar(select(AuthSession)).token_hash != issued.token
    with factory.begin() as db:
        actor = resolve_actor(db, issued.token, now)
        require_role(actor, "admin")
        with pytest.raises(PermissionDenied):
            require_role(actor, "unknown")
        db.execute(delete(UserRole).where(UserRole.user_id == uid))
    with factory.begin() as db:
        assert resolve_actor(db, issued.token, now).roles == frozenset()
        db.get(User, uid).active = False
    with factory.begin() as db:
        with pytest.raises(AuthenticationError):
            resolve_actor(db, issued.token, now)
        db.get(User, uid).active = True
        db.flush()
        revoke_session(db, issued.token)
    with factory.begin() as db:
        with pytest.raises(AuthenticationError):
            resolve_actor(db, issued.token, now)


def test_generic_credentials_errors_and_expiration(factory):
    from app.identity.bootstrap import bootstrap_admin
    from app.identity.service import AuthenticationError, authenticate_local
    from app.identity.sessions import issue_session, resolve_actor

    now = datetime.now(UTC)
    with factory.begin() as db:
        uid = bootstrap_admin(db, "admin", "Admin", PASSWORD)
        session = issue_session(db, uid, now)
    errors = []
    with factory.begin() as db:
        for login, password in [("missing", PASSWORD), ("admin", "wrong")]:
            with pytest.raises(AuthenticationError) as error:
                authenticate_local(db, login, password)
            errors.append(str(error.value))
        db.get(User, uid).active = False
        db.flush()
        with pytest.raises(AuthenticationError) as error:
            authenticate_local(db, "admin", PASSWORD)
        assert errors == [str(error.value)] * 2
        with pytest.raises(AuthenticationError):
            resolve_actor(db, session.token, now + timedelta(hours=12))


def test_password_validation(factory):
    from app.identity.bootstrap import bootstrap_admin

    with factory.begin() as db:
        with pytest.raises(ValueError):
            bootstrap_admin(db, "admin", "Admin", "short")
