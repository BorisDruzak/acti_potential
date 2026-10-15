"""Lifecycle, receipt idempotency, scoped diagnostics and published service contract."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select, text

from app.identity.models import LocalCredential, User
from app.jobs.worker import CONSUMER, process_batch, process_event
from app.platform.models import AuditEvent, OutboxEvent, ProcessedEvent
from app.registry.models import RegistryAccess
from app.service_api.auth import token_hash
from app.service_api.models import ServiceEventCursor, ServiceToken
from tests.test_content_security import sign_in_as
from tests.test_task010_service_acl import (
    SERVICE_PREFIX,
    ServiceHeaders,
    service_get,
    service_sample,
)


def mint(client, headers, account, **scopes):
    response = client.post(
        f"/api/v1/admin/service-accounts/{account['id']}/tokens",
        headers=headers,
        json={"expires_in_days": 1, "reason": "Synthetic lifecycle test", **scopes},
    )
    assert response.status_code == 201
    return response.json()


def change(client, headers, account, **patch):
    body = {key: account[key] for key in ("name", "registry_ids", "operations", "state")}
    body.update(patch, reason="Synthetic lifecycle test")
    response = client.patch(
        f"/api/v1/admin/service-accounts/{account['id']}", headers=headers, json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_hashed_tokens_noninteractive_identity_rotation_revoke_and_secret_delivery(client, factory):
    setup, account, bearer = service_sample(client, factory)
    secret = bearer["Authorization"].split(" ")[1]
    admin = sign_in_as(client, factory, setup["sample"]["admin"])
    with factory() as db:
        row = db.scalar(select(ServiceToken))
        identity = row.id
        assert row.token_hash == token_hash(secret) and secret != row.token_hash
        assert db.get(LocalCredential, UUID(account["user_id"])) is None
    listed = client.get(f"/api/v1/admin/service-accounts/{account['id']}")
    assert secret not in listed.text and "token_hash" not in listed.text
    assert listed.headers["cache-control"] == "no-store"
    users = client.get("/api/v1/admin/users").json()
    service = next(row for row in users if row["id"] == account["user_id"])
    assert service["kind"] == "service" and service["login"] is None
    assert (
        client.patch(
            f"/api/v1/admin/users/{account['user_id']}", headers=admin, json={"roles": ["admin"]}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/admin/users/{account['user_id']}/password",
            headers=admin,
            json={"password": "SyntheticOnlyPassword_123"},
        ).status_code
        == 403
    )
    rotated = client.post(
        f"/api/v1/admin/service-accounts/{account['id']}/tokens/{identity}/rotate",
        headers=admin,
        json={"expires_in_days": 1, "reason": "Synthetic rotation"},
    )
    assert rotated.status_code == 201
    fresh = ServiceHeaders(Authorization="Bearer " + rotated.json()["token"])
    service_get(client, bearer, f"/documents/{setup['source']['id']}", 401)
    service_get(client, fresh, f"/documents/{setup['source']['id']}")
    assert (
        client.post(
            f"/api/v1/admin/service-accounts/{account['id']}/tokens/{rotated.json()['id']}/revoke",
            headers=admin,
            json={"reason": "Synthetic revoke"},
        ).status_code
        == 204
    )
    service_get(client, fresh, f"/documents/{setup['source']['id']}", 401)
    with factory() as db:
        assert all(secret not in json.dumps(row.details) for row in db.scalars(select(AuditEvent)))


def test_account_disable_and_direct_identity_disable_never_revive_old_tokens(client, factory):
    setup, account, bearer = service_sample(client, factory)
    headers = sign_in_as(client, factory, setup["sample"]["admin"])
    change(client, headers, account, state="disabled")
    service_get(client, bearer, "/events", 401)
    change(client, headers, account, state="active")
    service_get(client, bearer, "/events", 401)
    issued = mint(client, headers, account)
    fresh = ServiceHeaders(Authorization="Bearer " + issued["token"])
    service_get(client, fresh, f"/documents/{setup['source']['id']}")
    with factory.begin() as db:
        db.get(User, UUID(account["user_id"])).active = False
    service_get(client, fresh, "/events", 401)
    with factory.begin() as db:
        db.get(User, UUID(account["user_id"])).active = True
    service_get(client, fresh, "/events", 401)


def test_expiry_scope_snapshot_ttl_and_input_validation(client, factory):
    setup, account, bearer = service_sample(client, factory, operations=["documents"])
    headers = sign_in_as(client, factory, setup["sample"]["admin"])
    expanded = change(client, headers, account, operations=["documents", "content"])
    service_get(client, bearer, f"/blocks/{setup['extraction']['blocks'][0]['id']}", 403)
    assert (
        client.patch(
            f"/api/v1/admin/service-accounts/{account['id']}",
            headers=headers,
            json={
                **{key: expanded[key] for key in ("registry_ids", "operations", "state")},
                "name": "   ",
                "reason": "Synthetic",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/admin/service-accounts/{account['id']}/tokens",
            headers=headers,
            json={"expires_in_days": 91, "reason": "Synthetic"},
        ).status_code
        == 422
    )
    # Clock injection affects only the authenticator's imported clock, not stored receipt facts.
    from unittest.mock import patch

    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(days=3)

    with patch("app.service_api.auth.datetime", Future):
        response = service_get(client, bearer, f"/documents/{setup['source']['id']}", 401)
        assert response.headers["www-authenticate"] == "Bearer"


def test_cursor_token_binding_replay_ttl_cleanup_and_current_authorization(client, factory):
    setup, account, bearer = service_sample(client, factory)
    process_batch(factory, 100)
    first = service_get(client, bearer, "/events?page_size=1").json()
    cursor = first["next_cursor"]
    page = service_get(client, bearer, f"/events?cursor={cursor}&page_size=2").json()
    replay = service_get(client, bearer, f"/events?cursor={cursor}&page_size=2").json()
    assert page["items"] == replay["items"]
    assert page["next_cursor"] != replay["next_cursor"]
    headers = sign_in_as(client, factory, setup["sample"]["admin"])
    second = mint(client, headers, account)
    service_get(
        client,
        ServiceHeaders(Authorization="Bearer " + second["token"]),
        f"/events?cursor={cursor}",
        403,
    )
    with factory.begin() as db:
        db.get(ServiceEventCursor, UUID(cursor)).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    service_get(client, bearer, f"/events?cursor={cursor}", 403)
    from app.service_api.events import cleanup_cursors

    cleanup_cursors(factory)
    with factory() as db:
        assert db.get(ServiceEventCursor, UUID(cursor)) is None


def test_scoped_queue_retry_preserves_worker_receipt_and_rejects_unknown_payload_version(
    client, factory
):
    setup, _, _ = service_sample(client, factory)
    headers = sign_in_as(client, factory, setup["sample"]["admin"])
    with factory.begin() as db:
        event = OutboxEvent(
            event_type="document.metadata.updated",
            object_id=UUID(setup["source"]["id"]),
            payload={"registry_id": str(setup["sample"]["registry"]), "revision": 1},
            state="failed",
            attempts=5,
            error_code="handler_failed",
        )
        db.add(event)
        db.flush()
        identity = event.id
        process_event(db, event)
    queue = client.get("/api/v1/admin/operations/queue?state=failed").json()
    assert identity.hex.replace("-", "") in json.dumps(queue).replace("-", "")
    retry = client.post(
        f"/api/v1/admin/operations/queue/{identity}/retry",
        headers=headers,
        json={"reason": "Synthetic diagnosed retry"},
    )
    assert retry.status_code == 200 and retry.json()["attempts"] == 0
    process_batch(factory, 100)
    with factory() as db:
        assert db.get(OutboxEvent, identity).state == "processed"
        assert db.get(ProcessedEvent, (CONSUMER, identity)) is not None
    with factory.begin() as db:
        unsupported = OutboxEvent(
            event_type="document.created",
            object_id=UUID(setup["source"]["id"]),
            payload={},
            payload_version=2,
        )
        db.add(unsupported)
        db.flush()
        with pytest.raises(ValueError, match="payload version"):
            process_event(db, unsupported)
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["admin"].id)
        ).permissions = []
        db.get(OutboxEvent, identity).state = "failed"
    hidden = client.get("/api/v1/admin/operations/queue?state=failed").json()
    assert str(identity) not in json.dumps(hidden)
    assert (
        client.post(
            f"/api/v1/admin/operations/queue/{identity}/retry",
            headers=headers,
            json={"reason": "Synthetic forbidden retry"},
        ).status_code
        == 404
    )


def test_aggregate_metrics_and_published_form_event_scope(client, factory):
    setup, account, bearer = service_sample(client, factory)
    headers = sign_in_as(client, factory, setup["sample"]["admin"])
    registry = str(setup["sample"]["registry"])
    for operation, duration in (("search", 12), ("time_to_open", 45)):
        assert (
            client.post(
                "/api/v1/usage/retrieval",
                headers=headers,
                json={"registry_id": registry, "operation": operation, "duration_ms": duration},
            ).status_code
            == 204
        )
    metrics = client.get(f"/api/v1/admin/operations/metrics?registry_id={registry}")
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["retrieval"]["time_to_open_ms_total"] == 45
    assert metrics.json()["content"]["documents"] >= 1
    assert not {"query", "ip", "actor_id", "user_id"} & set(metrics.json())
    form_id = setup["source"]["schema_version_id"]
    form = service_get(client, bearer, f"/form-schemas/{form_id}").json()
    assert form["state"] == "published"
    with factory.begin() as db:
        event = OutboxEvent(
            event_type="form.schema.published",
            object_id=UUID(form_id),
            payload={"schema_version_id": form_id},
        )
        db.add(event)
    process_batch(factory, 100)
    from tests.test_task010_service_acl import collect_events

    assert any(
        item["object"]["type"] == "form_schema" and item["object"]["id"] == form_id
        for item in collect_events(client, bearer)
    )
    with factory.begin() as db:
        for grant in db.scalars(
            select(RegistryAccess).where(RegistryAccess.user_id == UUID(account["user_id"]))
        ):
            grant.permissions = []
    service_get(client, bearer, f"/form-schemas/{form_id}", 404)
    assert not collect_events(client, bearer)


def test_token_receipt_is_immutable_in_postgresql(client, factory):
    service_sample(client, factory)
    from sqlalchemy.exc import DBAPIError

    with factory() as db:
        identity = db.scalar(select(ServiceToken.id))
    with pytest.raises(DBAPIError), factory.begin() as db:
        db.execute(
            text("UPDATE service_tokens SET operations='[]'::jsonb WHERE id=:id"), {"id": identity}
        )


def test_concurrent_rotation_serializes_single_successor(client, factory):
    setup, account, bearer = service_sample(client, factory)
    headers = sign_in_as(client, factory, setup["sample"]["admin"])
    with factory() as db:
        identity = db.scalar(select(ServiceToken.id))

    def rotate(_):
        return client.post(
            f"/api/v1/admin/service-accounts/{account['id']}/tokens/{identity}/rotate",
            headers=headers,
            json={"expires_in_days": 1, "reason": "Synthetic concurrent rotation"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(rotate, range(2))) == [201, 409]
    service_get(client, bearer, "/events", 401)
    with factory() as db:
        assert (
            len(list(db.scalars(select(ServiceToken).where(ServiceToken.revoked_at.is_(None)))))
            == 1
        )


def test_canonical_openapi_is_typed_versioned_and_matches_checked_export():
    from app.service_api.api import schema

    document = schema()
    checked = Path(__file__).parents[1] / "contracts" / "knowledge-service-v1.openapi.json"
    assert json.loads(checked.read_text(encoding="utf-8")) == document
    assert document["info"]["version"] == "1.0.0" and "servers" not in document
    assert document["components"]["securitySchemes"]["ServiceBearer"]["scheme"] == "bearer"
    for path, methods in document["paths"].items():
        assert path.startswith(SERVICE_PREFIX + "/")
        for method, operation in methods.items():
            assert method == "get" and operation["security"] == [{"ServiceBearer": []}]
            content = operation["responses"]["200"]["content"]
            if path.endswith("/download"):
                assert content["application/octet-stream"]["schema"] == {
                    "type": "string",
                    "format": "binary",
                }
            else:
                assert content["application/json"]["schema"]
            assert "404" in operation["responses"]
