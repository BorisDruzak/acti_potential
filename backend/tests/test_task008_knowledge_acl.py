"""Task008 knowledge visibility and source ACL against real versioned originals."""

import base64

from app.registry.models import RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task005_content import synthetic_docx
from tests.test_task007_sources_acl import source_process

SENSITIVE_NOTE = "SYNTHETIC-PRIVATE-KNOWLEDGE-LINK-NOTE"


def knowledge_sample(client, factory, *, hidden_original=False):
    setup = source_process(client, factory, private=hidden_original)
    with factory.begin() as db:
        grant = db.get(RegistryAccess, (setup["process_registry"], setup["sample"]["editor"].id))
        grant.permissions = ["read", "create", "update", "download"]
        viewer = db.get(RegistryAccess, (setup["process_registry"], setup["sample"]["viewer"].id))
        viewer.permissions = ["read", "download"]
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    return setup, headers


def bindings(setup):
    return {
        "document_type_id": setup["source"]["document_type_id"],
        "form_schema_id": setup["source"]["schema_version_id"],
        "process_version_id": setup["version"]["id"],
        "step_key": "start",
        "category": "SYNTHETIC-CATEGORY",
    }


def original(setup):
    extraction = setup["extraction"]
    return {
        "document_id": setup["source"]["id"],
        "version_id": extraction["version_id"],
        "file_id": extraction["file_id"],
    }


def publish_template(
    client, headers, setup, *, name="SYNTHETIC-ALPHA-TEMPLATE", visibility="registry"
):
    created = client.post(
        "/api/v1/knowledge/templates",
        headers=headers,
        json={
            "registry_id": str(setup["process_registry"]),
            "name": name,
            "purpose": "Synthetic reusable original",
            "visibility": visibility,
            "original": original(setup),
            **bindings(setup),
        },
    )
    assert created.status_code == 201, created.text
    template = created.json()
    published = client.post(
        f"/api/v1/knowledge/template-versions/{template['version']['id']}/publish",
        headers=headers,
        json={"revision": template["version"]["revision"]},
    )
    assert published.status_code == 200, published.text
    return template, published.json()


def publish_case(client, headers, setup, *, name="SYNTHETIC-ALPHA-CASE", visibility="registry"):
    created = client.post(
        "/api/v1/knowledge/cases",
        headers=headers,
        json={
            "registry_id": str(setup["process_registry"]),
            "name": name,
            "description": "Synthetic previous practice",
            "result": "Synthetic completed result",
            "visibility": visibility,
            "links": [
                {
                    "document_id": setup["source"]["id"],
                    "version_id": setup["extraction"]["version_id"],
                    "block_id": setup["extraction"]["blocks"][0]["id"],
                    "note": SENSITIVE_NOTE,
                }
            ],
            **bindings(setup),
        },
    )
    assert created.status_code == 201, created.text
    case = created.json()
    published = client.post(
        f"/api/v1/knowledge/cases/{case['id']}/publish",
        headers=headers,
        json={"revision": case["revision"]},
    )
    assert published.status_code == 200, published.text
    return published.json()


def related(client, setup):
    return client.get(
        "/api/v1/knowledge/related",
        params={
            "process_version_id": setup["version"]["id"],
            "step_key": "start",
            "mode": "viewer",
        },
    )


def test_private_and_newly_restricted_objects_are_absent_from_counts_context_and_direct_urls(
    client, factory
):
    setup, headers = knowledge_sample(client, factory)
    public_template, public_version = publish_template(client, headers, setup)
    private_template, private_version = publish_template(
        client, headers, setup, name="SYNTHETIC-ALPHA-PRIVATE-TEMPLATE", visibility="private"
    )
    public_case = publish_case(client, headers, setup)
    private_case = publish_case(
        client, headers, setup, name="SYNTHETIC-ALPHA-PRIVATE-CASE", visibility="private"
    )
    sign_in_as(client, factory, setup["sample"]["viewer"])
    for collection, visible_id in (
        ("templates", public_template["id"]),
        ("cases", public_case["id"]),
    ):
        response = client.get(f"/api/v1/knowledge/{collection}", params={"text": "SYNTHETIC-ALPHA"})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1 and response.json()["items"][0]["id"] == visible_id
    context = related(client, setup)
    assert context.status_code == 200, context.text
    assert len(context.json()["templates"]) == len(context.json()["cases"]) == 1
    for endpoint in (
        f"/templates/{private_template['id']}",
        f"/template-versions/{private_version['id']}",
        f"/cases/{private_case['id']}",
    ):
        assert client.get("/api/v1/knowledge" + endpoint).status_code == 404
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    for collection, identity in (
        ("templates", public_template["id"]),
        ("cases", public_case["id"]),
    ):
        changed = client.patch(
            f"/api/v1/knowledge/{collection}/{identity}/access",
            headers=headers,
            json={
                "visibility": "private",
                "lifecycle": "active",
                "reason": "Synthetic publication visibility revoked",
            },
        )
        assert changed.status_code == 200, changed.text
    sign_in_as(client, factory, setup["sample"]["viewer"])
    for collection in ("templates", "cases"):
        response = client.get(f"/api/v1/knowledge/{collection}", params={"text": "SYNTHETIC-ALPHA"})
        assert response.status_code == 200 and response.json()["total"] == 0, response.text
    for endpoint in (
        f"/templates/{public_template['id']}",
        f"/template-versions/{public_version['id']}/download",
        f"/cases/{public_case['id']}",
    ):
        assert client.get("/api/v1/knowledge" + endpoint).status_code == 404
    context = related(client, setup)
    assert context.json()["templates"] == [] and context.json()["cases"] == []


def test_hidden_original_cannot_leak_via_template_basis_content_search_or_download(client, factory):
    setup, headers = knowledge_sample(client, factory, hidden_original=True)
    template, version = publish_template(client, headers, setup)
    sign_in_as(client, factory, setup["sample"]["viewer"])
    detail = client.get(f"/api/v1/knowledge/template-versions/{version['id']}?mode=viewer")
    assert detail.status_code == 200, detail.text
    assert detail.json()["original"] == {"restricted": True, "label": "Оригинал ограничен"}
    basis = client.get(f"/api/v1/knowledge/template-versions/{version['id']}/basis?mode=viewer")
    assert basis.status_code == 200 and basis.json()["restricted"] is True, basis.text
    assert client.get(
        f"/api/v1/knowledge/template-versions/{version['id']}/download?mode=viewer"
    ).status_code in {403, 404}
    for hidden in (
        setup["source"]["id"],
        setup["source"]["title"],
        setup["extraction"]["file_id"],
        setup["extraction"]["version_id"],
        "source.docx",
        "Синтетическое основание",
    ):
        assert hidden not in detail.text + basis.text
    matches = client.get("/api/v1/knowledge/templates", params={"text": "Синтетическое основание"})
    assert matches.status_code == 200 and matches.json()["total"] == 0, matches.text
    assert client.get(f"/api/v1/knowledge/templates/{template['id']}").status_code == 200


def test_live_source_download_revocation_removes_content_matches_and_original_but_not_metadata(
    client, factory
):
    setup, headers = knowledge_sample(client, factory)
    template, version = publish_template(client, headers, setup)
    sign_in_as(client, factory, setup["sample"]["viewer"])
    endpoint = "/api/v1/knowledge/templates"
    assert client.get(endpoint, params={"text": "Синтетическое основание"}).json()["total"] == 1
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["viewer"].id)
        ).permissions = ["read"]
    assert client.get(endpoint, params={"text": "Синтетическое основание"}).json()["total"] == 0
    detail = client.get(f"/api/v1/knowledge/templates/{template['id']}")
    assert (
        detail.status_code == 200 and detail.json()["version"]["original"]["restricted"] is True
    ), detail.text
    basis = client.get(f"/api/v1/knowledge/template-versions/{version['id']}/basis")
    assert basis.status_code == 200 and basis.json()["restricted"] is True, basis.text
    assert client.get(
        f"/api/v1/knowledge/template-versions/{version['id']}/download"
    ).status_code in {403, 404}
    assert client.get(endpoint, params={"text": template["name"]}).json()["total"] == 1
    assert setup["extraction"]["file_id"] not in basis.text
    # Template content requires the object's download grant as well as the source's.
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["viewer"].id)
        ).permissions = ["read", "download"]
    assert client.get(endpoint, params={"text": "Синтетическое основание"}).json()["total"] == 1
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["viewer"].id)
        ).permissions = ["read"]
    assert client.get(f"/api/v1/knowledge/templates/{template['id']}").status_code == 200
    assert (
        client.get(f"/api/v1/knowledge/template-versions/{version['id']}/download").status_code
        == 403
    )
    assert client.get(endpoint, params={"text": "Синтетическое основание"}).json()["total"] == 0
    assert client.get(endpoint, params={"text": template["name"]}).json()["total"] == 1


def test_closed_case_link_retains_case_but_redacts_source_name_ids_note_and_text(client, factory):
    setup, headers = knowledge_sample(client, factory)
    case = publish_case(client, headers, setup)
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["viewer"].id)
        ).permissions = []
    sign_in_as(client, factory, setup["sample"]["viewer"])
    detail = client.get(f"/api/v1/knowledge/cases/{case['id']}")
    basis = client.get(f"/api/v1/knowledge/cases/{case['id']}/basis")
    assert detail.status_code == basis.status_code == 200
    assert detail.json()["links"] == [{"restricted": True, "label": "Основание ограничено"}]
    assert basis.json()["items"] == [{"restricted": True, "label": "Основание ограничено"}]
    for hidden in (
        setup["source"]["id"],
        setup["source"]["title"],
        setup["extraction"]["file_id"],
        setup["extraction"]["blocks"][0]["id"],
        SENSITIVE_NOTE,
        "Синтетическое основание",
    ):
        assert hidden not in detail.text + basis.text
    matches = client.get("/api/v1/knowledge/cases", params={"text": "Синтетическое основание"})
    assert matches.status_code == 200 and matches.json()["total"] == 0, matches.text


def test_template_v1_keeps_exact_original_after_publishing_different_v2(client, factory):
    setup, headers = knowledge_sample(client, factory)
    template, old = publish_template(client, headers, setup)
    card = client.get(f"/api/v1/documents/{setup['source']['id']}").json()
    second_bytes = synthetic_docx("Иной оригинал второй версии шаблона")
    uploaded = client.put(
        f"/api/v1/documents/{setup['source']['id']}",
        headers=headers,
        json={
            "revision": card["revision"],
            "values": {},
            "files": [
                {
                    "field_key": "public_file",
                    "name": "source.docx",
                    "content_base64": base64.b64encode(second_bytes).decode(),
                }
            ],
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    file = max(uploaded.json()["files"], key=lambda row: row["version"])
    draft = client.post(
        f"/api/v1/knowledge/templates/{template['id']}/versions", headers=headers, json={}
    )
    assert draft.status_code == 201, draft.text
    body = {
        key: old[key]
        for key in (
            "name",
            "purpose",
            "category",
            "visibility",
            "owner_id",
            "document_type_id",
            "form_schema_id",
            "process_version_id",
            "step_key",
            "characteristics",
        )
        if key in old
    }
    body.update(
        revision=draft.json()["revision"],
        original={
            "document_id": setup["source"]["id"],
            "version_id": (
                client.get(f"/api/v1/documents/{setup['source']['id']}/relation-options").json()[
                    "versions"
                ][0]["id"]
            ),
            "file_id": file["id"],
        },
    )
    saved = client.put(
        f"/api/v1/knowledge/template-versions/{draft.json()['id']}", headers=headers, json=body
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/knowledge/template-versions/{draft.json()['id']}/publish",
        headers=headers,
        json={"revision": saved.json()["revision"]},
    )
    assert published.status_code == 200, published.text
    sign_in_as(client, factory, setup["sample"]["viewer"])
    old_read = client.get(f"/api/v1/knowledge/template-versions/{old['id']}")
    assert (
        old_read.status_code == 200
        and old_read.json()["original"]["file_id"] == setup["extraction"]["file_id"]
    )
    first = client.get(f"/api/v1/knowledge/template-versions/{old['id']}/download")
    second = client.get(f"/api/v1/knowledge/template-versions/{published.json()['id']}/download")
    assert first.status_code == second.status_code == 200
    assert (
        first.content == setup["original"]
        and second.content == second_bytes
        and first.content != second.content
    )


def test_admin_without_object_scope_or_private_ownership_cannot_bypass_knowledge_acl(
    client, factory
):
    setup, headers = knowledge_sample(client, factory)
    private_case = publish_case(client, headers, setup, visibility="private")
    template, version = publish_template(client, headers, setup)
    sign_in_as(client, factory, setup["sample"]["admin"])
    assert client.get(f"/api/v1/knowledge/cases/{private_case['id']}").status_code == 404
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["admin"].id)
        ).permissions = []
    for endpoint in (
        f"/templates/{template['id']}",
        f"/template-versions/{version['id']}",
        f"/template-versions/{version['id']}/download",
    ):
        assert client.get("/api/v1/knowledge" + endpoint).status_code == 404
    for collection in ("templates", "cases"):
        response = client.get("/api/v1/knowledge/" + collection)
        assert response.status_code == 200 and response.json()["total"] == 0, response.text


def test_hidden_typed_characteristics_are_redacted_and_invalid_values_rejected(client, factory):
    from app.registry.models import ReferenceItem, ReferenceSet

    setup, _ = knowledge_sample(client, factory)
    with factory.begin() as db:
        allowed = ReferenceSet(code="knowledge-allowed", name="Synthetic permitted characteristics")
        foreign = ReferenceSet(code="knowledge-foreign", name="Synthetic other reference")
        db.add_all([allowed, foreign])
        db.flush()
        allowed_item = ReferenceItem(reference_set_id=allowed.id, label="Permitted value")
        foreign_item = ReferenceItem(reference_set_id=foreign.id, label="Wrong reference value")
        db.add_all([allowed_item, foreign_item])
        db.flush()
        reference_id, valid_id, foreign_id = (
            str(allowed.id),
            str(allowed_item.id),
            str(foreign_item.id),
        )
    admin_headers = sign_in_as(client, factory, setup["sample"]["admin"])
    key, label, operand = (
        "hidden_case_characteristic",
        "SYNTHETIC-HIDDEN-CHARACTERISTIC-LABEL",
        "SYNTHETIC-HIDDEN-CHARACTERISTIC-OPERAND",
    )
    schema = {
        "sections": [
            {
                "key": "main",
                "title": "Typed characteristics",
                "fields": [
                    {"key": "title", "label": "Title", "type": "text", "system": "title"},
                    {
                        "key": key,
                        "label": label,
                        "type": "text",
                        "read_roles": ["editor"],
                        "filterable": True,
                    },
                    {"key": "count", "label": "Count", "type": "integer", "filterable": True},
                    {
                        "key": "choice",
                        "label": "Choice",
                        "type": "reference",
                        "reference_set_id": reference_id,
                        "filterable": True,
                    },
                ],
            }
        ]
    }
    form = client.post(
        f"/api/v1/document-types/{setup['source']['document_type_id']}/versions",
        headers=admin_headers,
        json={"schema": schema},
    )
    assert form.status_code == 201, form.text
    form_id = form.json()["id"]
    published_form = client.post(
        f"/api/v1/form-schemas/{form_id}/publish", headers=admin_headers, json={}
    )
    assert published_form.status_code == 200, published_form.text
    draft = client.post(
        f"/api/v1/processes/{setup['process']['id']}/versions", headers=admin_headers, json={}
    )
    assert draft.status_code == 201, draft.text
    draft = draft.json()
    step_fields = (
        "key",
        "title",
        "instruction",
        "executor_role",
        "department",
        "duration_hours",
        "deadline",
        "inputs",
        "outputs",
        "system",
        "control_note",
        "is_start",
        "is_terminal",
        "form_schema_id",
        "sources",
        "templates",
    )
    steps = [
        {field: step[field] for field in step_fields if field in step} for step in draft["steps"]
    ]
    steps[0]["form_schema_id"] = form_id
    for step in steps:
        step["sources"] = [
            {
                key: item[key]
                for key in ("document_id", "version_id", "block_id", "note")
                if key in item
            }
            for item in step.get("sources", [])
        ]
    transitions = [
        {
            key: edge[key]
            for key in ("key", "from_step", "to_step", "label", "condition", "is_else")
            if key in edge
        }
        for edge in draft["transitions"]
    ]
    saved = client.put(
        f"/api/v1/process-versions/{draft['id']}",
        headers=admin_headers,
        json={
            "revision": draft["revision"],
            "effective_from": "2026-02-01",
            "steps": steps,
            "transitions": transitions,
        },
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/process-versions/{draft['id']}/publish",
        headers=admin_headers,
        json={"revision": saved.json()["revision"]},
    )
    assert published.status_code == 200, published.text
    setup["version"] = published.json()
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    metadata = {
        "registry_id": str(setup["process_registry"]),
        "name": "SYNTHETIC-TYPED-KNOWLEDGE",
        "visibility": "registry",
        **bindings(setup),
        "form_schema_id": form_id,
        "characteristics": {key: [operand], "count": [2], "choice": [valid_id]},
    }
    created = client.post(
        "/api/v1/knowledge/templates",
        headers=headers,
        json={**metadata, "original": original(setup)},
    )
    assert created.status_code == 201, created.text
    template = created.json()
    assert (
        client.post(
            f"/api/v1/knowledge/template-versions/{template['version']['id']}/publish",
            headers=headers,
            json={"revision": template["version"]["revision"]},
        ).status_code
        == 200
    )
    case = client.post(
        "/api/v1/knowledge/cases",
        headers=headers,
        json={
            **metadata,
            "description": "Synthetic typed case",
            "result": "Synthetic result",
            "links": [],
        },
    )
    assert case.status_code == 201, case.text
    case = case.json()
    assert (
        client.post(
            f"/api/v1/knowledge/cases/{case['id']}/publish",
            headers=headers,
            json={"revision": case["revision"]},
        ).status_code
        == 200
    )
    for changes in (
        {"unknown_field": ["value"]},
        {"count": ["not an integer"]},
        {"choice": [foreign_id]},
    ):
        denied = client.post(
            "/api/v1/knowledge/templates",
            headers=headers,
            json={
                **metadata,
                "name": "Invalid typed template",
                "characteristics": changes,
                "original": original(setup),
            },
        )
        assert denied.status_code == 422, denied.text
    sign_in_as(client, factory, setup["sample"]["viewer"])
    outputs = [
        client.get(f"/api/v1/knowledge/templates/{template['id']}"),
        client.get(f"/api/v1/knowledge/cases/{case['id']}"),
        related(client, setup),
    ]
    for response in outputs:
        assert response.status_code == 200, response.text
        for secret in (key, label, operand):
            assert secret not in response.text
    assert outputs[0].json()["version"]["characteristics"]["count"] == [2]
    assert outputs[1].json()["characteristics"]["choice"] == [valid_id]
    assert len(outputs[2].json()["templates"]) == len(outputs[2].json()["cases"]) == 1
