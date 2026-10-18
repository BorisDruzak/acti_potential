from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.identity.policy import Actor
from app.registry.validation import validate_schema
from app.registry.value_validation import validate_values


def numbered_schema(series_id=None):
    field = {"key": "number", "label": "Number", "type": "text", "system": "reg_number",
             "required": True}
    if series_id:
        field["number_series_id"] = str(series_id)
    return {"sections": [{"key": "main", "title": "Main", "fields": [field]}]}


def test_schema_supports_number_series_binding_without_changing_old_manual_schema():
    original = numbered_schema()
    bound = numbered_schema(uuid4())
    assert validate_schema(bound, previous=original) == bound
    assert "number_series_id" not in original["sections"][0]["fields"][0]
    for change in ({"number_series_id": str(uuid4())}, {"number_series_id": None}):
        modified = deepcopy(bound)
        modified["sections"][0]["fields"][0].update(change)
        assert validate_schema(modified, previous=bound) == modified
        assert bound["sections"][0]["fields"][0]["number_series_id"] != change["number_series_id"]


def test_number_binding_only_allows_system_text_field_without_literal_default():
    for change in ({"system": "title"}, {"default": "0012"}, {"number_series_id": "bad-id"}):
        schema = numbered_schema(uuid4())
        schema["sections"][0]["fields"][0].update(change)
        with pytest.raises(ValueError):
            validate_schema(schema)


def test_number_binding_allows_manual_input_and_can_start_blank():
    actor = Actor(uuid4(), "Synthetic editor", frozenset({"editor"}))
    schema = numbered_schema(uuid4())
    assert validate_values(None, actor, schema, {}, uuid4()) == {}
    assert validate_values(None, actor, schema, {"number": "0012"}, uuid4()) == {"number": "0012"}
    assert validate_values(None, actor, schema, {}, uuid4(), previous={"number": "0012"}) == {
        "number": "0012"
    }


def test_unbound_required_number_keeps_existing_validation():
    actor = Actor(uuid4(), "Synthetic editor", frozenset({"editor"}))
    with pytest.raises(HTTPException) as error:
        validate_values(None, actor, numbered_schema(), {}, uuid4())
    assert error.value.status_code == 422
    assert validate_values(None, actor, numbered_schema(), {"number": "0012"}, uuid4()) == {
        "number": "0012"
    }


def test_published_binding_checks_registry_and_document_type(factory):
    from app.numbering.models import NumberSeries
    from app.registry.models import Document, Registry
    from app.registry.number_binding import validate_number_bindings
    from tests.test_content_security import actors_and_document

    sample = actors_and_document(factory)
    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        series = NumberSeries(code="binding", name="Synthetic", registry_id=sample["registry"],
                              document_type_ids=[str(document.document_type_id)], synthetic=True)
        db.add(series)
        db.flush()
        schema = numbered_schema(series.id)
        validate_number_bindings(db, schema, [sample["registry"]], document.document_type_id)
        with pytest.raises(ValueError, match="outside"):
            validate_number_bindings(db, schema, [uuid4()], document.document_type_id)
        with pytest.raises(ValueError):
            validate_number_bindings(db, schema, [sample["registry"]], uuid4())
        legacy = Registry(code="okrug", name="Synthetic historical boundary")
        db.add(legacy)
        db.flush()
        series.registry_id = legacy.id
        db.flush()
        validate_number_bindings(db, schema, [legacy.id], document.document_type_id)


def test_existing_book_entry_protects_even_unbound_historical_form_after_void(client, factory):
    from app.numbering.models import NumberEntry, NumberSeries
    from app.registry.models import Document
    from tests.test_content_security import actors_and_document, sign_in_as

    sample = actors_and_document(factory, fields=numbered_schema()["sections"][0]["fields"])
    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        document.reg_number = "0012"
        series = NumberSeries(code="binding", name="Synthetic", registry_id=sample["registry"],
                              document_type_ids=[str(document.document_type_id)], synthetic=True)
        db.add(series)
        db.flush()
        db.add(NumberEntry(series_id=series.id, period_key="2026", ordinal=12,
                           number_string="0012", state="void", owner_id=sample["editor"].id,
                           document_id=document.id))
    security = sign_in_as(client, factory, sample["editor"])
    endpoint = f"/api/v1/documents/{sample['document']}"
    rejected = client.put(endpoint, headers=security, json={"revision": 1, "values": {"number": "0013"}})
    assert rejected.status_code == 403
    assert client.get(endpoint).json()["reg_number"] == "0012"
    unchanged = client.put(endpoint, headers=security, json={"revision": 1, "values": {}})
    assert unchanged.status_code == 200
    assert unchanged.json()["reg_number"] == "0012"
