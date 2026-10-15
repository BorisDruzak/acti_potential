from tests.test_schema_engine import schema


def admin_headers(client):
    return {
        "Origin": "https://testserver",
        "X-CSRF-Token": client.get("/api/v1/users/me").json()["csrf_token"],
    }


def test_admin_previews_unassigned_draft_reference_items_without_source_metadata(client, factory):
    from uuid import UUID

    from app.api.dependencies import current_actor
    from app.identity.models import User
    from app.identity.policy import Actor
    from app.registry.models import ReferenceItem
    from tests.conftest import login_admin

    login_admin(client, factory)
    headers = admin_headers(client)
    reference = client.post(
        "/api/v1/references",
        headers=headers,
        json={"code": "synthetic_preview", "name": "Synthetic preview choices"},
    )
    assert reference.status_code == 201, reference.text
    reference_id = reference.json()["id"]
    created = client.post(
        f"/api/v1/references/{reference_id}/items",
        headers=headers,
        json={"label": "Synthetic preview choice", "metadata": {"position": "Synthetic metadata"}},
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    with factory.begin() as db:
        item = db.get(ReferenceItem, UUID(item_id))
        item.source_database = "synthetic_preview"
        item.source_entity = "ref_synthetic"
        item.source_id = "1"
    kind = client.post(
        "/api/v1/document-types",
        headers=headers,
        json={"code": "synthetic_preview", "name": "Synthetic unpublished preview"},
    )
    assert kind.status_code == 201, kind.text
    draft = client.get(f"/api/v1/document-types/{kind.json()['id']}/versions").json()[0]
    saved = client.put(
        f"/api/v1/form-schemas/{draft['id']}",
        headers=headers,
        json={"schema": schema("reference", reference_set_id=reference_id)},
    )
    assert saved.status_code == 200 and saved.json()["state"] == "draft", saved.text
    administrative = client.get(f"/api/v1/references/{reference_id}/items").json()[0]
    assert administrative["metadata"] == {"position": "Synthetic metadata"}
    assert administrative["source_database"] == "synthetic_preview"
    for mode in ("editor", "viewer"):
        preview = client.get(f"/api/v1/references/{reference_id}/items?mode={mode}")
        assert preview.status_code == 200, preview.text
        item = preview.json()[0]
        assert item["id"] == item_id and item["label"] == "Synthetic preview choice"
        assert item["metadata"] == {}
        assert all(item[key] is None for key in ("source_database", "source_entity", "source_id"))
        assert reference_id in {
            r["id"] for r in client.get(f"/api/v1/references?mode={mode}").json()
        }

    try:
        for role in ("editor", "viewer"):
            with factory.begin() as db:
                user = User(display_name=f"Synthetic preview {role}")
                db.add(user)
                db.flush()
                actor = Actor(user.id, user.display_name, frozenset({role}))
            client.app.dependency_overrides[current_actor] = lambda: actor
            for mode in ("editor", "viewer"):
                assert (
                    client.get(f"/api/v1/references/{reference_id}/items?mode={mode}").status_code
                    == 404
                )
                assert reference_id not in {
                    r["id"] for r in client.get(f"/api/v1/references?mode={mode}").json()
                }
    finally:
        client.app.dependency_overrides.pop(current_actor, None)


def test_configuration_publication_copy_and_key_history(client, factory):
    from uuid import uuid4

    from tests.conftest import login_admin

    login_admin(client, factory)
    headers = admin_headers(client)
    response = client.post(
        "/api/v1/document-types",
        headers=headers,
        json={"code": "synthetic-" + uuid4().hex[:8], "name": "Synthetic dynamic type"},
    )
    assert response.status_code == 201, response.text
    kind = response.json()
    draft = client.get(f"/api/v1/document-types/{kind['id']}/versions").json()[0]
    body = schema("integer", constraints={"min": 1, "max": 10}, default=2, modes=["editor"])
    saved = client.put(
        f"/api/v1/form-schemas/{draft['id']}", headers=headers, json={"schema": body}
    )
    assert saved.status_code == 200, saved.text
    published = client.post(f"/api/v1/form-schemas/{draft['id']}/publish", headers=headers, json={})
    assert published.status_code == 200, published.text
    assert published.json()["state"] == "published"
    viewer_schema = client.get(f"/api/v1/form-schemas/{draft['id']}?mode=viewer").json()
    assert viewer_schema["schema"]["sections"][0]["fields"] == []
    editor_schema = client.get(f"/api/v1/form-schemas/{draft['id']}?mode=editor").json()
    assert editor_schema["schema"]["sections"][0]["fields"][0]["key"] == "detail"
    assert (
        client.put(
            f"/api/v1/form-schemas/{draft['id']}", headers=headers, json={"schema": body}
        ).status_code
        == 409
    )
    next_draft = client.post(
        f"/api/v1/document-types/{kind['id']}/versions", headers=headers, json={}
    )
    assert next_draft.status_code == 201, next_draft.text
    assert next_draft.json()["version"] == 2
    incompatible = client.put(
        f"/api/v1/form-schemas/{next_draft.json()['id']}",
        headers=headers,
        json={"schema": schema("text")},
    )
    assert incompatible.status_code == 422, incompatible.text
    copied = client.post(
        f"/api/v1/document-types/{kind['id']}/copy",
        headers=headers,
        json={"code": "copied-" + uuid4().hex[:8], "name": "Independent copied type"},
    )
    assert copied.status_code == 201, copied.text
    assert copied.json()["id"] != kind["id"]
    copied_schema = client.get(f"/api/v1/document-types/{copied.json()['id']}/versions").json()[0]
    assert copied_schema["state"] == "draft" and copied_schema["schema"] == body


def test_publication_rejects_invalid_defaults_and_cross_registry_bindings(client, factory):
    from uuid import uuid4

    from tests.conftest import login_admin

    login_admin(client, factory)
    headers = admin_headers(client)
    suffix = uuid4().hex[:8]
    registries = []
    refs = []
    items = []
    for index in range(2):
        response = client.post(
            "/api/v1/registries",
            headers=headers,
            json={"code": f"synthetic-{suffix}-{index}", "name": f"Synthetic registry {index}"},
        )
        assert response.status_code == 201, response.text
        registries.append(response.json())
        response = client.post(
            "/api/v1/references",
            headers=headers,
            json={
                "code": "synthetic_choices",
                "name": "Synthetic choices",
                "registry_id": registries[-1]["id"],
            },
        )
        assert response.status_code == 201, response.text
        refs.append(response.json())
        response = client.post(
            f"/api/v1/references/{refs[-1]['id']}/items",
            headers=headers,
            json={"label": f"Synthetic choice {index}"},
        )
        assert response.status_code == 201, response.text
        items.append(response.json())
    kind = client.post(
        "/api/v1/document-types",
        headers=headers,
        json={"code": f"synthetic-default-{suffix}", "name": "Synthetic reference defaults"},
    ).json()
    draft = client.get(f"/api/v1/document-types/{kind['id']}/versions").json()[0]
    for default, expected in ((items[1]["id"], 422), (items[0]["id"], 200)):
        value = schema("reference", reference_set_id=refs[0]["id"], default=default)
        saved = client.put(
            f"/api/v1/form-schemas/{draft['id']}", headers=headers, json={"schema": value}
        )
        assert saved.status_code == 200, saved.text
        published = client.post(
            f"/api/v1/form-schemas/{draft['id']}/publish", headers=headers, json={}
        )
        assert published.status_code == expected, published.text
    assigned = client.patch(
        f"/api/v1/registries/{registries[0]['id']}",
        headers=headers,
        json={"document_type_ids": [kind["id"]]},
    )
    assert assigned.status_code == 200, assigned.text
    rejected = client.patch(
        f"/api/v1/registries/{registries[1]['id']}",
        headers=headers,
        json={"document_type_ids": [kind["id"]]},
    )
    assert rejected.status_code == 422, rejected.text
