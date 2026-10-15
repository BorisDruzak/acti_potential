from uuid import UUID

from tests.conftest import login_admin


def headers(client):
    me = client.get("/api/v1/users/me").json()
    return {"Origin": "https://testserver", "X-CSRF-Token": me["csrf_token"]}


def setup_registry(client, factory):
    login_admin(client, factory)
    actor_id = client.get("/api/v1/users/me").json()["id"]
    from app.registry.builtin import seed_builtin

    with factory.begin() as db:
        seed_builtin(db)
    registry = next(r for r in client.get("/api/v1/registries").json() if r["code"] == "okrug")
    granted = client.put(
        "/api/v1/admin/access/" + registry["id"],
        json={
            "entries": [
                {
                    "user_id": actor_id,
                    "permissions": ["read", "create", "update", "upload", "download"],
                }
            ]
        },
        headers=headers(client),
    )
    assert granted.status_code == 200, granted.text
    return registry


def test_document_create_update_conflict_history_and_versions(client, factory):
    registry = setup_registry(client, factory)
    response = client.post(
        "/api/v1/documents",
        json={
            "registry_id": registry["id"],
            "document_type_id": registry["document_type_ids"][0],
            "values": {
                "title": "Synthetic acceptance",
                "reg_number": "TEST-002",
                "should_publish": None,
            },
        },
        headers=headers(client),
    )
    assert response.status_code == 201, response.text
    document = response.json()
    assert UUID(document["id"])
    assert document["values"]["title"] == "Synthetic acceptance"
    assert document["values"]["should_publish"] is None
    updated = client.put(
        "/api/v1/documents/" + document["id"],
        json={"revision": document["revision"], "values": {"title": "Synthetic updated"}},
        headers=headers(client),
    )
    assert updated.status_code == 200, updated.text
    conflict = client.put(
        "/api/v1/documents/" + document["id"],
        json={"revision": document["revision"], "values": {"title": "Stale overwrite"}},
        headers=headers(client),
    )
    assert conflict.status_code == 409
    current = updated.json()
    for body in (b"first original", b"second original"):
        upload = client.post(
            "/api/v1/documents/" + document["id"] + "/files",
            params={"filename": "synthetic.txt", "revision": current["revision"]},
            content=body,
            headers={**headers(client), "Content-Type": "text/plain"},
        )
        assert upload.status_code == 201, upload.text
        current = client.get("/api/v1/documents/" + document["id"]).json()
    assert [f["version"] for f in current["files"]] == [1, 2]
    for entry, body in zip(current["files"], (b"first original", b"second original")):
        assert client.get("/api/v1/files/" + entry["id"] + "/download").content == body
    history = client.get("/api/v1/documents/" + document["id"] + "/history").json()
    assert history[0]["values"]["title"] == "Synthetic acceptance"
    assert history[1]["values"]["title"] == "Synthetic updated"
    assert client.get("/api/v1/documents", params={"q": "Synthetic updated"}).json()["total"] == 1


def test_admin_without_registry_grant_cannot_access_content(client, factory):
    login_admin(client, factory)
    from app.registry.builtin import seed_builtin

    with factory.begin() as db:
        seed_builtin(db)
    result = client.get("/api/v1/documents").json()
    assert result["items"] == [] and result["total"] == 0
    registries = client.get("/api/v1/registries").json()
    registry = next(r for r in registries if r["code"] == "okrug")
    denied = client.post(
        "/api/v1/documents",
        json={
            "registry_id": registry["id"],
            "document_type_id": registry["document_type_ids"][0],
            "values": {"title": "Should not exist"},
        },
        headers=headers(client),
    )
    assert denied.status_code == 403


def test_typed_values_reject_invalid_unknown_and_system_shadowing(client, factory):
    registry = setup_registry(client, factory)
    for values in (
        {"title": "x", "reg_date": "2026-02-31"},
        {"title": "x", "pages_count": True},
        {"title": "x", "undeclared": "secret"},
        {"title": "x", "type_id": "not-a-uuid"},
    ):
        response = client.post(
            "/api/v1/documents",
            json={
                "registry_id": registry["id"],
                "document_type_id": registry["document_type_ids"][0],
                "values": values,
            },
            headers=headers(client),
        )
        assert response.status_code == 422, response.text


def test_required_file_is_created_atomically_with_document(client, factory):
    import base64

    registry = setup_registry(client, factory)
    security = headers(client)
    kind = client.post(
        "/api/v1/document-types",
        json={"code": "required-file", "name": "Synthetic required original"},
        headers=security,
    ).json()
    version = client.get("/api/v1/document-types/" + kind["id"] + "/versions").json()[0]
    schema = {
        "sections": [
            {
                "key": "main",
                "title": "Main",
                "fields": [
                    {
                        "key": "title",
                        "label": "Title",
                        "type": "text",
                        "system": "title",
                        "required": True,
                    },
                    {"key": "original", "label": "Original", "type": "file", "required": True},
                ],
            }
        ]
    }
    assert (
        client.put(
            "/api/v1/form-schemas/" + version["id"], json={"schema": schema}, headers=security
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/form-schemas/" + version["id"] + "/publish", json={}, headers=security
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/v1/registries/" + registry["id"],
            json={"document_type_ids": [*registry["document_type_ids"], kind["id"]]},
            headers=security,
        ).status_code
        == 200
    )
    body = {
        "registry_id": registry["id"],
        "document_type_id": kind["id"],
        "values": {"title": "With required original"},
    }
    assert client.post("/api/v1/documents", json=body, headers=security).status_code == 422
    body["files"] = [
        {
            "field_key": "original",
            "name": "synthetic.txt",
            "mime": "text/plain",
            "content_base64": base64.b64encode(b"synthetic original").decode(),
        }
    ]
    response = client.post("/api/v1/documents", json=body, headers=security)
    assert response.status_code == 201, response.text
    card = response.json()
    assert card["values"]["original"] == [card["files"][0]["id"]]
    assert (
        client.get("/api/v1/files/" + card["files"][0]["id"] + "/download").content
        == b"synthetic original"
    )
    from pathlib import Path

    storage = Path(client.app.state.settings.storage_root)
    before_files = set(storage.iterdir())
    invalid = {**body, "values": {"title": ""}}
    assert client.post("/api/v1/documents", json=invalid, headers=security).status_code == 422
    assert set(storage.iterdir()) == before_files
