"""SEARCH-001 behavioral contract on real PostgreSQL, with synthetic sources only."""

from uuid import uuid4

from app.content.indexing import index_metadata
from app.registry.models import Document, DocumentACL
from tests.test_content_security import actors_and_document, sign_in_as


def corpus(factory):
    sample = actors_and_document(
        factory,
        [
            {
                "key": "title",
                "label": "Название",
                "type": "text",
                "system": "title",
                "filterable": True,
                "exportable": True,
            },
            {
                "key": "reg_number",
                "label": "Номер",
                "type": "text",
                "system": "reg_number",
                "filterable": True,
            },
            {"key": "public", "label": "Текст", "type": "textarea"},
            {"key": "secret", "label": "Скрыто", "type": "text", "read_roles": ["admin"]},
            {
                "key": "conditional",
                "label": "Условное",
                "type": "text",
                "visible_when": {"field": "secret", "op": "present"},
            },
        ], reg_number="12-Р/2026",
    )
    with factory.begin() as db:
        source = db.get(Document, sample["document"])
        source.title = "Содержание территорий"
        source.custom_data = {
            "public": "Жилищно-коммунальное хозяйство",
            "secret": "Скрытыймаркер",
            "conditional": "Скрытаяулика",
        }
        index_metadata(db, source.id)
        other = Document(
            card_state="published",
            publication_targets=["internal", "shared"],
            registry_id=source.registry_id,
            document_type_id=source.document_type_id,
            schema_version_id=source.schema_version_id,
            created_by=sample["editor"].id,
            title="Другой документ",
            reg_number="13-Р/2026",
            custom_data={"public": "Содержание территорий"},
        )
        db.add(other)
        db.flush()
        index_metadata(db, other.id)
        sample["other"] = other.id
    return sample


def search(client, headers, **params):
    response = client.post("/api/v1/search", headers=headers, json=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_general_query_number_and_registration_date_are_intersected(client, factory):
    from datetime import date

    sample = actors_and_document(
        factory,
        [
            {
                "key": "title",
                "label": "Название",
                "type": "text",
                "system": "title",
                "filterable": True,
            },
            {
                "key": "number",
                "label": "Номер",
                "type": "text",
                "system": "reg_number",
                "filterable": True,
            },
            {
                "key": "date",
                "label": "Дата",
                "type": "date",
                "system": "reg_date",
                "filterable": True,
            },
        ], reg_number="42-П", reg_date=date(2026, 10, 16),
    )
    with factory.begin() as db:
        source = db.get(Document, sample["document"])
        source.title = "Озеленение"
        index_metadata(db, source.id)
        for number, day in [("42-П/other", 17), ("43-П", 16)]:
            document = Document(
                card_state="published",
                publication_targets=["internal", "shared"],
                registry_id=source.registry_id,
                document_type_id=source.document_type_id,
                schema_version_id=source.schema_version_id,
                created_by=source.created_by,
                title="Озеленение",
                reg_number=number,
                reg_date=date(2026, 10, day),
            )
            db.add(document)
            db.flush()
            index_metadata(db, document.id)
    headers = sign_in_as(client, factory, sample["viewer"])
    for query in ("", "озеленение"):
        result = search(
            client,
            headers,
            query=query,
            reg_number="42-П",
            date_from="2026-10-16",
            date_to="2026-10-16",
        )
        assert result["total"] == 1
        assert result["items"][0]["document"]["id"] == str(sample["document"])


def test_unified_or_ranking_scopes_and_browse(client, factory):
    sample = corpus(factory)
    headers = sign_in_as(client, factory, sample["viewer"])
    result = search(client, headers, query="содержание территорий", fuzzy=False)
    assert result["total"] == 2
    assert result["items"][0]["document"]["id"] == str(sample["document"])
    assert all(item["matches"] for item in result["items"])
    assert search(client, headers, query="", page_size=1)["total"] == 2
    assert search(client, headers, query="содержание", scope="content")["total"] == 0
    assert search(client, headers, query="ЖКХ", fuzzy=False)["total"] == 1
    morphology = search(client, headers, query="территория", fuzzy=False)
    assert any(match.get("highlights") for item in morphology["items"] for match in item["matches"])
    assert search(client, headers, query='"территорий содержание"', fuzzy=True)["total"] == 0


def test_number_normalization_never_approximates_and_hidden_sources_do_not_count(client, factory):
    sample = corpus(factory)
    headers = sign_in_as(client, factory, sample["viewer"])
    result = search(client, headers, query="12 р 2026", fuzzy=True)
    assert result["total"] == 1
    assert result["items"][0]["document"]["id"] == str(sample["document"])
    assert search(client, headers, query="14-р/2026", fuzzy=True)["total"] == 0
    for query in ("Скрытыймаркер", "Скрытаяулика"):
        assert search(client, headers, query=query)["total"] == 0


def test_cursor_ties_current_authorization_and_parameter_binding(client, factory):
    sample = corpus(factory)
    headers = sign_in_as(client, factory, sample["viewer"])
    first = search(client, headers, query="содержание", page_size=1)
    assert first["next_cursor"]
    second = search(client, headers, query="содержание", page_size=1, cursor=first["next_cursor"])
    assert first["items"][0]["document"]["id"] != second["items"][0]["document"]["id"]
    assert second["next_cursor"] is None
    assert (
        client.post(
            "/api/v1/search",
            headers=headers,
            json={"query": "другой", "cursor": first["next_cursor"]},
        ).status_code
        == 422
    )
    with factory.begin() as db:
        db.add(
            DocumentACL(
                document_id=sample["other"], user_id=sample["editor"].id, permissions=["read"]
            )
        )
    assert search(client, headers, query="содержание")["total"] == 1


def test_search_beyond_ten_thousand_cards_without_corpus_scan(client, factory):
    sample = corpus(factory)
    with factory.begin() as db:
        source = db.get(Document, sample["document"])
        db.bulk_insert_mappings(
            Document,
            [
                dict(
                    id=uuid4(),
                    registry_id=source.registry_id,
                    document_type_id=source.document_type_id,
                    schema_version_id=source.schema_version_id,
                    created_by=sample["editor"].id,
                    title="Нейтральный",
                    custom_data={},
                )
                for _ in range(10010)
            ],
        )
    headers = sign_in_as(client, factory, sample["viewer"])
    assert search(client, headers, query="содержание", fuzzy=False)["total"] == 2


def test_dynamic_group_fields_and_reference_labels_are_searchable_when_configured(client, factory):
    from app.registry.models import ReferenceItem, ReferenceSet

    sample = actors_and_document(
        factory,
        [
            {
                "key": "rows",
                "label": "Строки",
                "type": "group",
                "children": [
                    {"key": "body", "label": "Текст", "type": "text"},
                    {"key": "private", "label": "Закрыто", "type": "text", "read_roles": ["admin"]},
                ],
            },
            {"key": "agency", "label": "Орган", "type": "reference", "searchable": True},
        ],
    )
    with factory.begin() as db:
        refset = ReferenceSet(code="search-agencies", name="Органы")
        db.add(refset)
        db.flush()
        value = ReferenceItem(reference_set_id=refset.id, label="Благоустройство")
        db.add(value)
        db.flush()
        document = db.get(Document, sample["document"])
        document.custom_data = {
            "rows": [{"body": "Озеленение", "private": "Закрытыйребенок"}],
            "agency": str(value.id),
        }
        index_metadata(db, document.id)
    headers = sign_in_as(client, factory, sample["viewer"])
    assert search(client, headers, query="озеленения", fuzzy=False)["total"] == 1
    assert search(client, headers, query="благоустройство", fuzzy=False)["total"] == 1
    assert search(client, headers, query="Закрытыйребенок")["total"] == 0


def test_admin_index_status_and_controlled_background_rebuild(client, factory):
    from app.content.worker import process_backlog
    from app.registry.models import RegistryAccess

    sample = corpus(factory)
    with factory.begin() as db:
        db.add(
            RegistryAccess(
                registry_id=sample["registry"],
                user_id=sample["admin"].id,
                permissions=["read", "download", "update"],
            )
        )
    headers = sign_in_as(client, factory, sample["admin"])
    search(client, headers, query="территории", fuzzy=False)
    status = client.get("/api/v1/admin/search/status")
    assert status.status_code == 200
    assert status.json()["indexed_documents"] == 2
    assert status.json()["latency"]["requests"] >= 1
    rebuilt = client.post(
        "/api/v1/admin/search/rebuild",
        headers=headers,
        json={"registry_id": str(sample["registry"]), "reason": "Synthetic rebuild"},
    )
    assert rebuilt.status_code == 202, rebuilt.text
    assert client.get("/api/v1/admin/search/status").json()["pending_documents"] == 2
    process_backlog(factory)
    final = client.get("/api/v1/admin/search/status").json()
    assert final["pending_documents"] == 0
    assert final["last_rebuild"]["state"] == "completed"


def test_page_serialization_does_not_add_per_document_sql(factory):
    from app.content.search import ContentSearchParameters
    from tests.test_task011_search_performance import indexed_corpus, measured_search

    sample, _ = indexed_corpus(factory, count=30)
    _, small = measured_search(
        factory, sample["viewer"], ContentSearchParameters(text="пилот", fuzzy=False, page_size=2)
    )
    _, large = measured_search(
        factory, sample["viewer"], ContentSearchParameters(text="пилот", fuzzy=False, page_size=20)
    )
    assert large <= small + 3


def test_search_export_uses_identical_or_query_and_filters(client, factory):
    import json

    sample = corpus(factory)
    sign_in_as(client, factory, sample["viewer"])
    params = {"query": "содержание", "registry_ids": [str(sample["registry"])], "fuzzy": False}
    result = client.get(
        "/api/v1/search/export", params={"parameters": json.dumps(params), "format": "csv"}
    )
    assert result.status_code == 200, result.text
    assert "Содержание территорий" in result.content.decode("utf-8-sig")
    assert "Другой документ" in result.content.decode("utf-8-sig")


def test_decimal_presence_nested_same_key_and_normalized_scores(client, factory):
    sample = actors_and_document(factory, [
        {"key": "amount", "label": "Сумма", "type": "decimal"},
        {"key": "conditional", "label": "Условное", "type": "text", "visible_when": {"field": "amount", "op": "present"}},
        {"key": "gate", "label": "Основание", "type": "text"},
        {"key": "rows", "label": "Строки", "type": "group", "children": [
            {"key": "gate", "label": "Вложенное", "type": "text", "visible_when": {"field": "gate", "op": "present"}}]},
        {"key": "number", "label": "Номер", "type": "text", "system": "reg_number"},
    ], reg_number="АБВ")
    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        document.custom_data = {"amount": "1.2", "conditional": "Разрешенныймаркер", "gate": "да", "rows": [{"gate": "Вложенныймаркер"}]}
        index_metadata(db, document.id)
    headers = sign_in_as(client, factory, sample["viewer"])
    assert search(client, headers, query="Разрешенныймаркер", fuzzy=False)["total"] == 1
    assert search(client, headers, query="Вложенныймаркер", fuzzy=False)["total"] == 1
    result = search(client, headers, query="АБВ", fuzzy=False)
    assert result["total"] == 1
    assert 0 <= result["items"][0]["score"] <= 1
