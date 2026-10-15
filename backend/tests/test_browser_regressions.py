import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.identity.policy import Actor
from app.registry.models import Document, FormSchema
from app.registry.policy import condition_matches, redact_values
from app.registry.validation import validate_schema
from app.registry.value_validation import validate_values
from tests.test_content_security import actors_and_document, sign_in_as
from tests.test_schema_engine import schema


@pytest.mark.parametrize(
    "kind,stored,query",
    [
        ("decimal", "1.00", 1),
        ("decimal", "1.00", "1.0"),
        ("datetime", "2026-10-15T10:30:00", "2026-10-15T10:30"),
    ],
)
def test_typed_filters_match_values_saved_by_the_form(client, factory, kind, stored, query):
    sample = actors_and_document(factory, [{"key": "detail", "type": kind, "filterable": True}])
    with factory.begin() as db:
        db.get(Document, sample["document"]).custom_data = {"detail": stored}
    sign_in_as(client, factory, sample["editor"])
    response = client.get("/api/v1/documents", params={"filters": json.dumps({"detail": query})})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


@pytest.mark.parametrize(
    "kind,value",
    [("decimal", "not-number"), ("decimal", True), ("datetime", "not-date"), ("integer", True)],
)
def test_invalid_typed_filters_fail_closed(client, factory, kind, value):
    sample = actors_and_document(factory, [{"key": "detail", "type": kind, "filterable": True}])
    sign_in_as(client, factory, sample["editor"])
    response = client.get("/api/v1/documents", params={"filters": json.dumps({"detail": value})})
    assert response.status_code == 422


def test_decimal_filter_does_not_cast_text_from_another_schema(client, factory):
    sample = actors_and_document(
        factory, [{"key": "detail", "type": "decimal", "filterable": True}]
    )
    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        document.custom_data = {"detail": "1.00"}
        other = FormSchema(
            document_type_id=document.document_type_id,
            version=2,
            state="published",
            schema_data=schema("text", filterable=True),
        )
        db.add(other)
        db.flush()
        db.add(
            Document(
                registry_id=document.registry_id,
                document_type_id=document.document_type_id,
                schema_version_id=other.id,
                created_by=sample["editor"].id,
                custom_data={"detail": "not-a-number"},
            )
        )
    sign_in_as(client, factory, sample["editor"])
    response = client.get("/api/v1/documents", params={"filters": '{"detail":"1.0"}'})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


@pytest.mark.parametrize("op,expected", [("eq", 1.5), ("ne", 2), ("in", [0, 1.5])])
def test_decimal_conditions_match_normalized_stored_values(factory, op, expected):
    actor = Actor(uuid4(), "Editor", frozenset({"editor"}))
    fields = [
        {"key": "amount", "type": "decimal"},
        {
            "key": "reason",
            "type": "text",
            "required": True,
            "visible_when": {"field": "amount", "op": op, "value": expected},
        },
    ]
    form = {"sections": [{"fields": fields}]}
    values = {"amount": "1.50", "reason": "visible"}
    assert redact_values(form, values, actor) == values
    with factory() as db:
        assert validate_values(db, actor, form, values, uuid4()) == values
        with pytest.raises(HTTPException) as error:
            validate_values(db, actor, form, {"amount": "1.50"}, uuid4())
        assert error.value.status_code == 422


def test_conditions_keep_text_and_boolean_distinct_from_numbers():
    for actual, expected in [("1", 1), (True, 1), (False, 0)]:
        assert not condition_matches(
            {"field": "detail", "op": "eq", "value": expected}, {"detail": actual}
        )
        assert condition_matches(
            {"field": "detail", "op": "ne", "value": expected}, {"detail": actual}
        )


def test_upload_into_a_decimal_conditional_field(client, factory):
    sample = actors_and_document(
        factory,
        [
            {"key": "amount", "type": "decimal"},
            {
                "key": "original",
                "type": "file",
                "visible_when": {"field": "amount", "op": "eq", "value": 1.5},
            },
        ],
    )
    with factory.begin() as db:
        db.get(Document, sample["document"]).custom_data = {"amount": "1.50"}
    security = sign_in_as(client, factory, sample["editor"])
    response = client.post(
        f"/api/v1/documents/{sample['document']}/files",
        params={"filename": "synthetic.txt", "revision": 1, "field_key": "original"},
        content=b"synthetic original",
        headers={**security, "Content-Type": "text/plain"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["files"][0]["field_key"] == "original"


@pytest.mark.parametrize("row", [{"unknown": "value"}, {"count": "not-an-integer"}, {"count": 9}])
def test_group_defaults_validate_child_keys_types_and_constraints(row):
    with pytest.raises(ValueError):
        validate_schema(
            schema(
                "group",
                default=[row],
                children=[
                    {"key": "count", "label": "Count", "type": "integer", "constraints": {"max": 5}}
                ],
            )
        )
