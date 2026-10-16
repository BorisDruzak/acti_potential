from datetime import date
from uuid import uuid4

import pytest


def test_external_basis_requires_https_version_date_and_user_attestation():
    from app.relations.contracts import RelationInput

    basis = {
        "url": "https://example.test/act/1",
        "title": "Synthetic external act",
        "identifier": "Synthetic 1",
        "version": "2026-01",
        "effective_date": "2026-01-01",
        "verified": True,
    }
    value = {"source_document_id": uuid4(), "relation_type": "reference", "external_target": basis}
    assert RelationInput(**value).external_target.version == "2026-01"
    for changes in (
        {"url": "http://example.test/act"},
        {"url": "https://example.test:invalid/act"},
        {"url": "https://example.test:70000/act"},
        {"url": "https://example.test/unsafe\npath"},
        {"verified": False},
        {"version": ""},
    ):
        with pytest.raises(ValueError):
            RelationInput(**{**value, "external_target": {**basis, **changes}})
    with pytest.raises(ValueError):
        RelationInput(**{**value, "target_document_id": uuid4()})


def test_confirmation_requires_reason_and_effective_date():
    from app.relations.contracts import DecisionInput

    for body in ({"reason": ""}, {"effective_date": date.today()}, {"reason": "Synthetic"}):
        with pytest.raises(ValueError):
            DecisionInput(**body)


def relation_documents(client, factory, *, fields=None):
    from app.registry.models import Document
    from tests.test_content_security import actors_and_document, sign_in_as

    fields = fields or [
        {"key": "title", "label": "Title", "type": "text", "system": "title"},
        {"key": "status", "label": "Status", "type": "text", "system": "status"},
    ]
    sample = actors_and_document(factory, fields)
    with factory.begin() as db:
        original = db.get(Document, sample["document"])
        original.title, original.status = "Synthetic original act", "Действует"
        documents = [original]
        for title in ["Synthetic changing act", "Synthetic repealing act"]:
            doc = Document(
                card_state="published",
                publication_targets=["internal", "shared"],
                registry_id=original.registry_id,
                document_type_id=original.document_type_id,
                schema_version_id=original.schema_version_id,
                created_by=original.created_by,
                title=title,
                status="Действует",
                custom_data={},
            )
            db.add(doc)
            documents.append(doc)
        db.flush()
        identities = [str(doc.id) for doc in documents]
    headers = sign_in_as(client, factory, sample["editor"])
    cards = [client.get("/api/v1/documents/" + identity).json() for identity in identities]
    sample["documents"] = identities
    return sample, cards, headers


def grant_confirmation(factory, sample):
    from app.registry.models import RegistryAccess

    with factory.begin() as db:
        row = db.get(RegistryAccess, (sample["registry"], sample["editor"].id))
        row.permissions = [*row.permissions, "confirm_relations"]


@pytest.mark.parametrize(
    "kind",
    [
        "changes",
        "repeals",
        "replaces",
        "approves",
        "attachment",
        "reference",
        "in_execution",
        "previous_revision",
        "topic",
    ],
)
def test_all_relation_types_remain_proposals_without_legal_status_mutation(client, factory, kind):
    sample, cards, headers = relation_documents(client, factory)
    response = client.post(
        "/api/v1/document-relations",
        headers=headers,
        json={
            "source_document_id": cards[1]["id"],
            "target_document_id": cards[0]["id"],
            "relation_type": kind,
            "source": "ai_suggested",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "proposed"
    assert (
        client.get(f"/api/v1/documents/{cards[0]['id']}/relations").json()["status_effects"] == []
    )
    assert client.get(f"/api/v1/documents/{cards[0]['id']}").json()["status"] == "Действует"


def test_mixed_legal_cycles_duplicates_and_selflinks_are_blocked(client, factory):
    sample, cards, headers = relation_documents(client, factory)
    body = {
        "source_document_id": cards[1]["id"],
        "target_document_id": cards[0]["id"],
        "relation_type": "changes",
    }
    assert client.post("/api/v1/document-relations", headers=headers, json=body).status_code == 201
    assert client.post("/api/v1/document-relations", headers=headers, json=body).status_code == 409
    assert (
        client.post(
            "/api/v1/document-relations",
            headers=headers,
            json={**body, "target_document_id": cards[1]["id"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/document-relations",
            headers=headers,
            json={
                "source_document_id": cards[0]["id"],
                "target_document_id": cards[1]["id"],
                "relation_type": "repeals",
            },
        ).status_code
        == 409
    )


def test_confirmation_grants_future_effects_and_immutable_decisions(client, factory):
    sample, cards, headers = relation_documents(client, factory)
    created = client.post(
        "/api/v1/document-relations",
        headers=headers,
        json={
            "source_document_id": cards[1]["id"],
            "target_document_id": cards[0]["id"],
            "relation_type": "changes",
        },
    ).json()
    endpoint = "/api/v1/document-relations/" + created["id"]
    future = {"reason": "Synthetic confirmation", "effective_date": "2099-01-01"}
    assert client.post(endpoint + "/confirm", headers=headers, json=future).status_code == 403
    grant_confirmation(factory, sample)
    confirmed = client.post(endpoint + "/confirm", headers=headers, json=future)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "confirmed" and confirmed.json()["applies_now"] is False
    assert (
        client.get(f"/api/v1/documents/{cards[0]['id']}/relations").json()["status_effects"] == []
    )
    assert client.post(endpoint + "/reject", headers=headers, json=future).status_code == 409
    current = client.post(
        "/api/v1/document-relations",
        headers=headers,
        json={
            "source_document_id": cards[2]["id"],
            "target_document_id": cards[0]["id"],
            "relation_type": "repeals",
        },
    ).json()
    assert (
        client.post(
            "/api/v1/document-relations/" + current["id"] + "/confirm",
            headers=headers,
            json={"reason": "Synthetic current effect", "effective_date": "2026-01-01"},
        ).status_code
        == 200
    )
    effects = client.get(f"/api/v1/documents/{cards[0]['id']}/relations").json()["status_effects"]
    assert [effect["relation_type"] for effect in effects] == ["repeals"]
    events = client.get(f"/api/v1/documents/{cards[0]['id']}/relation-history").json()["items"]
    assert any(event["action"] == "confirmed" and event["applies_now"] is False for event in events)
    assert client.get(f"/api/v1/documents/{cards[0]['id']}").json()["status"] == "Действует"


def test_external_deduplication_uses_identity_instead_of_title(client, factory):
    sample, cards, headers = relation_documents(client, factory)
    external = {
        "url": "https://EXAMPLE.test:443/act",
        "title": "First display name",
        "identifier": "Synthetic external 1",
        "version": "1",
        "effective_date": "2026-01-01",
        "verified": True,
    }
    body = {
        "source_document_id": cards[0]["id"],
        "relation_type": "reference",
        "external_target": external,
    }
    created = client.post("/api/v1/document-relations", headers=headers, json=body)
    assert created.status_code == 201, created.text
    duplicate = {
        **body,
        "external_target": {
            **external,
            "url": "https://example.test/act",
            "title": "Changed display name",
        },
    }
    assert (
        client.post("/api/v1/document-relations", headers=headers, json=duplicate).status_code
        == 409
    )
    basis = client.get(
        "/api/v1/document-relations/" + created.json()["id"] + "/basis?side=target"
    ).json()
    assert basis["document"] is None and basis["external_target"]["verified"] is True


def test_hidden_target_disappears_from_list_graph_history_and_direct_reads(client, factory):
    from uuid import UUID

    from app.registry.models import DocumentACL
    from tests.test_content_security import sign_in_as

    sample, cards, headers = relation_documents(client, factory)
    relation = client.post(
        "/api/v1/document-relations",
        headers=headers,
        json={
            "source_document_id": cards[1]["id"],
            "target_document_id": cards[0]["id"],
            "relation_type": "reference",
        },
    ).json()
    with factory.begin() as db:
        db.add(
            DocumentACL(
                document_id=UUID(cards[0]["id"]),
                user_id=sample["editor"].id,
                permissions=["read", "update"],
            )
        )
    sign_in_as(client, factory, sample["viewer"])
    root = f"/api/v1/documents/{cards[1]['id']}"
    listed = client.get(root + "/relations").json()
    graph = client.get(root + "/relation-graph").json()
    history = client.get(root + "/relation-history").json()
    assert listed["total"] == 0 and listed["items"] == [] and listed["status_effects"] == []
    assert graph["edges"] == [] and len(graph["nodes"]) == 1 and graph["truncated"] is False
    assert history["total"] == 0 and history["items"] == []
    for suffix in ["", "/basis?side=source"]:
        assert (
            client.get("/api/v1/document-relations/" + relation["id"] + suffix).status_code == 404
        )


def test_concurrent_legal_proposals_cannot_publish_opposite_edges(client, factory):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.relations.contracts import RelationInput
    from app.relations.service import propose

    sample, cards, headers = relation_documents(client, factory)
    request = SimpleNamespace(client=None, state=SimpleNamespace(request_id="synthetic-relations"))

    def create(direction):
        try:
            with factory.begin() as db:
                propose(
                    db,
                    request,
                    sample["editor"],
                    RelationInput(
                        source_document_id=cards[direction]["id"],
                        target_document_id=cards[1 - direction]["id"],
                        relation_type="changes" if direction == 0 else "repeals",
                    ),
                )
            return 201
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, [0, 1])) == [201, 409]


def test_relation_decisions_provenance_and_history_are_immutable_in_database(client, factory):
    from uuid import UUID

    from sqlalchemy import delete, update
    from sqlalchemy.exc import DBAPIError

    from app.relations.models import DocumentRelation, RelationEvent

    sample, cards, headers = relation_documents(client, factory)
    created = client.post(
        "/api/v1/document-relations",
        headers=headers,
        json={
            "source_document_id": cards[1]["id"],
            "target_document_id": cards[0]["id"],
            "relation_type": "reference",
        },
    ).json()
    identity = UUID(created["id"])
    with pytest.raises(DBAPIError):
        with factory.begin() as db:
            db.execute(
                update(DocumentRelation)
                .where(DocumentRelation.id == identity)
                .values(description="rewrite")
            )
    grant_confirmation(factory, sample)
    assert (
        client.post(
            "/api/v1/document-relations/" + created["id"] + "/reject",
            headers=headers,
            json={"reason": "Synthetic rejected basis", "effective_date": "2026-01-01"},
        ).status_code
        == 200
    )
    for statement in [
        update(DocumentRelation).where(DocumentRelation.id == identity).values(reason="rewrite"),
        delete(DocumentRelation).where(DocumentRelation.id == identity),
        update(RelationEvent).where(RelationEvent.relation_id == identity).values(reason="rewrite"),
    ]:
        with pytest.raises(DBAPIError):
            with factory.begin() as db:
                db.execute(statement)
    assert (
        client.get("/api/v1/document-relations/" + created["id"]).json()["reason"]
        == "Synthetic rejected basis"
    )
