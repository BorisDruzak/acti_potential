"""Task006 source-basis authorization against real persisted versions and extraction."""

import base64
from uuid import UUID, uuid4

from sqlalchemy import select

from app.content.worker import process_extractions
from app.registry.models import DocumentACL, RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task005_content import synthetic_docx

FIELDS = [
    {"key": "title", "label": "Title", "type": "text", "system": "title"},
    {"key": "status", "label": "Status", "type": "text", "system": "status"},
    {"key": "public_file", "label": "Public original", "type": "file"},
    {
        "key": "private_file",
        "label": "Private original",
        "type": "file",
        "read_roles": ["editor"],
        "write_roles": ["editor"],
    },
]


def setup_basis(client, factory):
    from tests.test_task006_relations import relation_documents

    sample, cards, headers = relation_documents(client, factory, fields=FIELDS)
    public_bytes = synthetic_docx("Открытое исходное основание первой версии")
    private_bytes = synthetic_docx("SYNTHETIC-PRIVATE-BASIS-FRAGMENT")
    source, target, foreign = cards
    response = client.put(
        f"/api/v1/documents/{source['id']}",
        headers=headers,
        json={
            "revision": source["revision"],
            "values": {},
            "files": [
                {
                    "field_key": key,
                    "name": name,
                    "content_base64": base64.b64encode(content).decode(),
                }
                for key, name, content in (
                    ("public_file", "public-basis.docx", public_bytes),
                    ("private_file", "private-basis.docx", private_bytes),
                )
            ],
        },
    )
    assert response.status_code == 200, response.text
    for file in response.json()["files"]:
        queued = client.post(f"/api/v1/files/{file['id']}/extract", headers=headers, json={})
        assert queued.status_code == 202, queued.text
    # The production worker bounds each invocation to one extraction heartbeat.
    assert process_extractions(factory, client.app.state.settings.storage_root) == 1
    assert process_extractions(factory, client.app.state.settings.storage_root) == 1
    content = client.get(f"/api/v1/documents/{source['id']}/content").json()["items"]
    public = next(item for item in content if item["name"] == "public-basis.docx")
    private = next(item for item in content if item["name"] == "private-basis.docx")
    assert public["state"] == private["state"] == "completed"
    return sample, cards, headers, public, private, public_bytes


def propose_basis(client, headers, source, target, extraction):
    response = client.post(
        "/api/v1/document-relations",
        headers=headers,
        json={
            "source_document_id": source["id"],
            "target_document_id": target["id"],
            "source_version_id": extraction["version_id"],
            "source_block_id": extraction["blocks"][0]["id"],
            "relation_type": "reference",
            "source": "manual",
            "description": "Synthetic exact basis",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_basis_and_options_do_not_reveal_hidden_originals_or_blocks(client, factory):
    sample, cards, headers, public, private, _ = setup_basis(client, factory)
    visible = propose_basis(client, headers, cards[0], cards[1], public)
    hidden = propose_basis(client, headers, cards[0], cards[1], private)
    sign_in_as(client, factory, sample["viewer"])
    basis = client.get(f"/api/v1/document-relations/{visible['id']}/basis?side=source&mode=viewer")
    assert basis.status_code == 200, basis.text
    assert basis.json()["anchor"]["block_id"] == public["blocks"][0]["id"]
    assert {item["id"] for item in basis.json()["files"]} == {public["file_id"]}
    assert client.get(f"/api/v1/document-relations/{hidden['id']}").status_code == 404
    assert (
        client.get(f"/api/v1/document-relations/{hidden['id']}/basis?side=source").status_code
        == 404
    )
    options = client.get(f"/api/v1/documents/{cards[0]['id']}/relation-options?mode=viewer")
    assert options.status_code == 200, options.text
    listed = client.get(f"/api/v1/documents/{cards[0]['id']}/relations").json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == visible["id"]
    serialized = basis.text + options.text + str(listed)
    for secret in (
        private["file_id"],
        private["id"],
        private["blocks"][0]["id"],
        "private-basis.docx",
        "SYNTHETIC-PRIVATE-BASIS-FRAGMENT",
    ):
        assert secret not in serialized


def test_download_revocation_removes_pinned_relation_without_removing_card_access(client, factory):
    sample, cards, headers, public, _, _ = setup_basis(client, factory)
    relation = propose_basis(client, headers, cards[0], cards[1], public)
    sign_in_as(client, factory, sample["viewer"])
    endpoint = f"/api/v1/document-relations/{relation['id']}/basis?side=source"
    assert client.get(endpoint).status_code == 200
    with factory.begin() as db:
        grant = db.scalar(
            select(RegistryAccess).where(
                RegistryAccess.registry_id == sample["registry"],
                RegistryAccess.user_id == sample["viewer"].id,
            )
        )
        grant.permissions = ["read"]
    assert client.get(f"/api/v1/documents/{cards[0]['id']}").status_code == 200
    assert client.get(endpoint).status_code == 404
    assert client.get(f"/api/v1/document-relations/{relation['id']}").status_code == 404
    listed = client.get(f"/api/v1/documents/{cards[1]['id']}/relations").json()
    assert listed["total"] == 0 and listed["items"] == [] and listed["status_effects"] == []
    graph = client.get(f"/api/v1/documents/{cards[1]['id']}/relation-graph").json()
    assert graph["edges"] == []
    with factory.begin() as db:
        grant = db.scalar(
            select(RegistryAccess).where(
                RegistryAccess.registry_id == sample["registry"],
                RegistryAccess.user_id == sample["viewer"].id,
            )
        )
        grant.permissions = ["read", "download"]
    assert client.get(endpoint).status_code == 200


def test_hidden_foreign_version_is_indistinguishable_from_missing_version(client, factory):
    sample, cards, headers, _, _, _ = setup_basis(client, factory)
    foreign = client.put(
        f"/api/v1/documents/{cards[2]['id']}",
        headers=headers,
        json={
            "revision": cards[2]["revision"],
            "values": {},
            "files": [
                {
                    "name": "foreign.docx",
                    "content_base64": base64.b64encode(synthetic_docx()).decode(),
                }
            ],
        },
    )
    assert foreign.status_code == 200, foreign.text
    options = client.get(f"/api/v1/documents/{cards[2]['id']}/relation-options").json()
    foreign_version = options["versions"][0]["id"]
    body = {
        "source_document_id": cards[0]["id"],
        "target_document_id": cards[1]["id"],
        "source_version_id": foreign_version,
        "relation_type": "reference",
    }
    mismatch = client.post("/api/v1/document-relations", headers=headers, json=body)
    assert (
        mismatch.status_code == 422
        and mismatch.json()["error"]["code"] == "relation_basis_mismatch"
    )
    with factory.begin() as db:
        db.add(
            DocumentACL(
                document_id=UUID(cards[2]["id"]),
                user_id=sample["viewer"].id,
                permissions=["read", "download"],
            )
        )
    hidden = client.post("/api/v1/document-relations", headers=headers, json=body)
    missing = client.post(
        "/api/v1/document-relations",
        headers=headers,
        json={**body, "source_version_id": str(uuid4())},
    )
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"] == missing.json()["error"]
    assert foreign_version not in hidden.text


def test_admin_role_without_content_grant_cannot_read_relation_basis(client, factory):
    sample, cards, headers, public, _, _ = setup_basis(client, factory)
    relation = propose_basis(client, headers, cards[0], cards[1], public)
    sign_in_as(client, factory, sample["admin"])
    for endpoint in (
        f"/documents/{cards[0]['id']}",
        f"/document-relations/{relation['id']}",
        f"/document-relations/{relation['id']}/basis?side=source",
        f"/documents/{cards[0]['id']}/relation-options",
    ):
        response = client.get("/api/v1" + endpoint)
        assert response.status_code == 404, response.text
        assert "Открытое исходное основание" not in response.text


def test_basis_keeps_old_original_and_block_after_new_file_version(client, factory):
    sample, cards, headers, public, _, original_bytes = setup_basis(client, factory)
    relation = propose_basis(client, headers, cards[0], cards[1], public)
    card = client.get(f"/api/v1/documents/{cards[0]['id']}").json()
    newer = client.put(
        f"/api/v1/documents/{cards[0]['id']}",
        headers=headers,
        json={
            "revision": card["revision"],
            "values": {"title": "Current permitted metadata"},
            "files": [
                {
                    "field_key": "public_file",
                    "name": "public-basis.docx",
                    "content_base64": base64.b64encode(
                        synthetic_docx("Новый оригинал: другое основание второй версии")
                    ).decode(),
                }
            ],
        },
    )
    assert newer.status_code == 200, newer.text
    assert newer.json()["current_version"] == card["current_version"] + 1
    latest = max(newer.json()["files"], key=lambda file: file["version"])
    queued = client.post(f"/api/v1/files/{latest['id']}/extract", headers=headers, json={})
    assert queued.status_code == 202, queued.text
    assert process_extractions(factory, client.app.state.settings.storage_root) == 1
    sign_in_as(client, factory, sample["viewer"])
    response = client.get(
        f"/api/v1/document-relations/{relation['id']}/basis?side=source&mode=viewer"
    )
    assert response.status_code == 200, response.text
    basis = response.json()
    assert basis["document"]["title"] == "Current permitted metadata"
    assert basis["anchor"]["version_id"] == public["version_id"] and basis["anchor"]["version"] == 1
    assert basis["anchor"]["block_id"] == public["blocks"][0]["id"]
    assert basis["blocks"][0]["text"] == "Открытое исходное основание первой версии"
    assert {item["id"] for item in basis["files"]} == {public["file_id"]}
    downloaded = client.get(f"/api/v1/files/{public['file_id']}/download?mode=viewer")
    assert downloaded.status_code == 200 and downloaded.content == original_bytes
    assert "Новый оригинал" not in response.text


def test_same_document_lineage_requires_newer_version_and_keeps_exact_old_basis(client, factory):
    sample, cards, headers, old, _, original_bytes = setup_basis(client, factory)
    document_id = cards[0]["id"]
    card = client.get(f"/api/v1/documents/{document_id}").json()
    uploaded = client.put(
        f"/api/v1/documents/{document_id}",
        headers=headers,
        json={
            "revision": card["revision"],
            "values": {},
            "files": [
                {
                    "field_key": "public_file",
                    "name": "public-basis.docx",
                    "content_base64": base64.b64encode(
                        synthetic_docx("Следующая редакция синтетического акта")
                    ).decode(),
                }
            ],
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    latest = max(uploaded.json()["files"], key=lambda file: file["version"])
    queued = client.post(f"/api/v1/files/{latest['id']}/extract", headers=headers, json={})
    assert queued.status_code == 202, queued.text
    assert process_extractions(factory, client.app.state.settings.storage_root) == 1
    versions = client.get(f"/api/v1/documents/{document_id}/relation-options").json()["versions"]
    newest = next(
        version
        for version in versions
        if any(file["id"] == latest["id"] for file in version["files"])
    )
    body = {
        "source_document_id": document_id,
        "target_document_id": document_id,
        "source_version_id": newest["id"],
        "target_version_id": old["version_id"],
        "target_block_id": old["blocks"][0]["id"],
        "relation_type": "previous_revision",
        "description": "Явная преемственность редакций одного документа",
    }
    response = client.post("/api/v1/document-relations", headers=headers, json=body)
    assert response.status_code == 201, response.text
    relation = response.json()
    assert relation["source_version"] > relation["target_version"] == 1
    for wrong in (
        {
            "source_version_id": old["version_id"],
            "target_version_id": newest["id"],
            "target_block_id": None,
        },
        {"source_version_id": old["version_id"], "target_version_id": old["version_id"]},
    ):
        denied = client.post("/api/v1/document-relations", headers=headers, json={**body, **wrong})
        assert denied.status_code == 422, denied.text
        assert denied.json()["error"]["code"] == "relation_version_order"
    sign_in_as(client, factory, sample["viewer"])
    basis = client.get(f"/api/v1/document-relations/{relation['id']}/basis?side=target&mode=viewer")
    assert basis.status_code == 200, basis.text
    assert basis.json()["anchor"]["version_id"] == old["version_id"]
    assert basis.json()["anchor"]["block_id"] == old["blocks"][0]["id"]
    assert basis.json()["blocks"][0]["text"] == "Открытое исходное основание первой версии"
    downloaded = client.get(f"/api/v1/files/{old['file_id']}/download?mode=viewer")
    assert downloaded.status_code == 200 and downloaded.content == original_bytes
