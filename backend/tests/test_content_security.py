from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.identity.models import Role, User, UserRole
from app.identity.policy import Actor
from app.registry.documents import document_output, get_document
from app.registry.models import (
    Document,
    DocumentACL,
    DocumentFile,
    DocumentType,
    DocumentVersion,
    FormSchema,
    ReferenceItem,
    ReferenceSet,
    Registry,
    RegistryAccess,
)
from app.registry.policy import (
    accessible_document_clause,
    field_readable,
    field_writable,
    redact_schema,
    redact_values,
)
from app.registry.value_validation import validate_values


def actors_and_document(factory, fields=None):
    with factory.begin() as db:
        administrator = User(display_name="Synthetic administrator")
        editor = User(display_name="Synthetic editor")
        viewer = User(display_name="Synthetic viewer")
        db.add_all([administrator, editor, viewer])
        registry = Registry(code="content-security", name="Synthetic permitted registry")
        dtype = DocumentType(code="security-type", name="Synthetic type")
        db.add_all([registry, dtype])
        db.flush()
        schema = FormSchema(
            document_type_id=dtype.id,
            version=1,
            state="published",
            schema_data={
                "sections": [
                    {
                        "key": "main",
                        "title": "Main",
                        "fields": fields
                        or [
                            {
                                "key": "title",
                                "label": "Title",
                                "type": "text",
                                "system": "title",
                                "read_roles": ["admin"],
                            },
                            {"key": "public", "label": "Public", "type": "text"},
                        ],
                    }
                ]
            },
        )
        db.add(schema)
        db.flush()
        document = Document(
            card_state="published",
            publication_targets=["internal", "shared"],
            registry_id=registry.id,
            document_type_id=dtype.id,
            schema_version_id=schema.id,
            title="SYNTHETIC-HIDDEN-TITLE",
            created_by=editor.id,
            custom_data={"public": "Visible"},
        )
        db.add(document)
        db.add_all(
            [
                RegistryAccess(
                    registry_id=registry.id,
                    user_id=editor.id,
                    permissions=["read", "create", "update", "upload", "download"],
                ),
                RegistryAccess(
                    registry_id=registry.id,
                    user_id=viewer.id,
                    permissions=["read", "create", "update", "upload", "download"],
                ),
            ]
        )
        db.flush()
        return {
            "admin": Actor(administrator.id, administrator.display_name, frozenset({"admin"})),
            "editor": Actor(editor.id, editor.display_name, frozenset({"editor"})),
            "viewer": Actor(viewer.id, viewer.display_name, frozenset({"viewer"})),
            "registry": registry.id,
            "document": document.id,
        }


def test_hidden_field_guesses_always_fail_including_equal_previous_value(factory):
    actor = Actor(uuid4(), "Synthetic editor", frozenset({"editor"}))
    schema = {
        "sections": [{"fields": [{"key": "secret", "type": "text", "read_roles": ["admin"]}]}]
    }
    with factory() as db:
        for guessed in ("incorrect-guess", "SYNTHETIC-SECRET"):
            with pytest.raises(HTTPException) as error:
                validate_values(
                    db,
                    actor,
                    schema,
                    {"secret": guessed},
                    uuid4(),
                    previous={"secret": "SYNTHETIC-SECRET"},
                )
            assert error.value.status_code == 403
        assert validate_values(
            db, actor, schema, {}, uuid4(), previous={"secret": "SYNTHETIC-SECRET"}
        ) == {"secret": "SYNTHETIC-SECRET"}


def test_nested_schema_never_exposes_conditions_on_hidden_root_field():
    actor = Actor(uuid4(), "Synthetic viewer", frozenset({"viewer"}))
    schema = {
        "sections": [
            {
                "fields": [
                    {"key": "secret", "type": "text", "read_roles": ["admin"]},
                    {
                        "key": "rows",
                        "type": "group",
                        "children": [
                            {
                                "key": "dependent",
                                "type": "text",
                                "visible_when": {
                                    "field": "secret",
                                    "op": "eq",
                                    "value": "SYNTHETIC-HIDDEN-PREDICATE",
                                },
                            },
                        ],
                    },
                ]
            }
        ]
    }
    output = redact_schema(schema, actor)
    children = output["sections"][0]["fields"][0]["children"]
    assert [field["key"] for field in children] == ["dependent"]
    assert "visible_when" not in children[0]
    assert children[0]["editable"] is False
    values = {"secret": "SYNTHETIC-HIDDEN-PREDICATE", "rows": [{"dependent": "HIDDEN"}]}
    assert redact_values(schema, values, actor) == {"rows": [{}]}


def test_admin_role_does_not_bypass_registry_grants_or_individual_acl(factory):
    sample = actors_and_document(factory)
    with factory.begin() as db:
        assert (
            list(
                db.scalars(select(Document).where(accessible_document_clause(db, sample["admin"])))
            )
            == []
        )
        with pytest.raises(HTTPException) as error:
            get_document(db, sample["admin"], sample["document"])
        assert error.value.status_code == 404
        assert get_document(db, sample["editor"], sample["document"]).id == sample["document"]
        db.add(
            DocumentACL(
                document_id=sample["document"], user_id=sample["editor"].id, permissions=["read"]
            )
        )
        db.flush()
        assert (
            list(
                db.scalars(select(Document).where(accessible_document_clause(db, sample["viewer"])))
            )
            == []
        )
        with pytest.raises(HTTPException) as error:
            get_document(db, sample["viewer"], sample["document"])
        assert error.value.status_code == 404
        with pytest.raises(HTTPException) as error:
            get_document(db, sample["editor"], sample["document"], "update")
        assert error.value.status_code == 403


def test_hidden_system_fields_are_redacted_from_card_and_summary(factory):
    sample = actors_and_document(factory)
    with factory() as db:
        document = get_document(db, sample["viewer"], sample["document"])
        result = document_output(db, document, sample["viewer"])
        assert result["title"] is None
        assert result["values"] == {"public": "Visible"}
        assert [f["key"] for f in result["schema"]["sections"][0]["fields"]] == ["public"]
        assert result["permissions"] == ["download", "read"]


def test_reference_validation_keeps_registry_boundary_and_historical_inactive_value(factory):
    sample = actors_and_document(factory)
    with factory.begin() as db:
        other_registry = Registry(code="other-reference-registry", name="Other")
        db.add(other_registry)
        db.flush()
        local_set = ReferenceSet(code="refs", name="Local", registry_id=sample["registry"])
        other_set = ReferenceSet(code="refs", name="Other", registry_id=other_registry.id)
        db.add_all([local_set, other_set])
        db.flush()
        inactive = ReferenceItem(reference_set_id=local_set.id, label="Historical", active=False)
        foreign = ReferenceItem(reference_set_id=other_set.id, label="Other-registry item")
        db.add_all([inactive, foreign])
        db.flush()
        schema = {
            "sections": [
                {
                    "fields": [
                        {
                            "key": "reference",
                            "type": "reference",
                            "reference_set_id": str(local_set.id),
                        }
                    ]
                }
            ]
        }
        for value in (str(inactive.id), str(foreign.id)):
            with pytest.raises(HTTPException) as error:
                validate_values(
                    db, sample["editor"], schema, {"reference": value}, sample["registry"]
                )
            assert error.value.status_code == 422
        assert validate_values(
            db,
            sample["editor"],
            schema,
            {"reference": str(inactive.id)},
            sample["registry"],
            previous={"reference": str(inactive.id)},
        ) == {"reference": str(inactive.id)}


def sign_in_as(client, factory, actor):
    from datetime import UTC, datetime

    from app.api.dependencies import COOKIE_NAME
    from app.identity.sessions import issue_session

    with factory.begin() as db:
        for code in actor.roles:
            role = db.scalar(select(Role).where(Role.code == code))
            if db.get(UserRole, (actor.id, role.id)) is None:
                db.add(UserRole(user_id=actor.id, role_id=role.id))
        issued = issue_session(db, actor.id, datetime.now(UTC))
    client.cookies.set(COOKIE_NAME, issued.token, domain="testserver.local", path="/")
    me = client.get("/api/v1/users/me")
    assert me.status_code == 200
    return {"Origin": "https://testserver", "X-CSRF-Token": me.json()["csrf_token"]}


def test_conditional_hidden_title_cannot_be_guessed_through_search_or_sort(client, factory):
    sample = actors_and_document(
        factory,
        fields=[
            {"key": "gate", "label": "Hidden gate", "type": "boolean", "read_roles": ["admin"]},
            {
                "key": "title",
                "label": "Title",
                "type": "text",
                "system": "title",
                "filterable": True,
                "visible_when": {"field": "gate", "op": "eq", "value": True},
            },
        ],
    )
    with factory.begin() as db:
        first = db.get(Document, sample["document"])
        first.custom_data = {"gate": True}
        first.title = "SYNTHETIC-CONDITIONAL-TITLE-A"
        second = Document(
            card_state="published",
            publication_targets=["internal", "shared"],
            registry_id=first.registry_id,
            document_type_id=first.document_type_id,
            schema_version_id=first.schema_version_id,
            created_by=sample["editor"].id,
            title="SYNTHETIC-CONDITIONAL-TITLE-Z",
            custom_data={"gate": True},
        )
        db.add(second)
        db.flush()
    sign_in_as(client, factory, sample["viewer"])
    ordinary = client.get("/api/v1/documents").json()
    assert ordinary["total"] == 2
    assert all(item["title"] is None for item in ordinary["items"])
    for params in ({"q": "CONDITIONAL-TITLE-A"}, {"title": "CONDITIONAL-TITLE-A"}):
        result = client.get("/api/v1/documents", params=params)
        assert result.status_code == 200
        assert result.json()["total"] == 0
    filtered = client.get(
        "/api/v1/documents", params={"filters": '{"title":"SYNTHETIC-CONDITIONAL-TITLE-A"}'}
    )
    assert filtered.status_code == 422
    ascending = client.get("/api/v1/documents", params={"sort": "title", "direction": "asc"}).json()
    descending = client.get(
        "/api/v1/documents", params={"sort": "title", "direction": "desc"}
    ).json()
    assert [row["id"] for row in ascending["items"]] == [row["id"] for row in descending["items"]]
    card = client.get(f"/api/v1/documents/{sample['document']}").json()
    fields = card["schema"]["sections"][0]["fields"]
    assert fields[0]["filterable"] is False


def test_group_conditions_use_root_values_and_preserve_protected_rows(factory):
    actor = Actor(uuid4(), "Synthetic editor", frozenset({"editor"}))
    schema = {
        "sections": [
            {
                "fields": [
                    {"key": "gate", "type": "boolean"},
                    {
                        "key": "rows",
                        "type": "group",
                        "children": [
                            {
                                "key": "value",
                                "type": "text",
                                "required_when": {"field": "gate", "op": "eq", "value": True},
                                "visible_when": {"field": "gate", "op": "eq", "value": True},
                            },
                            {"key": "secret", "type": "text", "read_roles": ["admin"]},
                        ],
                    },
                ]
            }
        ]
    }
    values = {"gate": True, "rows": [{"value": "Visible", "secret": "SYNTHETIC-HIDDEN-GROUP"}]}
    assert redact_values(schema, values, actor) == {"gate": True, "rows": [{"value": "Visible"}]}
    rendered = redact_schema(schema, actor)
    child = rendered["sections"][0]["fields"][1]["children"][0]
    assert child["visible_when"] == {"field": "gate", "op": "eq", "value": True}
    with factory() as db:
        with pytest.raises(HTTPException) as error:
            validate_values(db, actor, schema, {"gate": True, "rows": [{}]}, uuid4())
        assert error.value.status_code == 422
        with pytest.raises(HTTPException) as error:
            validate_values(db, actor, schema, {"rows": []}, uuid4(), previous=values)
        assert error.value.status_code == 403
        updated = validate_values(
            db, actor, schema, {"rows": [{"value": "Changed"}]}, uuid4(), previous=values
        )
        assert updated["rows"] == [{"value": "Changed", "secret": "SYNTHETIC-HIDDEN-GROUP"}]


def test_conditionally_hidden_file_is_omitted_and_download_denied(client, factory):
    import io

    from app.storage.local import LocalFileStorage

    sample = actors_and_document(
        factory,
        fields=[
            {"key": "gate", "type": "boolean"},
            {
                "key": "original",
                "type": "file",
                "visible_when": {"field": "gate", "op": "eq", "value": True},
            },
        ],
    )
    stored = LocalFileStorage(client.app.state.settings.storage_root).put(
        "synthetic-file", io.BytesIO(b"synthetic original")
    )
    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        version = DocumentVersion(
            document_id=document.id, version=1, created_by=sample["editor"].id
        )
        db.add(version)
        db.flush()
        file = DocumentFile(
            document_id=document.id,
            version_id=version.id,
            field_key="original",
            name="synthetic.txt",
            size=stored.size,
            mime="text/plain",
            sha256=stored.sha256,
            storage_key=stored.storage_key,
            created_by=sample["editor"].id,
        )
        db.add(file)
        db.flush()
        file_id = file.id
        document.custom_data = {"gate": True, "original": [str(file_id)]}
    sign_in_as(client, factory, sample["viewer"])
    endpoint = f"/api/v1/documents/{sample['document']}"
    assert client.get(endpoint).json()["files"][0]["id"] == str(file_id)
    assert client.get(f"/api/v1/files/{file_id}/download").content == b"synthetic original"
    with factory.begin() as db:
        db.get(Document, sample["document"]).custom_data = {
            "gate": False,
            "original": [str(file_id)],
        }
    card = client.get(endpoint).json()
    assert card["files"] == [] and "original" not in card["values"]
    assert client.get(f"/api/v1/files/{file_id}/download").status_code == 404


def test_direct_routes_enforce_viewer_role_and_current_document_acl(client, factory):
    sample = actors_and_document(factory)
    security = sign_in_as(client, factory, sample["viewer"])
    endpoint = f"/api/v1/documents/{sample['document']}"
    assert client.get(endpoint).status_code == 200
    assert (
        client.put(
            endpoint, json={"revision": 1, "values": {"public": "unauthorized"}}, headers=security
        ).status_code
        == 403
    )
    assert (
        client.post(
            endpoint + "/files",
            params={"filename": "x.txt", "revision": 1},
            content=b"x",
            headers=security,
        ).status_code
        == 403
    )
    with factory.begin() as db:
        db.add(
            DocumentACL(
                document_id=sample["document"], user_id=sample["editor"].id, permissions=["read"]
            )
        )
    assert client.get(endpoint).status_code == 404
    assert client.get(endpoint + "/history").status_code == 404
    listed = client.get("/api/v1/documents", params={"q": "Visible"}).json()
    assert listed["total"] == 0 and listed["items"] == []
    sign_in_as(client, factory, sample["admin"])
    assert client.get(endpoint).status_code == 404


def test_user_role_has_reader_field_access_without_editor_alias():
    user = Actor(uuid4(), "Legacy user", frozenset({"user"}))
    viewer = Actor(uuid4(), "Viewer", frozenset({"viewer"}))
    field = {
        "key": "editor_value",
        "type": "text",
        "read_roles": ["editor"],
        "write_roles": ["editor"],
    }
    assert field_readable(field, user) is False
    assert field_readable(field, user, "editor") is False
    assert field_writable(field, user) is False
    assert field_readable(field, viewer) is False
    assert field_writable(field, viewer) is False
    schema = {"sections": [{"fields": [field, {"key": "public", "type": "text"}]}]}
    assert redact_values(schema, {"editor_value": "HIDDEN", "public": "Visible"}, user) == {
        "public": "Visible"
    }
    assert [item["key"] for item in redact_schema(schema, user)["sections"][0]["fields"]] == [
        "public"
    ]


def test_viewer_cannot_elevate_display_mode_to_editor():
    viewer = Actor(uuid4(), "Viewer", frozenset({"viewer"}))
    editor = Actor(uuid4(), "Editor", frozenset({"editor"}))
    field = {"key": "internal", "type": "text", "modes": ["editor"]}
    assert field_readable(field, viewer) is False
    assert field_readable(field, viewer, "editor") is False
    assert field_readable(field, editor, "editor") is True
    assert field_readable(field, editor, "viewer") is False


def test_display_mode_is_applied_to_direct_card_list_and_search(client, factory):
    sample = actors_and_document(
        factory,
        fields=[
            {
                "key": "title",
                "label": "Internal title",
                "type": "text",
                "system": "title",
                "modes": ["editor"],
            },
            {"key": "public", "label": "Public", "type": "text"},
        ],
    )
    sign_in_as(client, factory, sample["viewer"])
    endpoint = f"/api/v1/documents/{sample['document']}"
    for mode in ("viewer", "editor"):
        card = client.get(endpoint, params={"mode": mode})
        assert card.status_code == 200
        assert card.json()["title"] is None
        assert card.json()["values"] == {"public": "Visible"}
        listed = client.get("/api/v1/documents", params={"mode": mode})
        assert listed.json()["items"][0]["title"] is None
        searched = client.get("/api/v1/documents", params={"q": "HIDDEN-TITLE", "mode": mode})
        assert searched.json()["total"] == 0
    sign_in_as(client, factory, sample["editor"])
    assert (
        client.get(endpoint, params={"mode": "editor"}).json()["title"] == "SYNTHETIC-HIDDEN-TITLE"
    )
    assert client.get(endpoint, params={"mode": "viewer"}).json()["title"] is None
