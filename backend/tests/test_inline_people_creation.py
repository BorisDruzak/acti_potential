"""HTTP acceptance for card-scoped participant creation and its authorization boundary."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy import delete, select

from app.api.dependencies import current_actor
from app.identity.models import Role, UserRole
from app.identity.policy import Actor
from app.platform.models import AuditEvent, OutboxEvent
from app.registry.models import (
    Document,
    DocumentACL,
    DocumentType,
    FormSchema,
    ReferenceItem,
    ReferenceSet,
    Registry,
    RegistryAccess,
)
from tests.conftest import login_admin


@pytest.fixture
def people_card(client, factory):
    assert login_admin(client, factory).status_code == 200
    me = client.get("/api/v1/users/me").json()
    actor_id = UUID(me["id"])
    with factory.begin() as db:
        kind = DocumentType(code="inline-people", name="Synthetic participants")
        registry = Registry(code="inline-people", name="Synthetic registry")
        foreign = Registry(code="inline-foreign", name="Synthetic foreign registry")
        db.add_all([kind, registry, foreign])
        db.flush()
        registry.document_type_ids = [str(kind.id)]
        foreign.document_type_ids = [str(kind.id)]
        fields = []
        books = {}
        for code in ("executors", "responsible_executors", "signers", "approvers", "catalogue"):
            book = ReferenceSet(code=code, name=code, registry_id=registry.id)
            db.add(book)
            db.flush()
            books[code] = book.id
            fields.append({"key": f"dynamic_{code}", "label": code,
                           "type": "multi_reference", "reference_code": code})
        schema = FormSchema(document_type_id=kind.id, version=1, state="published",
                            schema_data={"sections": [{"key": "main", "title": "Main", "fields": fields}]})
        db.add(schema)
        db.flush()
        document = Document(registry_id=registry.id, document_type_id=kind.id,
                            schema_version_id=schema.id, created_by=actor_id, custom_data={})
        db.add(document)
        for target in (registry, foreign):
            db.add(RegistryAccess(registry_id=target.id, user_id=actor_id,
                                  permissions=["read", "create", "update"]))
        db.flush()
        context = dict(registry_id=registry.id, foreign_id=foreign.id, schema_id=schema.id,
                       document_id=document.id, actor_id=actor_id, books=books)
    context["headers"] = {"Origin": "https://testserver", "X-CSRF-Token": me["csrf_token"]}
    return context


def request_person(client, card, *, code="executors", existing=False, **changes):
    body = {"schema_version_id": str(card["schema_id"]), "field_key": f"dynamic_{code}",
            "label": "Synthetic Person"}
    if existing:
        body["document_id"] = str(card["document_id"])
    body.update(changes)
    return client.post(f"/api/v1/registries/{card['registry_id']}/people", json=body, headers=card["headers"])


def set_role(factory, card, code):
    with factory.begin() as db:
        db.execute(delete(UserRole).where(UserRole.user_id == card["actor_id"]))
        role = db.scalar(select(Role).where(Role.code == code))
        if role is None:
            role = Role(code=code)
            db.add(role)
            db.flush()
        db.add(UserRole(user_id=card["actor_id"], role_id=role.id))


def replace_card_schema(db, card, data, *, state="published"):
    """Published schema immutability is part of the exercised production contract."""
    old = db.get(FormSchema, card["schema_id"])
    replacement = FormSchema(document_type_id=old.document_type_id, version=old.version + 1,
                             state=state, schema_data=data)
    db.add(replacement)
    db.flush()
    db.get(Document, card["document_id"]).schema_version_id = replacement.id
    card["schema_id"] = replacement.id


@pytest.mark.parametrize("role", ["admin", "editor"])
@pytest.mark.parametrize("code", ["executors", "responsible_executors", "signers", "approvers"])
@pytest.mark.parametrize("existing", [False, True])
def test_card_people_creation_supports_all_people_books(client, factory, people_card, role, code, existing):
    set_role(factory, people_card, role)
    response = request_person(client, people_card, code=code, existing=existing)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["created"] is True
    assert result["item"]["selectable"] is True
    assert result["item"]["reference_set_id"] == str(people_card["books"][code])
    assert result["item"]["metadata"] == {}
    with factory() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "reference.item.created"))
        assert event.object_id == UUID(result["item"]["id"])
        assert event.details == {"reference_set_id": str(people_card["books"][code]),
                                 "registry_id": str(people_card["registry_id"]),
                                 "schema_version_id": str(people_card["schema_id"])}
        outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "reference.item.created"))
        assert outbox.payload == event.details


def test_duplicate_normalizes_spaces_case_and_emits_once(client, factory, people_card):
    first = request_person(client, people_card, label="  Synthetic   Person  ")
    assert first.status_code == 201, first.text
    assert first.json()["item"]["label"] == "Synthetic Person"
    second = request_person(client, people_card, label="synthetic\tPERSON")
    assert second.status_code == 200, second.text
    assert second.json() == {"item": first.json()["item"], "created": False}
    with factory.begin() as db:
        assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.action == "reference.item.created")))) == 1
        assert len(list(db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "reference.item.created")))) == 1
        db.get(ReferenceItem, UUID(first.json()["item"]["id"])).active = False
    inactive = request_person(client, people_card, label="SYNTHETIC Person")
    assert inactive.status_code == 409, inactive.text


def test_concurrent_duplicate_requests_create_one_person_and_one_event(client, factory, people_card):
    with ThreadPoolExecutor(max_workers=4) as workers:
        responses = list(workers.map(lambda _: request_person(client, people_card), range(4)))
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 201]
    assert len({response.json()["item"]["id"] for response in responses}) == 1
    with factory() as db:
        assert len(list(db.scalars(select(ReferenceItem)))) == 1
        assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.action == "reference.item.created")))) == 1


@pytest.mark.parametrize("label", ["", " \t\n ", "x" * 256])
def test_invalid_person_labels_are_rejected(client, people_card, label):
    assert request_person(client, people_card, label=label).status_code == 422


@pytest.mark.parametrize("role", ["viewer", "user"])
def test_read_roles_cannot_add_people(client, factory, people_card, role):
    set_role(factory, people_card, role)
    assert request_person(client, people_card).status_code == 403


@pytest.mark.parametrize("existing", [False, True])
def test_read_only_registry_actor_cannot_add_people(client, factory, people_card, existing):
    with factory.begin() as db:
        db.get(RegistryAccess, (people_card["registry_id"], people_card["actor_id"])).permissions = ["read"]
    assert request_person(client, people_card, existing=existing).status_code == 403


@pytest.mark.parametrize("restriction", ["write", "hidden", "conditional", "foreign", "global", "nested"])
def test_field_authorization_and_reference_scope_cannot_be_bypassed(client, factory, people_card, restriction):
    with factory.begin() as db:
        schema = db.get(FormSchema, people_card["schema_id"])
        data = deepcopy(schema.schema_data)
        field = data["sections"][0]["fields"][0]
        if restriction == "write":
            field["write_roles"] = ["editor"]
        elif restriction == "hidden":
            field["read_roles"] = ["editor"]
        elif restriction == "conditional":
            field["visible_when"] = {"field": "enabled", "op": "eq", "value": True}
            data["sections"][0]["fields"].append({"key": "enabled", "type": "boolean", "default": False})
        elif restriction in {"foreign", "global"}:
            book = ReferenceSet(code="executors", name="Other people", registry_id=(people_card["foreign_id"] if restriction == "foreign" else None))
            db.add(book)
            db.flush()
            field.pop("reference_code")
            field["reference_set_id"] = str(book.id)
        else:
            data["sections"][0]["fields"][0] = {"key": "group", "type": "group", "children": [field]}
        replace_card_schema(db, people_card, data)
    response = request_person(client, people_card)
    assert response.status_code in {403, 422}, response.text
    with factory() as db:
        assert list(db.scalars(select(ReferenceItem))) == []


def test_arbitrary_catalogue_and_client_metadata_are_rejected(client, people_card):
    assert request_person(client, people_card, code="catalogue").status_code == 422
    assert request_person(client, people_card, metadata={"untrusted": True}).status_code == 422


def test_schema_and_document_context_must_match(client, factory, people_card):
    with factory.begin() as db:
        original = db.get(FormSchema, people_card["schema_id"])
        other = FormSchema(document_type_id=original.document_type_id, version=2,
                           state="published", schema_data=original.schema_data)
        db.add(other)
        db.flush()
        other_id = str(other.id)
    assert request_person(client, people_card, existing=True, schema_version_id=other_id).status_code == 422
    foreign_context = dict(people_card, registry_id=people_card["foreign_id"])
    assert request_person(client, foreign_context, existing=True).status_code == 422


def test_new_card_requires_published_assigned_schema(client, factory, people_card):
    with factory.begin() as db:
        db.get(Registry, people_card["registry_id"]).document_type_ids = []
    assert request_person(client, people_card).status_code == 422
    with factory.begin() as db:
        schema = db.get(FormSchema, people_card["schema_id"])
        db.get(Registry, people_card["registry_id"]).document_type_ids = [str(schema.document_type_id)]
        replace_card_schema(db, people_card, schema.schema_data, state="draft")
    assert request_person(client, people_card).status_code == 422


def test_document_acl_update_is_required(client, factory, people_card):
    with factory.begin() as db:
        db.add(DocumentACL(document_id=people_card["document_id"], user_id=people_card["actor_id"], permissions=["read"]))
    assert request_person(client, people_card, existing=True).status_code == 403


def test_editor_can_maintain_the_registry_reference_directly(client, factory, people_card):
    set_role(factory, people_card, "editor")
    result = client.post(f"/api/v1/references/{people_card['books']['executors']}/items",
                         json={"label": "Synthetic Person"}, headers=people_card["headers"])
    assert result.status_code == 201


def test_scoped_service_actor_cannot_inherit_human_people_creation(client, people_card):
    actor = Actor(people_card["actor_id"], "Synthetic scoped actor", frozenset({"editor"}),
                  scoped_registry_ids=frozenset({str(people_card["registry_id"])}),
                  scoped_operations=frozenset({"content", "metadata"}))
    client.app.dependency_overrides[current_actor] = lambda: actor
    try:
        assert request_person(client, people_card).status_code == 403
        assert request_person(client, people_card, existing=True).status_code == 403
    finally:
        client.app.dependency_overrides.pop(current_actor, None)


def test_default_values_control_new_card_visibility(client, factory, people_card):
    with factory.begin() as db:
        schema = db.get(FormSchema, people_card["schema_id"])
        data = deepcopy(schema.schema_data)
        data["sections"][0]["fields"][0]["visible_when"] = {"field": "enabled", "op": "eq", "value": True}
        data["sections"][0]["fields"].append({"key": "enabled", "type": "boolean", "default": True})
        replace_card_schema(db, people_card, data)
    response = request_person(client, people_card)
    assert response.status_code == 201, response.text
    assert request_person(client, people_card, existing=True, label="Other Person").status_code == 403
