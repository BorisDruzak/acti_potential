"""Task007 published source and condition ACL with actual API/storage extraction."""

import base64
from uuid import UUID

from app.content.worker import process_extractions
from app.registry.models import Document, Registry, RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task005_content import synthetic_docx
from tests.test_task006_relations import relation_documents

SECRET_KEY = "secret_route_key"
SECRET_LABEL = "SYNTHETIC-HIDDEN-CONDITION-LABEL"
SECRET_OPERAND = "SYNTHETIC-HIDDEN-CONDITION-OPERAND"
PRIVATE_NOTE = "SYNTHETIC-PRIVATE-SOURCE-NOTE"
FIELDS = [
    {"key": "title", "label": "Title", "type": "text", "system": "title"},
    {"key": SECRET_KEY, "label": SECRET_LABEL, "type": "text", "read_roles": ["admin", "editor"]},
    {"key": "public_file", "label": "Public original", "type": "file"},
    {
        "key": "private_file",
        "label": "SYNTHETIC-PRIVATE-FILE",
        "type": "file",
        "read_roles": ["admin", "editor"],
        "write_roles": ["editor"],
    },
]


def source_process(client, factory, *, private=False, condition=False):
    sample, cards, headers = relation_documents(client, factory, fields=FIELDS)
    source = cards[0]
    original = synthetic_docx("Синтетическое основание описания административного шага")
    uploaded = client.put(
        f"/api/v1/documents/{source['id']}",
        headers=headers,
        json={
            "revision": source["revision"],
            "values": {},
            "files": [
                {
                    "field_key": "private_file" if private else "public_file",
                    "name": "source.docx",
                    "content_base64": base64.b64encode(original).decode(),
                }
            ],
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    file = uploaded.json()["files"][0]
    queued = client.post(f"/api/v1/files/{file['id']}/extract", headers=headers, json={})
    assert queued.status_code == 202, queued.text
    assert process_extractions(factory, client.app.state.settings.storage_root) == 1
    extraction = client.get(f"/api/v1/documents/{source['id']}/content").json()["items"][0]
    assert extraction["state"] == "completed"
    with factory.begin() as db:
        document = db.get(Document, UUID(source["id"]))
        registry = Registry(
            code="process-knowledge-acl",
            name="Synthetic process scope",
            document_type_ids=[str(document.document_type_id)],
        )
        db.add(registry)
        db.flush()
        process_registry_id = registry.id
        for actor in (sample["admin"], sample["editor"], sample["viewer"]):
            db.add(
                RegistryAccess(
                    registry_id=registry.id,
                    user_id=actor.id,
                    permissions=["read", "update"] if actor.id == sample["admin"].id else ["read"],
                )
            )
        db.add(
            RegistryAccess(
                registry_id=sample["registry"],
                user_id=sample["admin"].id,
                permissions=["read", "download"],
            )
        )
    admin_headers = sign_in_as(client, factory, sample["admin"])
    process = client.post(
        "/api/v1/processes",
        headers=admin_headers,
        json={
            "registry_id": str(process_registry_id),
            "code": "synthetic-process-acl",
            "name": "Synthetic process preparation",
            "purpose": "Synthetic Process Knowledge without execution",
        },
    )
    assert process.status_code == 201, process.text
    process = process.json()
    draft = client.get(f"/api/v1/processes/{process['id']}/versions").json()[0]
    source_reference = {
        "document_id": source["id"],
        "version_id": extraction["version_id"],
        "block_id": extraction["blocks"][0]["id"],
        "note": PRIVATE_NOTE if private else "Synthetic permitted source",
    }
    steps = [
        {
            "key": "start",
            "title": "Start preparation",
            "instruction": "Read the synthetic source",
            "is_start": True,
            "is_terminal": False,
            "form_schema_id": source["schema_version_id"],
            "sources": [source_reference],
        },
        {
            "key": "complete",
            "title": "Complete preparation",
            "is_start": False,
            "is_terminal": True,
        },
    ]
    transitions = [
        {
            "key": "complete-route",
            "from_step": "start",
            "to_step": "complete",
            "label": "Continue",
            "condition": None,
            "is_else": False,
        }
    ]
    if condition:
        steps.append(
            {
                "key": "fallback",
                "title": "Alternate preparation",
                "is_start": False,
                "is_terminal": True,
            }
        )
        transitions = [
            {
                "key": "private-route",
                "from_step": "start",
                "to_step": "complete",
                "label": "Conditional route",
                "condition": {"field": SECRET_KEY, "op": "eq", "value": SECRET_OPERAND},
                "is_else": False,
            },
            {
                "key": "else-route",
                "from_step": "start",
                "to_step": "fallback",
                "label": "Explicit else",
                "condition": None,
                "is_else": True,
            },
        ]
    body = {
        "revision": draft["revision"],
        "effective_from": "2026-01-01",
        "steps": steps,
        "transitions": transitions,
    }
    saved = client.put(f"/api/v1/process-versions/{draft['id']}", headers=admin_headers, json=body)
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/process-versions/{draft['id']}/publish",
        headers=admin_headers,
        json={"revision": saved.json()["revision"]},
    )
    assert published.status_code == 200, published.text
    return {
        "sample": sample,
        "source": source,
        "process": process,
        "version": published.json(),
        "process_registry": process_registry_id,
        "extraction": extraction,
        "source_reference": source_reference,
        "original": original,
    }


def test_hidden_file_basis_redacts_ids_names_snippets_and_notes(client, factory):
    setup = source_process(client, factory, private=True)
    sign_in_as(client, factory, setup["sample"]["viewer"])
    version = client.get(f"/api/v1/process-versions/{setup['version']['id']}?mode=viewer")
    assert version.status_code == 200, version.text
    assert len(version.json()["steps"]) == 2
    basis = client.get(
        f"/api/v1/process-versions/{setup['version']['id']}/steps/start/basis?mode=viewer"
    )
    assert basis.status_code == 200, basis.text
    assert basis.json()["items"] == [{"restricted": True, "label": "Основание ограничено"}]
    for hidden in (
        setup["source"]["id"],
        setup["extraction"]["version_id"],
        setup["extraction"]["file_id"],
        setup["extraction"]["blocks"][0]["id"],
        setup["source"]["title"],
        PRIVATE_NOTE,
        "Синтетическое основание",
    ):
        assert hidden not in version.text + basis.text


def test_source_download_revocation_keeps_process_usable_and_hides_basis(client, factory):
    setup = source_process(client, factory)
    sign_in_as(client, factory, setup["sample"]["viewer"])
    endpoint = f"/api/v1/process-versions/{setup['version']['id']}/steps/start/basis?mode=viewer"
    allowed = client.get(endpoint)
    assert allowed.status_code == 200 and allowed.json()["items"][0]["restricted"] is False, (
        allowed.text
    )
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["viewer"].id)
        ).permissions = ["read"]
    blocked = client.get(endpoint)
    assert blocked.status_code == 200 and blocked.json()["items"][0]["restricted"] is True, (
        blocked.text
    )
    assert setup["extraction"]["file_id"] not in blocked.text
    assert client.get(f"/api/v1/processes/{setup['process']['id']}?mode=viewer").status_code == 200
    assert client.get(f"/api/v1/documents/{setup['source']['id']}").status_code == 200
    assert client.get(f"/api/v1/files/{setup['extraction']['file_id']}/download").status_code == 403


def test_hidden_condition_never_leaks_operand_or_falls_through_else(client, factory):
    setup = source_process(client, factory, condition=True)
    viewer_headers = sign_in_as(client, factory, setup["sample"]["viewer"])
    endpoint = f"/api/v1/process-versions/{setup['version']['id']}"
    version = client.get(endpoint + "?mode=viewer")
    assert version.status_code == 200, version.text
    for secret in (SECRET_KEY, SECRET_LABEL, SECRET_OPERAND):
        assert secret not in version.text
    assert any(transition.get("restricted") for transition in version.json()["transitions"])
    for values in ({}, {SECRET_KEY: SECRET_OPERAND}, {SECRET_KEY: "a guessed value"}):
        preview = client.post(
            endpoint + "/preview",
            headers=viewer_headers,
            json={"step_key": "start", "values": values},
        )
        assert preview.status_code == 403, preview.text
        for secret in (SECRET_KEY, SECRET_LABEL, SECRET_OPERAND, "fallback"):
            assert secret not in preview.text


def test_process_scope_revocation_blocks_direct_version_basis_preview_and_counts(client, factory):
    setup = source_process(client, factory)
    viewer_headers = sign_in_as(client, factory, setup["sample"]["viewer"])
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["viewer"].id)
        ).permissions = []
    for endpoint in (
        f"/processes/{setup['process']['id']}",
        f"/process-versions/{setup['version']['id']}",
        f"/process-versions/{setup['version']['id']}/steps/start/basis",
    ):
        response = client.get("/api/v1" + endpoint)
        assert response.status_code == 404, response.text
        assert setup["process"]["name"] not in response.text
    listed = client.get("/api/v1/processes", params={"q": setup["process"]["name"]})
    assert listed.status_code == 200 and listed.json() == [], listed.text
    preview = client.post(
        f"/api/v1/process-versions/{setup['version']['id']}/preview",
        headers=viewer_headers,
        json={"step_key": "start", "values": {}},
    )
    assert preview.status_code == 404, preview.text


def test_published_form_and_original_pins_survive_later_versions(client, factory):
    setup = source_process(client, factory, condition=True)
    admin_headers = sign_in_as(client, factory, setup["sample"]["admin"])
    version_id = setup["version"]["id"]
    before = client.get(f"/api/v1/process-versions/{version_id}").json()
    draft = client.post(
        f"/api/v1/document-types/{setup['source']['document_type_id']}/versions",
        headers=admin_headers,
        json={
            "schema": {
                "sections": [
                    {
                        "key": "main",
                        "title": "Later form",
                        "fields": [
                            {
                                "key": "title",
                                "label": "New title label",
                                "type": "text",
                                "system": "title",
                            }
                        ],
                    }
                ]
            }
        },
    )
    assert draft.status_code == 201, draft.text
    published = client.post(
        f"/api/v1/form-schemas/{draft.json()['id']}/publish", headers=admin_headers, json={}
    )
    assert published.status_code == 200, published.text
    editor_headers = sign_in_as(client, factory, setup["sample"]["editor"])
    card = client.get(f"/api/v1/documents/{setup['source']['id']}").json()
    upload = client.put(
        f"/api/v1/documents/{setup['source']['id']}",
        headers=editor_headers,
        json={
            "revision": card["revision"],
            "values": {},
            "files": [
                {
                    "field_key": "public_file",
                    "name": "source.docx",
                    "content_base64": base64.b64encode(
                        synthetic_docx(
                            "Новый оригинал не должен подменять старое основание процесса"
                        )
                    ).decode(),
                }
            ],
        },
    )
    assert upload.status_code == 200 and upload.json()["current_version"] == 2, upload.text
    sign_in_as(client, factory, setup["sample"]["admin"])
    after = client.get(f"/api/v1/process-versions/{version_id}")
    assert after.status_code == 200, after.text
    assert after.json()["steps"] == before["steps"]
    assert after.json()["transitions"] == before["transitions"]
    sign_in_as(client, factory, setup["sample"]["viewer"])
    basis = client.get(f"/api/v1/process-versions/{version_id}/steps/start/basis?mode=viewer")
    assert basis.status_code == 200, basis.text
    item = basis.json()["items"][0]
    assert item["restricted"] is False
    assert item["anchor"]["version_id"] == setup["extraction"]["version_id"]
    assert item["anchor"]["block_id"] == setup["extraction"]["blocks"][0]["id"]
    assert item["blocks"][0]["text"] == "Синтетическое основание описания административного шага"
    downloaded = client.get(f"/api/v1/files/{setup['extraction']['file_id']}/download?mode=viewer")
    assert downloaded.status_code == 200 and downloaded.content == setup["original"]


def test_reader_cannot_edit_publish_or_clone_process_and_admin_has_no_scope_bypass(client, factory):
    setup = source_process(client, factory)
    viewer_headers = sign_in_as(client, factory, setup["sample"]["viewer"])
    version_id, process_id = setup["version"]["id"], setup["process"]["id"]
    for endpoint, method, body in (
        (
            f"/process-versions/{version_id}",
            "PUT",
            {
                "revision": setup["version"]["revision"],
                "effective_from": "2026-01-01",
                "steps": [],
                "transitions": [],
            },
        ),
        (
            f"/process-versions/{version_id}/publish",
            "POST",
            {"revision": setup["version"]["revision"]},
        ),
        (f"/processes/{process_id}/versions", "POST", {}),
    ):
        response = client.request(method, "/api/v1" + endpoint, headers=viewer_headers, json=body)
        assert response.status_code == 403, response.text
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["admin"].id)
        ).permissions = []
    sign_in_as(client, factory, setup["sample"]["admin"])
    for endpoint in (
        f"/processes/{process_id}",
        f"/process-versions/{version_id}",
        f"/process-versions/{version_id}/steps/start/basis",
    ):
        denied = client.get("/api/v1" + endpoint)
        assert denied.status_code == 404, denied.text
    listed = client.get("/api/v1/processes", params={"q": setup["process"]["name"]})
    assert listed.status_code == 200 and listed.json() == [], listed.text
