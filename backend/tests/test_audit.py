import pytest
from sqlalchemy import func, select

from app.platform.models import AuditEvent, OutboxEvent
from tests.conftest import login_admin


def test_auth_actions_are_audited_without_secrets(client, factory):
    response = login_admin(client, factory)
    assert response.status_code == 200
    me = client.get("/api/v1/users/me").json()
    client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "https://testserver", "X-CSRF-Token": me["csrf_token"]},
    )
    client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": "wrong"},
        headers={"Origin": "https://testserver"},
    )
    with factory.begin() as db:
        events = list(db.scalars(select(AuditEvent)))
        assert {e.action for e in events} >= {
            "identity.bootstrap",
            "auth.login",
            "auth.logout",
            "auth.login_failed",
        }
        assert all(
            "password" not in str(e.details) and "token" not in str(e.details) for e in events
        )
        assert all(e.request_id for e in events)


def test_rollback_removes_audit_and_outbox(factory):
    from app.identity.bootstrap import bootstrap_admin

    with pytest.raises(RuntimeError):
        with factory.begin() as db:
            bootstrap_admin(db, "admin", "Admin", "synthetic-password-for-tests")
            raise RuntimeError("rollback")
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_audit_rejects_sensitive_details(factory):
    from app.platform.audit import AuditInput, record_audit

    with factory.begin() as db:
        with pytest.raises(ValueError):
            record_audit(
                db,
                AuditInput(
                    None, "test", "test", None, {"password": "never-store"}, "request", "test"
                ),
            )
