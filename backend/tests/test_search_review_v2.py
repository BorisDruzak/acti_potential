"""Reproductions from the single independent SEARCH-001 functional review."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.content.chunks import index_chunks
from app.content.indexing import index_metadata
from app.content.models import ContentExtraction, DocumentBlock
from app.registry.models import Document, DocumentFile, DocumentVersion
from tests.test_content_security import actors_and_document, sign_in_as
from tests.test_search_engine_v2 import corpus, search
from tests.test_task010_service_acl import service_sample


@pytest.mark.parametrize(
    "kind,values",
    [
        ("date", ["2026-10-01", "2026-10-02"]),
        ("datetime", ["2026-10-01T10:00:00+00:00", "2026-10-02T10:00:00+00:00"]),
    ],
)
def test_custom_temporal_cursor_round_trip(client, factory, kind, values):
    sample = actors_and_document(
        factory, [{"key": "when", "label": "Дата", "type": kind, "filterable": True}]
    )
    with factory.begin() as db:
        original = db.get(Document, sample["document"])
        original.custom_data = {"when": values[0]}
        db.add(
            Document(
                registry_id=original.registry_id,
                document_type_id=original.document_type_id,
                schema_version_id=original.schema_version_id,
                created_by=sample["editor"].id,
                custom_data={"when": values[1]},
            )
        )
    headers = sign_in_as(client, factory, sample["viewer"])
    params = {"query": "", "sort": "custom:when", "direction": "asc", "page_size": 1}
    first = search(client, headers, **params)
    second = search(client, headers, **params, cursor=first["next_cursor"])
    assert first["items"][0]["document"]["id"] != second["items"][0]["document"]["id"]


def test_fuzzy_export_uses_same_transaction_profile_as_screen(client, factory):
    import json

    sample = corpus(factory)
    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        document.custom_data = {"public": "Благоустройство"}
        document.revision += 1
        index_metadata(db, document.id)
        db.execute(select(func.set_config("pg_trgm.word_similarity_threshold", "0.99", False)))
    headers = sign_in_as(client, factory, sample["viewer"])
    params = {"query": "благоустроиство", "fuzzy": True}
    assert search(client, headers, **params)["total"] == 1
    result = client.get("/api/v1/search/export", params={"parameters": json.dumps(params)})
    assert result.status_code == 200
    assert "Содержание территорий" in result.content.decode("utf-8-sig")
    legacy = client.get(
        "/api/v1/content/search/export", params={"text": params["query"], "fuzzy": True}
    )
    assert legacy.status_code == 200
    assert "Содержание территорий" in legacy.content.decode("utf-8-sig")


@pytest.mark.parametrize("filters", ["{", "[]", "null", '"text"'])
def test_legacy_malformed_filters_are_422(client, factory, filters):
    sample = corpus(factory)
    sign_in_as(client, factory, sample["viewer"])
    for path in ("/api/v1/content/search", "/api/v1/content/search/export"):
        assert (
            client.get(path, params={"text": "территория", "filters": filters}).status_code == 422
        )
    assert client.get("/api/v1/content/search", params={"text": " "}).status_code == 422


def test_metadata_chunk_scope_is_rechecked_after_documents_operation_revocation(client, factory):
    from app.service_api.models import ServiceAccount

    setup, account, bearer = service_sample(client, factory)
    title = setup["source"]["title"]
    response = client.post(
        "/api/service/v1/retrieval", headers=bearer, json={"query": title, "scope": "metadata"}
    )
    assert response.status_code == 200
    chunk = response.json()["items"][0]
    assert chunk["source_type"] == "metadata"
    with factory.begin() as db:
        db.get(ServiceAccount, UUID(account["id"])).operations = ["content"]
    for suffix in ("", "/neighbors"):
        assert (
            client.get(
                f"/api/service/v1/chunks/{chunk['chunk_id']}" + suffix, headers=bearer
            ).status_code
            == 404
        )


def test_synonym_context_points_to_late_relevant_chunk(client, factory):
    setup, _, bearer = service_sample(client, factory)
    with factory.begin() as db:
        document = db.get(Document, UUID(setup["source"]["id"]))
        document.current_version += 1
        version = DocumentVersion(
            document_id=document.id,
            version=document.current_version,
            created_by=document.created_by,
        )
        db.add(version)
        db.flush()
        file = DocumentFile(
            document_id=document.id,
            version_id=version.id,
            name="long-context.docx",
            size=1,
            mime="application/octet-stream",
            sha256="0" * 64,
            storage_key=uuid4().hex,
            created_by=document.created_by,
        )
        db.add(file)
        db.flush()
        extraction = ContentExtraction(
            document_id=document.id,
            file_id=file.id,
            version_id=version.id,
            sha256="0" * 64,
            fingerprint=uuid4().hex,
            parser="docx",
            parser_version="synthetic",
            state="completed",
        )
        db.add(extraction)
        db.flush()
        db.add(
            DocumentBlock(
                id=uuid4(),
                extraction_id=extraction.id,
                document_id=document.id,
                kind="paragraph",
                ordinal=0,
                anchor="paragraph:1",
                text="Нейтральный абзац. " * 450 + "Жилищно-коммунальное хозяйство района.",
            )
        )
        db.flush()
        index_chunks(db, extraction.id)
    result = client.post(
        "/api/service/v1/retrieval", headers=bearer, json={"query": "ЖКХ", "scope": "content"}
    )
    assert result.status_code == 200
    assert result.json()["items"]
    assert "Жилищно-коммунальное хозяйство" in result.json()["items"][0]["text"]


def test_file_scores_remain_normalized_with_maximum_configured_weight(client, factory):
    setup, _, bearer = service_sample(client, factory)
    client.app.state.settings.search_weights = {"content": 1.0}
    query = setup["extraction"]["blocks"][0]["text"].split()[0]
    response = client.post(
        "/api/service/v1/search",
        headers=bearer,
        json={"query": query, "scope": "content", "fuzzy": False},
    )
    assert response.status_code == 200
    assert response.json()["items"]
    assert all(0 <= item["score"] <= 1 for item in response.json()["items"])


def test_postgresql_search_timeout_returns_safe_refinement_error(client, factory, monkeypatch):
    import json

    import app.content.engine as engine

    sample = corpus(factory)
    headers = sign_in_as(client, factory, sample["viewer"])
    configure = engine.configure_search

    def expire(session):
        configure(session)
        session.execute(select(func.set_config("statement_timeout", "1", True)))
        session.execute(select(func.pg_sleep(0.02)))

    monkeypatch.setattr(engine, "configure_search", expire)
    for path in (
        "/api/v1/search",
        "/api/v1/search/export",
        "/api/v1/content/search",
        "/api/v1/content/search/export",
    ):
        if path == "/api/v1/search":
            response = client.post(path, headers=headers, json={"query": "содержание"})
        else:
            response = client.get(
                path,
                params={"parameters": json.dumps({"query": "содержание"})}
                if path == "/api/v1/search/export"
                else {"text": "содержание"},
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "search_query_too_broad"
        assert "pg_sleep" not in response.text and "содержание" not in response.text
    assert client.get("/api/v1/registries").status_code == 200
