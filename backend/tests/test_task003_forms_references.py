from copy import deepcopy

import pytest

from app.registry.policy import condition_matches
from app.registry.validation import validate_schema


def conditional(kind="text", **condition):
    return {
        "sections": [
            {
                "key": "main",
                "title": "Main",
                "fields": [
                    {"key": "trigger", "label": "Trigger", "type": kind},
                    {
                        "key": "detail",
                        "label": "Detail",
                        "type": "text",
                        "visible_when": {"field": "trigger", **condition},
                    },
                ],
            }
        ]
    }


@pytest.mark.parametrize(
    "op,value,actual,expected",
    [
        ("not_in", ["a", "b"], "c", True),
        ("not_in", ["a", "b"], "a", False),
        ("absent", None, "", True),
        ("absent", None, False, False),
    ],
)
def test_six_condition_operators(op, value, actual, expected):
    rule = {"field": "trigger", "op": op}
    if op == "not_in":
        rule["value"] = value
    validate_schema(conditional(**{k: v for k, v in rule.items() if k != "field"}))
    assert condition_matches(rule, {"trigger": actual}) is expected


@pytest.mark.parametrize("kind,value", [("integer", True), ("boolean", 1), ("date", "bad")])
def test_conditions_validate_operand_types(kind, value):
    with pytest.raises(ValueError):
        validate_schema(conditional(kind, op="eq", value=value))


def test_schema_accepts_safe_patterns_and_rejects_backtracking_and_nontext():
    base = conditional(op="present")
    base["sections"][0]["fields"][0]["constraints"] = {"pattern": "^[A-Z]{2}[0-9]{3}$"}
    assert validate_schema(base) == base
    for pattern in ("(a+)+$", "(a|aa)+$", r"(a)\1", ".*.*"):
        bad = deepcopy(base)
        bad["sections"][0]["fields"][0]["constraints"]["pattern"] = pattern
        with pytest.raises(ValueError):
            validate_schema(bad)


def test_list_view_validates_stable_keys_and_duplicates():
    base = conditional(op="present")
    base["list_view"] = {"columns": [{"key": "trigger", "label": "Visible"}]}
    assert validate_schema(base) == base
    base["list_view"]["columns"].append({"key": "trigger", "label": "Duplicate"})
    with pytest.raises(ValueError):
        validate_schema(base)


def test_default_drives_required_conditions_on_server():
    from uuid import uuid4

    from fastapi import HTTPException

    from app.identity.policy import Actor
    from app.registry.value_validation import validate_values

    form = conditional("boolean", op="eq", value=True)
    fields = form["sections"][0]["fields"]
    fields[0]["default"] = True
    fields[1]["required_when"] = fields[1].pop("visible_when")
    actor = Actor(uuid4(), "Synthetic", frozenset({"editor"}))
    with pytest.raises(HTTPException) as error:
        validate_values(None, actor, form, {}, uuid4())
    assert error.value.status_code == 422


def test_reference_hierarchy_validity_history_and_atomic_import(client, factory):
    import base64

    from tests.conftest import login_admin
    from tests.test_config_api import admin_headers

    login_admin(client, factory)
    headers = admin_headers(client)
    created = client.post(
        "/api/v1/references",
        headers=headers,
        json={"code": "task003", "name": "Task003", "description": "Managed dictionary"},
    )
    assert created.status_code == 201, created.text
    reference_id = created.json()["id"]
    url = f"/api/v1/references/{reference_id}"
    root = client.post(
        url + "/items",
        headers=headers,
        json={"label": "Parent", "code": "p", "sort_order": 2, "metadata": {"safe": "kept"}},
    ).json()
    child = client.post(
        url + "/items",
        headers=headers,
        json={
            "label": "Child",
            "code": "c",
            "parent_id": root["id"],
            "sort_order": 1,
            "valid_until": "2000-01-01",
        },
    )
    assert child.status_code == 201, child.text
    assert child.json()["selectable"] is False
    child_id = child.json()["id"]
    assert (
        client.patch(
            url + f"/items/{root['id']}", headers=headers, json={"parent_id": child_id}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            url + f"/items/{child_id}",
            headers=headers,
            json={"valid_from": "2030-01-01", "valid_until": "2020-01-01"},
        ).status_code
        == 422
    )
    csv = b"code,label,sort_order,active\na,Import A,0,true\nb,Import B,1,false\n"
    result = client.post(
        url + "/import",
        headers=headers,
        json={"format": "csv", "content": base64.b64encode(csv).decode()},
    )
    assert result.status_code == 200 and result.json()["created"] == 2, result.text
    rows = client.get(url + "/items").json()
    assert [r["label"] for r in rows] == ["Import A", "Child", "Import B", "Parent"]
    for format in ("csv", "xlsx"):
        exported = client.get(url + f"/export?format={format}")
        assert exported.status_code == 200, exported.text
        again = client.post(
            url + "/import",
            headers=headers,
            json={"format": format, "content": base64.b64encode(exported.content).decode()},
        )
        assert again.status_code == 200 and again.json()["updated"] == 4, again.text
    assert next(r for r in client.get(url + "/items").json() if r["id"] == root["id"])[
        "metadata"
    ] == {"safe": "kept"}
    invalid = b"code,label\nz,Should roll back\nc,Duplicate code\n"
    assert (
        client.post(
            url + "/import",
            headers=headers,
            json={"format": "csv", "content": base64.b64encode(invalid).decode()},
        ).status_code
        == 409
    )
    assert len(client.get(url + "/items").json()) == 4
    history = client.get(url + "/history").json()["items"]
    assert any(e["action"] == "reference.imported" for e in history)
    assert any(e["action"] == "reference.item.created" for e in history)


def test_reference_provenance_and_historical_inactive_values_are_preserved(client, factory):
    import base64
    from uuid import UUID, uuid4

    from fastapi import HTTPException

    from app.api.dependencies import current_actor
    from app.identity.policy import Actor
    from app.registry.models import ReferenceItem
    from app.registry.value_validation import validate_value
    from tests.conftest import login_admin
    from tests.test_config_api import admin_headers

    login_admin(client, factory)
    headers = admin_headers(client)
    reference = client.post(
        "/api/v1/references", headers=headers, json={"code": "legacy", "name": "Legacy"}
    ).json()
    endpoint = f"/api/v1/references/{reference['id']}"
    row = client.post(endpoint + "/items", headers=headers, json={"label": "Historical"}).json()
    identity = UUID(row["id"])
    with factory.begin() as db:
        item = db.get(ReferenceItem, identity)
        item.source_database, item.source_entity, item.source_id = "synthetic", "ref", "0001"
    assert (
        client.patch(
            endpoint + f"/items/{identity}", headers=headers, json={"source_id": "2"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            endpoint + f"/items/{identity}", headers=headers, json={"active": False}
        ).status_code
        == 200
    )
    field = {"key": "legacy", "type": "reference", "reference_set_id": reference["id"]}
    actor = Actor(uuid4(), "Editor", frozenset({"editor"}))
    with factory() as db:
        assert validate_value(db, actor, field, str(identity), None, previous=str(identity)) == str(
            identity
        )
        with pytest.raises(HTTPException) as error:
            validate_value(db, actor, field, str(identity), None)
        assert error.value.status_code == 422
    exported = client.get(endpoint + "/export?format=xlsx")
    imported = client.post(
        endpoint + "/import",
        headers=headers,
        json={"format": "xlsx", "content": base64.b64encode(exported.content).decode()},
    )
    assert imported.status_code == 200, imported.text
    with factory() as db:
        assert db.get(ReferenceItem, identity).source_id == "0001"
    try:
        for role in ("editor", "viewer"):
            actor = Actor(uuid4(), role, frozenset({role}))
            client.app.dependency_overrides[current_actor] = lambda: actor
            assert client.get(endpoint + "/history").status_code == 403
            assert client.get(endpoint + "/export").status_code == 403
            assert (
                client.post(
                    endpoint + "/import", json={"format": "csv", "content": "YQ=="}
                ).status_code
                == 403
            )
    finally:
        client.app.dependency_overrides.pop(current_actor, None)

@pytest.mark.parametrize("code", ["executors", "responsible_executors", "signers", "approvers"])
def test_people_metadata_preview_and_exchange_preserve_private_attributes(client, factory, code):
    import base64
    import csv
    import io

    from tests.conftest import login_admin
    from tests.test_config_api import admin_headers

    login_admin(client, factory)
    headers = admin_headers(client)
    reference = client.post("/api/v1/references", headers=headers,
                            json={"code": code, "name": "Synthetic participants"}).json()
    url = f"/api/v1/references/{reference['id']}"
    metadata = {"position": "Synthetic lawyer", "department": "Synthetic unit",
                "source_attributes": {"source_secret": "private"}, "private": "kept"}
    created = client.post(url + "/items", headers=headers,
                          json={"label": "Synthetic person", "metadata": metadata})
    assert created.status_code == 201, created.text
    for mode in ("editor", "viewer"):
        preview = client.get(url + f"/items?mode={mode}").json()[0]
        assert preview["metadata"] == {"position": "Synthetic lawyer", "department": "Synthetic unit"}
        assert preview["source_database"] is None
    for format in ("csv", "xlsx"):
        exported = client.get(url + f"/export?format={format}")
        assert exported.status_code == 200
        imported = client.post(url + "/import", headers=headers,
            json={"format": format, "content": base64.b64encode(exported.content).decode()})
        assert imported.status_code == 200, imported.text
        assert client.get(url + "/items").json()[0]["metadata"] == metadata
    exported = list(csv.DictReader(io.StringIO(client.get(url + "/export").text)))
    assert exported[0]["position"] == "Synthetic lawyer"
    assert exported[0]["department"] == "Synthetic unit"
    assert "private" not in str(exported)
    invalid = client.patch(url + "/items/" + created.json()["id"], headers=headers,
                           json={"metadata": {"position": {"secret": "private"}}})
    assert invalid.status_code == 422


def test_reference_import_target_is_source_qualified_idempotent_and_preserves_manual(factory):
    from uuid import UUID

    from migration.reference_import import plan_snapshot, target_plan
    from sqlalchemy import select

    from app.registry.models import ReferenceItem, ReferenceSet, Registry
    from tests.test_reference_import import snapshot

    raw = snapshot()
    plan = plan_snapshot(raw)
    with factory.begin() as session:
        for source in raw["sources"]:
            session.add(Registry(id=UUID(source["registry_id"]), code=source["database"],
                                 name="Synthetic registry", document_type_ids=[]))
    with factory.begin() as session:
        preview = target_plan(session, plan)
        assert preview["created"] == 20
        assert list(session.scalars(select(ReferenceItem))) == []
    with factory.begin() as session:
        applied = target_plan(session, plan, apply=True)
        assert applied["created"] == 20
        identities = {str(row.id) for row in session.scalars(select(ReferenceItem))}
        book = session.scalar(select(ReferenceSet).where(ReferenceSet.code == "executors"))
        session.add(ReferenceItem(reference_set_id=book.id, label="Manual value",
                                  item_metadata={"manual": True}))
    with factory.begin() as session:
        repeated = target_plan(session, plan, apply=True)
        assert repeated["unchanged"] == 20 and repeated["created"] == 0
        rows = list(session.scalars(select(ReferenceItem)))
        assert len(rows) == 21
        assert identities.issubset({str(row.id) for row in rows})
        manual = next(row for row in rows if row.label == "Manual value")
        assert manual.item_metadata == {"manual": True}


def test_reference_import_never_overwrites_manual_label_collision_and_supports_explicit_adoption(factory):
    from uuid import UUID

    from migration.reference_import import SnapshotError, plan_snapshot, target_plan
    from sqlalchemy import select

    from app.registry.models import ReferenceItem, ReferenceSet, Registry
    from tests.test_reference_import import snapshot

    raw = snapshot()
    registry_id = UUID(raw["sources"][0]["registry_id"])
    with factory.begin() as session:
        for source in raw["sources"]:
            session.add(Registry(id=UUID(source["registry_id"]), code=source["database"],
                                 name="Synthetic registry", document_type_ids=[]))
        session.flush()
        book = ReferenceSet(registry_id=registry_id, code="status", name="Synthetic book")
        session.add(book)
        session.flush()
        manual = ReferenceItem(reference_set_id=book.id, label="Synthetic",
                               item_metadata={"custom": "retained"})
        session.add(manual)
        session.flush()
        identity = str(manual.id)
    with factory.begin() as session:
        with pytest.raises(SnapshotError) as error:
            target_plan(session, plan_snapshot(raw), apply=True)
        assert error.value.errors == {"manual_label_collision": 1}
        assert len(list(session.scalars(select(ReferenceItem)))) == 1
        assert session.get(ReferenceItem, UUID(identity)).source_id is None
    raw["sources"][0]["adopt"] = {"dbo.ref_status": {"1": identity}}
    plan = plan_snapshot(raw)
    with factory.begin() as session:
        result = target_plan(session, plan, apply=True)
        assert result["adopted"] == 1 and result["created"] == 19
        row = session.get(ReferenceItem, UUID(identity))
        assert row.source_database == "okrug" and row.source_id == "1"
        assert row.item_metadata["custom"] == "retained"
    with factory.begin() as session:
        assert target_plan(session, plan, apply=True)["unchanged"] == 20
