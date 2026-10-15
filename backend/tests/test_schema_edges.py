from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.identity.policy import Actor
from app.registry.policy import redact_schema, redact_values
from app.registry.value_validation import validate_values


def test_chained_hidden_dependencies_are_redacted_and_cannot_be_written(factory):
    actor = Actor(uuid4(), "Viewer", frozenset({"viewer"}))
    schema = {
        "sections": [
            {
                "fields": [
                    {"key": "secret", "type": "text", "read_roles": ["admin"]},
                    {
                        "key": "guarded",
                        "type": "text",
                        "visible_when": {"field": "secret", "op": "eq", "value": "private"},
                    },
                    {
                        "key": "dependent",
                        "type": "text",
                        "visible_when": {
                            "field": "guarded",
                            "op": "eq",
                            "value": "sensitive-predicate",
                        },
                    },
                ]
            }
        ]
    }
    values = {"secret": "private", "guarded": "sensitive-predicate", "dependent": "content"}
    assert redact_values(schema, values, actor) == {}
    rendered = redact_schema(schema, actor)
    assert "sensitive-predicate" not in str(rendered)
    assert rendered["sections"][0]["fields"][1]["editable"] is False
    with factory() as db:
        with pytest.raises(HTTPException) as error:
            validate_values(db, actor, schema, {"dependent": "updated"}, uuid4(), previous=values)
        assert error.value.status_code == 403


def test_system_column_bounds_cannot_be_overridden_by_published_schema(factory):
    actor = Actor(uuid4(), "Editor", frozenset({"editor"}))
    for system, length in [("reg_number", 255), ("status", 100)]:
        schema = {
            "sections": [
                {
                    "fields": [
                        {
                            "key": system,
                            "type": "text",
                            "system": system,
                            "constraints": {"max_length": 100000},
                        }
                    ]
                }
            ]
        }
        with factory() as db:
            with pytest.raises(HTTPException) as error:
                validate_values(db, actor, schema, {system: "x" * (length + 1)}, uuid4())
            assert error.value.status_code == 422


def test_hidden_required_condition_does_not_reveal_secret_through_write_errors(factory):
    actor = Actor(uuid4(), "Editor", frozenset({"editor"}))
    schema = {
        "sections": [
            {
                "fields": [
                    {"key": "secret", "type": "text", "read_roles": ["admin"]},
                    {
                        "key": "public",
                        "type": "text",
                        "required_when": {"field": "secret", "op": "eq", "value": "private"},
                    },
                ]
            }
        ]
    }
    with factory() as db:
        for secret in ("private", "other"):
            prior = {"secret": secret}
            assert validate_values(db, actor, schema, {}, uuid4(), previous=prior) == prior
            with pytest.raises(HTTPException) as error:
                validate_values(db, actor, schema, {"public": ""}, uuid4(), previous=prior)
            assert error.value.status_code == 403


@pytest.mark.parametrize("grouped", [False, True])
def test_update_required_attachment_and_metadata_is_atomic(client, factory, grouped):
    import base64

    from app.registry.models import Document
    from tests.test_content_security import actors_and_document, sign_in_as

    original = {
        "key": "original",
        "type": "file",
        "required_when": {"field": "gate", "op": "eq", "value": True},
        "visible_when": {"field": "gate", "op": "eq", "value": True},
    }
    fields = [
        {"key": "gate", "type": "boolean"},
        {"key": "rows", "type": "group", "children": [original]} if grouped else original,
    ]
    sample = actors_and_document(factory, fields)
    with factory.begin() as db:
        db.get(Document, sample["document"]).custom_data = (
            {"gate": False, "rows": []} if grouped else {"gate": False}
        )
    security = sign_in_as(client, factory, sample["editor"])
    endpoint = "/api/v1/documents/" + str(sample["document"])
    values = {"gate": True, "rows": [{}]} if grouped else {"gate": True}
    assert (
        client.put(endpoint, json={"revision": 1, "values": values}, headers=security).status_code
        == 422
    )
    response = client.put(
        endpoint,
        json={
            "revision": 1,
            "values": values,
            "files": [
                {
                    "field_key": "rows.0.original" if grouped else "original",
                    "name": "synthetic.txt",
                    "mime": "text/plain",
                    "content_base64": base64.b64encode(b"atomic original").decode(),
                }
            ],
        },
        headers=security,
    )
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["revision"] == 2
    assert card["files"][0]["version"] == 1
    assert (
        client.get("/api/v1/files/" + card["files"][0]["id"] + "/download").content
        == b"atomic original"
    )
