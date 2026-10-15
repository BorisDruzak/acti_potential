from tests.test_schema_engine import schema


def admin_headers(client):
    return {
        "Origin": "https://testserver",
        "X-CSRF-Token": client.get("/api/v1/users/me").json()["csrf_token"],
    }


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
