"""Positive service contracts preserve resource identities and usable pagination."""

import base64
from uuid import UUID

from app.content.worker import process_extractions
from app.registry.models import RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task005_content import synthetic_docx
from tests.test_task008_knowledge_acl import publish_case, publish_template
from tests.test_task010_service_acl import service_get, service_sample


def assert_page(body, *, page, page_size, total):
    assert set(body) == {"items", "total", "page", "page_size"}
    assert (body["page"], body["page_size"], body["total"]) == (page, page_size, total)


def second_original(client, factory, setup):
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    card = client.get(f"/api/v1/documents/{setup['source']['id']}").json()
    original = synthetic_docx(
        "Синтетическая новая редакция"
        "</w:t></w:r></w:p><w:p><w:r><w:t>Самостоятельный второй абзац"
        "</w:t></w:r></w:p><w:p><w:r><w:t>Самостоятельный третий абзац"
    )
    changed = client.put(
        f"/api/v1/documents/{card['id']}",
        headers=headers,
        json={
            "revision": card["revision"],
            "values": {"title": "Synthetic revised metadata title"},
            "files": [
                {
                    "field_key": "public_file",
                    "name": "source.docx",
                    "content_base64": base64.b64encode(original).decode(),
                }
            ],
        },
    )
    assert changed.status_code == 200, changed.text
    latest_file = max(changed.json()["files"], key=lambda item: item["version"])
    requested = client.post(f"/api/v1/files/{latest_file['id']}/extract", headers=headers, json={})
    assert requested.status_code == 202, requested.text
    assert process_extractions(factory, client.app.state.settings.storage_root) == 1
    extractions = client.get(f"/api/v1/documents/{card['id']}/content").json()["items"]
    extraction = max(extractions, key=lambda item: item["version"])
    assert extraction["state"] == "completed" and len(extraction["blocks"]) == 3
    return changed.json(), extraction, original


def test_metadata_revision_and_file_edition_are_distinct_paged_pinned_resources(client, factory):
    setup, _, bearer = service_sample(client, factory)
    before = service_get(
        client, bearer, f"/documents/{setup['source']['id']}/metadata-revisions"
    ).json()
    assert before["items"]
    pinned_metadata = before["items"][0]
    old_edition = service_get(
        client, bearer, f"/file-versions/{setup['extraction']['version_id']}"
    ).json()
    changed, extraction, new_bytes = second_original(client, factory, setup)
    excluded = service_get(
        client,
        bearer,
        f"/documents?registry_id={setup['sample']['registry']}&q=revised&page_size=1",
    ).json()
    # This published fixture intentionally has no filterable title; q cannot bypass that policy.
    assert_page(excluded, page=1, page_size=1, total=0)
    assert excluded["items"] == []
    catalog = service_get(
        client,
        bearer,
        f"/documents?registry_id={setup['sample']['registry']}"
        f"&document_type_id={changed['document_type_id']}&page_size=20",
    ).json()
    assert_page(catalog, page=1, page_size=20, total=3)
    assert changed["id"] in {item["id"] for item in catalog["items"]}
    first = service_get(
        client, bearer, f"/documents/{changed['id']}/metadata-revisions?page=1&page_size=1"
    ).json()
    next_page = service_get(
        client, bearer, f"/documents/{changed['id']}/metadata-revisions?page=2&page_size=1"
    ).json()
    assert_page(first, page=1, page_size=1, total=before["total"] + 1)
    assert_page(next_page, page=2, page_size=1, total=before["total"] + 1)
    assert first["items"][0]["id"] != next_page["items"][0]["id"]
    assert first["items"][0]["values"]["title"] == changed["title"]
    assert first["items"][0]["revision"] > pinned_metadata["revision"]
    assert (
        service_get(client, bearer, f"/metadata-revisions/{pinned_metadata['id']}").json()
        == pinned_metadata
    )
    editions = [
        service_get(
            client, bearer, f"/documents/{changed['id']}/file-versions?page={page}&page_size=1"
        ).json()
        for page in (1, 2)
    ]
    for page, body in enumerate(editions, 1):
        assert_page(body, page=page, page_size=1, total=2)
    assert editions[0]["items"][0]["id"] == extraction["version_id"]
    assert editions[1]["items"][0] == old_edition
    assert pinned_metadata["id"] not in {old_edition["id"], extraction["version_id"]}
    service_get(client, bearer, f"/file-versions/{pinned_metadata['id']}", 404)
    service_get(client, bearer, f"/metadata-revisions/{old_edition['id']}", 404)
    assert (
        service_get(client, bearer, f"/files/{setup['extraction']['file_id']}/download").content
        == setup["original"]
    )
    assert (
        service_get(client, bearer, f"/files/{extraction['file_id']}/download").content == new_bytes
    )


def test_extraction_and_block_pagination_reassembles_exact_order_without_retargeting_old_blocks(
    client, factory
):
    setup, _, bearer = service_sample(client, factory)
    old_block = service_get(
        client, bearer, f"/blocks/{setup['extraction']['blocks'][0]['id']}"
    ).json()
    _, extraction, _ = second_original(client, factory, setup)
    content_pages = [
        service_get(
            client, bearer, f"/documents/{setup['source']['id']}/content?page={page}&page_size=1"
        ).json()
        for page in (1, 2)
    ]
    for page, body in enumerate(content_pages, 1):
        assert_page(body, page=page, page_size=1, total=2)
    assert {body["items"][0]["id"] for body in content_pages} == {
        setup["extraction"]["id"],
        extraction["id"],
    }
    blocks = []
    for page in (1, 2, 3):
        body = service_get(
            client, bearer, f"/extractions/{extraction['id']}/blocks?page={page}&page_size=1"
        ).json()
        assert_page(body, page=page, page_size=1, total=3)
        blocks.extend(body["items"])
    assert len({block["id"] for block in blocks}) == 3
    assert [block["ordinal"] for block in blocks] == sorted(block["ordinal"] for block in blocks)
    assert [block["text"] for block in blocks] == [item["text"] for item in extraction["blocks"]]
    for block in blocks:
        assert (
            block["document_id"],
            block["version_id"],
            block["file_id"],
            block["extraction_id"],
        ) == (
            setup["source"]["id"],
            extraction["version_id"],
            extraction["file_id"],
            extraction["id"],
        )
        assert service_get(client, bearer, f"/blocks/{block['id']}").json() == block
    assert service_get(client, bearer, f"/blocks/{old_block['id']}").json() == old_block
    last = service_get(
        client, bearer, f"/extractions/{extraction['id']}/blocks?page=4&page_size=1"
    ).json()
    assert_page(last, page=4, page_size=1, total=3)
    assert last["items"] == []


def test_service_domain_resources_expose_pinned_relations_knowledge_numbers_and_execution(
    client, factory
):
    setup, account, bearer = service_sample(
        client,
        factory,
        operations=[
            "documents",
            "files",
            "content",
            "relations",
            "processes",
            "knowledge",
            "numbers",
            "execution",
            "events",
        ],
    )
    with factory.begin() as db:
        for identity in (setup["sample"]["editor"].id, UUID(account["user_id"])):
            db.get(RegistryAccess, (setup["process_registry"], identity)).permissions = (
                ["read", "create", "update", "download"]
                if identity == setup["sample"]["editor"].id
                else ["read", "download"]
            )
    editor = sign_in_as(client, factory, setup["sample"]["editor"])
    relation = client.post(
        "/api/v1/document-relations",
        headers=editor,
        json={
            "source_document_id": setup["source"]["id"],
            "target_document_id": setup["sample"]["documents"][1],
            "relation_type": "reference",
            "source_version_id": setup["extraction"]["version_id"],
            "source_block_id": setup["extraction"]["blocks"][0]["id"],
        },
    )
    assert relation.status_code == 201, relation.text
    template, version = publish_template(client, editor, setup)
    case = publish_case(client, editor, setup)
    started = client.post(
        "/api/v1/execution/instances",
        headers=editor,
        json={
            "process_version_id": setup["version"]["id"],
            "name": "Synthetic service retrieval execution",
        },
    )
    assert started.status_code == 201, started.text
    admin = sign_in_as(client, factory, setup["sample"]["admin"])
    series = client.post(
        "/api/v1/number-series",
        headers=admin,
        json={
            "code": "service-positive",
            "name": "Synthetic service numbers",
            "registry_id": str(setup["process_registry"]),
            "document_type_ids": [setup["source"]["document_type_id"]],
            "synthetic": True,
        },
    )
    assert series.status_code == 201, series.text
    grants = client.put(
        f"/api/v1/number-series/{series.json()['id']}/grants",
        headers=admin,
        json={
            "entries": [
                {"user_id": str(setup["sample"]["editor"].id), "permissions": ["view", "reserve"]},
                {"user_id": account["user_id"], "permissions": ["view"]},
            ]
        },
    )
    assert grants.status_code == 200, grants.text
    editor = sign_in_as(client, factory, setup["sample"]["editor"])
    reserved = client.post(
        f"/api/v1/number-series/{series.json()['id']}/reserve",
        headers=editor,
        json={"count": 2, "idempotency_key": "service-positive-reserve"},
    )
    assert reserved.status_code == 200, reserved.text
    client.cookies.clear()
    related = service_get(
        client, bearer, f"/documents/{setup['source']['id']}/relations?page_size=1"
    ).json()
    assert_page(related, page=1, page_size=1, total=1)
    assert (
        related["items"][0]
        == service_get(client, bearer, f"/relations/{relation.json()['id']}").json()
    )
    assert related["items"][0]["source_block_id"] == setup["extraction"]["blocks"][0]["id"]
    exact_template = service_get(client, bearer, f"/template-versions/{version['id']}").json()
    assert (
        exact_template["id"],
        exact_template["template_id"],
        exact_template["original"]["file_id"],
    ) == (version["id"], template["id"], setup["extraction"]["file_id"])
    exact_case = service_get(client, bearer, f"/cases/{case['id']}").json()
    assert exact_case["confirmed"] is False and exact_case["recommended"] is False
    assert exact_case["links"][0]["block_id"] == setup["extraction"]["blocks"][0]["id"]
    page_one = service_get(
        client, bearer, f"/number-series/{series.json()['id']}/entries?page=1&page_size=1"
    ).json()
    page_two = service_get(
        client, bearer, f"/number-series/{series.json()['id']}/entries?page=2&page_size=1"
    ).json()
    for page, body in enumerate((page_one, page_two), 1):
        assert_page(body, page=page, page_size=1, total=2)
    assert {page_one["items"][0]["id"], page_two["items"][0]["id"]} == {
        item["id"] for item in reserved.json()["items"]
    }
    assert (
        service_get(client, bearer, f"/number-entries/{page_one['items'][0]['id']}").json()
        == page_one["items"][0]
    )
    instances = service_get(
        client, bearer, f"/execution/instances?registry_id={setup['process_registry']}&page_size=1"
    ).json()
    assert_page(instances, page=1, page_size=1, total=1)
    exact_instance = service_get(
        client, bearer, f"/execution/instances/{started.json()['id']}"
    ).json()
    assert instances["items"][0]["id"] == exact_instance["id"] == started.json()["id"]
    assert exact_instance["process_version_id"] == setup["version"]["id"]
    assert exact_instance["state"] == "pending" and exact_instance["trajectory_restricted"] is False
