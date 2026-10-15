"""Service consumers use current ACL for pinned sources, scopes and event pointers."""

from uuid import UUID

from app.jobs.worker import process_batch
from app.registry.models import DocumentACL, RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task007_sources_acl import source_process

SERVICE_PREFIX = "/api/service/v1"


class ServiceHeaders(dict):
    """Keep real HTTP headers usable without leaking synthetic credentials in pytest locals."""

    def __repr__(self):
        return "{'Authorization': 'Bearer [redacted]'}"

    __str__ = __repr__


def service_sample(
    client, factory, *, private=False, operations=None, source_only=False, process_token_only=False
):
    setup = source_process(client, factory, private=private)
    headers = sign_in_as(client, factory, setup["sample"]["admin"])
    scopes = [str(setup["sample"]["registry"])]
    if not source_only:
        scopes.append(str(setup["process_registry"]))
    created = client.post(
        "/api/v1/admin/service-accounts",
        headers=headers,
        json={
            "name": "Synthetic source consumer",
            "registry_ids": scopes,
            "operations": operations or ["documents", "files", "content", "processes", "events"],
        },
    )
    assert created.status_code == 201, created.text
    account = created.json()
    issued = client.post(
        f"/api/v1/admin/service-accounts/{account['id']}/tokens",
        headers=headers,
        json={
            "expires_in_days": 1,
            "reason": "Synthetic consumer ACL",
            **({"registry_ids": [str(setup["process_registry"])]} if process_token_only else {}),
        },
    )
    assert issued.status_code == 201  # Never include the one-time response in diagnostics.
    headers = ServiceHeaders(Authorization=f"Bearer {issued.json()['token']}")
    del issued
    assert process_batch(factory, 1000) > 0
    return setup, account, headers


def service_get(client, headers, path, status=200):
    response = client.get(SERVICE_PREFIX + path, headers=headers)
    assert response.status_code == status, f"Service GET {path}: {response.status_code}"
    return response


def source_paths(setup):
    extraction = setup["extraction"]
    return [
        f"/file-versions/{extraction['version_id']}",
        f"/files/{extraction['file_id']}",
        f"/files/{extraction['file_id']}/download",
        f"/extractions/{extraction['id']}/blocks",
        f"/blocks/{extraction['blocks'][0]['id']}",
    ]


def test_service_private_file_field_hides_exact_versions_blocks_and_source_basis(client, factory):
    setup, _, bearer = service_sample(client, factory, private=True)
    card = service_get(client, bearer, f"/documents/{setup['source']['id']}")
    assert card.json()["files"] == []
    for path in source_paths(setup):
        service_get(client, bearer, path, 404)
    process = service_get(client, bearer, f"/process-versions/{setup['version']['id']}")
    assert process.json()["steps"][0]["sources"][0]["restricted"] is True
    body = process.text + card.text
    for secret in (
        setup["extraction"]["file_id"],
        setup["extraction"]["version_id"],
        setup["extraction"]["blocks"][0]["id"],
        setup["extraction"]["blocks"][0]["text"],
        "source.docx",
    ):
        assert secret not in body


def test_service_download_revocation_closes_cached_source_urls_but_preserves_metadata(
    client, factory
):
    setup, account, bearer = service_sample(client, factory)
    paths = source_paths(setup)
    for path in paths:
        service_get(client, bearer, path)
    assert service_get(client, bearer, paths[2]).content == setup["original"]
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], UUID(account["user_id"]))
        ).permissions = ["read"]
    service_get(client, bearer, f"/documents/{setup['source']['id']}")
    for path in paths:
        service_get(client, bearer, path, 404)
    process = service_get(client, bearer, f"/process-versions/{setup['version']['id']}")
    assert process.json()["steps"][0]["sources"][0]["restricted"] is True


def test_service_document_acl_rechecked_for_old_pointer_even_with_admin_cookie(client, factory):
    setup, _, bearer = service_sample(client, factory)
    for path in source_paths(setup):
        service_get(client, bearer, path)
    # The active cookie belongs to the configuring admin; service requests still use their actor.
    with factory.begin() as db:
        db.add(
            DocumentACL(
                document_id=UUID(setup["source"]["id"]),
                user_id=setup["sample"]["admin"].id,
                permissions=["read", "download"],
            )
        )
    service_get(client, bearer, f"/documents/{setup['source']['id']}", 404)
    for path in source_paths(setup):
        service_get(client, bearer, path, 404)
    version = service_get(client, bearer, f"/process-versions/{setup['version']['id']}")
    assert version.json()["steps"][0]["sources"][0]["restricted"] is True


def test_service_operation_and_registry_scope_cannot_fall_back_to_cookie_authority(client, factory):
    setup, _, bearer = service_sample(client, factory, operations=["documents"], source_only=True)
    service_get(client, bearer, f"/documents/{setup['source']['id']}")
    service_get(client, bearer, f"/blocks/{setup['extraction']['blocks'][0]['id']}", 403)
    service_get(client, bearer, f"/process-versions/{setup['version']['id']}", 403)
    # Existing browser routes do not become a bearer write/admin/session API.
    client.cookies.clear()
    for method, path, payload in (
        ("get", "/api/v1/admin/users", None),
        ("get", "/api/v1/documents/" + setup["source"]["id"], None),
        ("post", "/api/v1/documents", {}),
        ("post", SERVICE_PREFIX + "/documents", {}),
    ):
        response = getattr(client, method)(
            path, headers=bearer, **({"json": payload} if payload is not None else {})
        )
        assert response.status_code in (401, 403, 404, 405)
    assert service_get(client, bearer, f"/documents/{setup['source']['id']}").status_code == 200


def collect_events(client, bearer, cursor=None):
    items = []
    # A bounded polling window: filtered empty pages are never an exhaustion signal.
    for _ in range(8):
        query = "?page_size=2" + (f"&cursor={cursor}" if cursor else "")
        body = service_get(client, bearer, "/events" + query).json()
        assert set(body) == {"items", "next_cursor"}
        items.extend(body["items"])
        cursor = body["next_cursor"]
    return items


def test_service_event_feed_filters_all_origins_and_old_cursor_after_live_source_revoke(
    client, factory
):
    setup, account, bearer = service_sample(client, factory)
    events = collect_events(client, bearer)
    assert events
    source_id = setup["source"]["id"]
    assert any(source_id in str(event) for event in events)
    for event in events:
        assert event["payload_version"] == 1
        assert set(event) == {
            "id",
            "event_type",
            "payload_version",
            "created_at",
            "object",
            "origins",
        }
        assert setup["extraction"]["blocks"][0]["text"] not in str(event)
    first = service_get(client, bearer, "/events?page_size=1").json()
    old_cursor = first["next_cursor"]
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], UUID(account["user_id"]))
        ).permissions = []
    fresh = collect_events(client, bearer)
    resumed = collect_events(client, bearer, old_cursor) if old_cursor else []
    for event in [*fresh, *resumed]:
        for hidden in (
            source_id,
            setup["extraction"]["file_id"],
            setup["extraction"]["blocks"][0]["id"],
        ):
            assert hidden not in str(event)
    service_get(client, bearer, f"/documents/{source_id}", 404)


def test_service_scope_revocation_applies_to_tokens_without_reissue_and_hides_process_basis(
    client, factory
):
    setup, account, bearer = service_sample(client, factory)
    service_get(client, bearer, f"/blocks/{setup['extraction']['blocks'][0]['id']}")
    headers = sign_in_as(client, factory, setup["sample"]["admin"])
    changed = client.patch(
        f"/api/v1/admin/service-accounts/{account['id']}",
        headers=headers,
        json={
            "name": account["name"],
            "registry_ids": [str(setup["process_registry"])],
            "operations": account["operations"],
            "state": account["state"],
            "reason": "Synthetic scope revocation",
        },
    )
    assert changed.status_code == 200, changed.text
    # The backing identity deliberately retains B; A-only scope is still an intersection.
    with factory.begin() as db:
        grant = db.get(RegistryAccess, (setup["sample"]["registry"], UUID(account["user_id"])))
        if grant is None:
            db.add(
                RegistryAccess(
                    registry_id=setup["sample"]["registry"],
                    user_id=UUID(account["user_id"]),
                    permissions=["read", "download"],
                )
            )
        else:
            grant.permissions = ["read", "download"]
    client.cookies.clear()
    service_get(client, bearer, f"/documents/{setup['source']['id']}", 404)
    service_get(client, bearer, f"/blocks/{setup['extraction']['blocks'][0]['id']}", 404)
    process = service_get(client, bearer, f"/process-versions/{setup['version']['id']}")
    assert process.json()["steps"][0]["sources"][0]["restricted"] is True
    for hidden in (
        setup["source"]["id"],
        setup["source"]["title"],
        setup["extraction"]["file_id"],
        setup["extraction"]["blocks"][0]["id"],
        setup["extraction"]["blocks"][0]["text"],
    ):
        assert hidden not in process.text
    for event in collect_events(client, bearer):
        assert setup["source"]["id"] not in str(event)


def test_service_nested_process_source_cannot_bypass_missing_content_and_file_operations(
    client, factory
):
    setup, _, bearer = service_sample(client, factory, operations=["documents", "processes"])
    client.cookies.clear()
    process = service_get(client, bearer, f"/process-versions/{setup['version']['id']}")
    assert process.json()["steps"][0]["sources"][0]["restricted"] is True
    step = service_get(client, bearer, f"/process-versions/{setup['version']['id']}/steps/start")
    for hidden in (
        setup["extraction"]["file_id"],
        setup["extraction"]["blocks"][0]["id"],
        setup["extraction"]["blocks"][0]["text"],
    ):
        assert hidden not in process.text + step.text


def test_service_token_a_only_does_not_expand_backing_account_a_and_b_nested_source(
    client, factory
):
    setup, account, bearer = service_sample(client, factory, process_token_only=True)
    with factory() as db:
        grant = db.get(RegistryAccess, (setup["sample"]["registry"], UUID(account["user_id"])))
        assert "read" in grant.permissions and "download" in grant.permissions
    # Even a valid administrative browser cookie cannot widen the scoped actor.
    process = service_get(client, bearer, f"/process-versions/{setup['version']['id']}")
    assert process.json()["steps"][0]["sources"][0]["restricted"] is True
    for hidden in (
        setup["source"]["id"],
        setup["source"]["title"],
        setup["extraction"]["blocks"][0]["id"],
        setup["extraction"]["blocks"][0]["text"],
    ):
        assert hidden not in process.text
    client.cookies.clear()
    service_get(client, bearer, f"/blocks/{setup['extraction']['blocks'][0]['id']}", 404)
    for event in collect_events(client, bearer):
        assert setup["source"]["id"] not in str(event)
