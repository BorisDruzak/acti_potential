from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.platform.models import AuditEvent
from tests.test_account_admin import admin_headers, create_user, headers, login


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_activity_is_private_paginated_and_has_no_audit_details(client, factory, role):
    security = admin_headers(client, factory)
    other_id = UUID(client.get("/api/v1/users/me").json()["id"])
    created = create_user(client, security, roles=[role])
    user_id = UUID(created.json()["id"])
    assert login(client).status_code == 200
    assert client.post("/api/v1/users/me/password", json={
        "current_password": "synthetic-temporary-password-only",
        "new_password": "synthetic-activity-password-only",
    }, headers=headers(client)).status_code == 200
    with factory.begin() as db:
        for action, actor, day in [
            ("document.created", user_id, 10),
            ("document.updated", user_id, 11),
            ("document.created", None, 12),
            ("document.created", other_id, 13),
            ("number.assigned", user_id, 14),
            ("registry.updated", user_id, 15),
        ]:
            db.add(AuditEvent(actor_id=actor, action=action, object_type="document",
                              object_id=uuid4(), created_at=datetime(2026, 10, day, tzinfo=UTC),
                              details={"private": "must not be returned"},
                              request_id="private-request", request_source="private-source"))
    url = "/api/v1/users/me/activity?category=documents&page_size=1"
    first = client.get(url)
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 2
    assert first.json()["items"][0]["action"] == "document.updated"
    assert set(first.json()["items"][0]) == {"id", "created_at", "action", "object_type"}
    second = client.get(url + "&page=2").json()
    assert second["items"][0]["action"] == "document.created"
    assert first.json()["items"][0]["id"] != second["items"][0]["id"]
    filtered = client.get(url + "&since=2026-10-11T00:00:00Z&until=2026-10-12T00:00:00Z")
    assert filtered.json()["total"] == 1
    # Supplying another actor must never widen the server-owned scope.
    assert client.get(url + "&actor_id=" + str(uuid4())).json()["total"] == 2
    assert "private" not in first.text
    own = client.get("/api/v1/users/me/activity?page_size=100").json()
    assert sum(row["action"].startswith("document.") for row in own["items"]) == 2
    assert client.get("/api/v1/users/me/activity?category=numbers").json()["total"] == 1
    assert client.get("/api/v1/users/me/activity?category=other").json()["total"] == 1
    assert client.get("/api/v1/users/me/activity?category=account").json()["total"] >= 2
    assert client.get("/api/v1/admin/audit").status_code == 403


def test_activity_requires_auth_and_validates_filters(client, factory):
    assert client.get("/api/v1/users/me/activity").status_code == 401
    admin_headers(client, factory)
    for query in ["page=0", "page_size=101", "category=unknown",
                  "since=2026-10-10T00:00:00", "until=2026-10-10T00:00:00",
                  "since=2026-10-12T00:00:00Z&until=2026-10-10T00:00:00Z"]:
        assert client.get("/api/v1/users/me/activity?" + query).status_code == 422
