"""Password rotation must serialize with a login using the previous credential."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event
from time import monotonic

import pytest
from argon2 import PasswordHasher

from app.identity.account_service import change_password
from app.identity.bootstrap import bootstrap_admin
from app.identity.models import LocalCredential
from app.identity.service import AuthenticationError, authenticate_local
from app.identity.sessions import issue_session, resolve_actor

OLD_PASSWORD = "synthetic-review-old-password"
NEW_PASSWORD = "synthetic-review-new-password"


@pytest.mark.parametrize("needs_rehash", [False, True])
def test_password_change_cannot_leave_a_concurrent_old_password_session(
    factory, monkeypatch, needs_rehash
):
    from app.identity import service

    with factory.begin() as db:
        uid = bootstrap_admin(db, "review-admin", "Review administrator", OLD_PASSWORD)
        if needs_rehash:
            db.get(LocalCredential, uid).password_hash = PasswordHasher(time_cost=1).hash(
                OLD_PASSWORD
            )

    verified = Event()
    real_hasher = service.hasher

    class ObservedHasher:
        def verify(self, hashed, password):
            result = real_hasher.verify(hashed, password)
            verified.set()
            return result

        def check_needs_rehash(self, hashed):
            return real_hasher.check_needs_rehash(hashed)

        def hash(self, password):
            return real_hasher.hash(password)

    monkeypatch.setattr(service, "hasher", ObservedHasher())

    def old_password_login():
        try:
            with factory.begin() as db:
                user = authenticate_local(db, "review-admin", OLD_PASSWORD)
                return issue_session(db, user.id, datetime.now(UTC))
        except AuthenticationError:
            return None

    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory.begin() as changing:
            # The password and session revocation are flushed but not committed.
            # Verification sees the old hash; session issuance must wait for
            # this transaction and reject the now-obsolete credential.
            change_password(changing, uid, OLD_PASSWORD, NEW_PASSWORD)
            pending = pool.submit(old_password_login)
            assert verified.wait(5), "Concurrent old-password verification did not complete"
        issued = pending.result(timeout=10)

    if issued is not None:
        with factory.begin() as db:
            with pytest.raises(AuthenticationError):
                resolve_actor(db, issued.token, datetime.now(UTC))
    with factory.begin() as db:
        with pytest.raises(AuthenticationError):
            authenticate_local(db, "review-admin", OLD_PASSWORD)
        assert authenticate_local(db, "review-admin", NEW_PASSWORD).id == uid


def test_unrelated_logins_verify_passwords_concurrently(factory, monkeypatch):
    from app.identity import service
    from app.identity.account_service import create_account

    with factory.begin() as db:
        bootstrap_admin(db, "review-admin", "Review administrator", OLD_PASSWORD)
        create_account(
            db,
            login="review-editor",
            display_name="Review editor",
            password=OLD_PASSWORD,
            roles=["editor"],
        )

    verification = Barrier(2, timeout=5)
    real_hasher = service.hasher

    class ConcurrentHasher:
        def verify(self, hashed, password):
            verification.wait()
            return real_hasher.verify(hashed, password)

        def check_needs_rehash(self, hashed):
            return real_hasher.check_needs_rehash(hashed)

        def hash(self, password):
            return real_hasher.hash(password)

    monkeypatch.setattr(service, "hasher", ConcurrentHasher())

    def login(name):
        with factory.begin() as db:
            user = authenticate_local(db, name, OLD_PASSWORD)
            return issue_session(db, user.id, datetime.now(UTC))

    started = monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(login, ("review-admin", "review-editor")))
    assert len(results) == 2
    print(f"Concurrent real password verification and session issuance: {monotonic() - started:.3f}s")
