from copy import deepcopy

import pytest
from sqlalchemy import select


def schema(field_type="text", **options):
    return {
        "sections": [
            {
                "key": "main",
                "title": "Main",
                "fields": [{"key": "detail", "label": "Detail", "type": field_type, **options}],
            }
        ]
    }


@pytest.mark.parametrize(
    "kind",
    [
        "text",
        "textarea",
        "integer",
        "decimal",
        "date",
        "datetime",
        "boolean",
        "reference",
        "multi_reference",
        "user",
        "department",
        "document",
        "file",
        "group",
    ],
)
def test_schema_accepts_all_supported_field_kinds(kind):
    from app.registry.validation import validate_schema

    extra = {"reference_code": "themes"} if kind in {"reference", "multi_reference"} else {}
    if kind == "group":
        extra = {"children": [{"key": "name", "label": "Name", "type": "text"}]}
    assert validate_schema(schema(kind, **extra))["sections"][0]["fields"][0]["type"] == kind


def test_schema_rejects_code_duplicate_keys_nested_groups_and_type_reuse():
    from app.registry.validation import validate_schema

    for value in [
        schema("python"),
        schema("text", visible_when={"expression": "eval(x)"}),
        schema("group", children=[{"key": "nested", "label": "Nested", "type": "group"}]),
    ]:
        with pytest.raises(ValueError):
            validate_schema(value)
    duplicate = schema()
    duplicate["sections"][0]["fields"] *= 2
    with pytest.raises(ValueError):
        validate_schema(duplicate)
    with pytest.raises(ValueError):
        validate_schema(schema("integer"), previous=schema())


def test_condition_references_and_system_bindings_are_validated():
    from app.registry.validation import validate_schema

    with pytest.raises(ValueError):
        validate_schema(schema("text", system="reg_date"))
    with pytest.raises(ValueError):
        validate_schema(schema("text", required_when={"field": "missing", "op": "present"}))
    result = schema("boolean")
    result["sections"][0]["fields"].append(
        {
            "key": "reason",
            "label": "Reason",
            "type": "text",
            "visible_when": {"field": "detail", "op": "eq", "value": True},
        }
    )
    assert validate_schema(result) == result
    result["sections"][0]["fields"][0]["visible_when"] = {"field": "reason", "op": "present"}
    with pytest.raises(ValueError):
        validate_schema(result)


def test_builtin_is_idempotent_and_registry_scoped(factory):
    from app.registry.builtin import seed_builtin
    from app.registry.models import DocumentType, FormSchema, ReferenceSet, Registry

    with factory.begin() as db:
        seed_builtin(db)
        seed_builtin(db)
        registries = list(db.scalars(select(Registry).where(Registry.code.in_(["okrug", "raion"]))))
        assert len(registries) == 2
        assert registries[0].document_type_ids == registries[1].document_type_ids
        kind = db.scalar(select(DocumentType).where(DocumentType.code == "acti-v2"))
        form = db.scalar(select(FormSchema).where(FormSchema.document_type_id == kind.id))
        assert form.state == "published"
        fields = {f["key"]: f for s in form.schema_data["sections"] for f in s["fields"]}
        assert fields["should_publish"]["type"] == "boolean"
        assert fields["should_publish"]["default"] is None
        assert fields["type_id"]["type"] == "reference"
        assert fields["number"]["label"] == "Номер публикации / газеты"
        assert fields["signers"]["type"] == "multi_reference"
        assert (
            len(
                list(
                    db.scalars(
                        select(ReferenceSet).where(
                            ReferenceSet.registry_id.in_([r.id for r in registries])
                        )
                    )
                )
            )
            == 20
        )


def test_published_schema_cannot_be_updated(factory):
    from sqlalchemy.exc import DBAPIError

    from app.registry.builtin import seed_builtin
    from app.registry.models import FormSchema

    with factory.begin() as db:
        seed_builtin(db)
        form_id = db.scalar(select(FormSchema.id).where(FormSchema.state == "published"))
    with pytest.raises(DBAPIError), factory.begin() as db:
        form = db.get(FormSchema, form_id)
        value = deepcopy(form.schema_data)
        value["sections"][0]["title"] = "Changed"
        form.schema_data = value
        db.flush()


def test_reference_rename_deactivation_and_scoped_source_identity(factory):
    from uuid import uuid4

    from app.registry.models import ReferenceItem, ReferenceSet, Registry

    with factory.begin() as db:
        suffix = uuid4().hex[:8]
        registries = [
            Registry(code=f"synthetic-{suffix}-{i}", name=f"Synthetic {i}") for i in range(2)
        ]
        db.add_all(registries)
        db.flush()
        sets = [
            ReferenceSet(code="same_code", name="Same classification", registry_id=r.id)
            for r in registries
        ]
        db.add_all(sets)
        db.flush()
        items = [
            ReferenceItem(
                reference_set_id=ref.id,
                label="Synthetic original",
                active=True,
                source_database=f"synthetic_{i}_{suffix}",
                source_entity="ref_themes",
                source_id="1",
                source_fingerprint="a" * 64,
            )
            for i, ref in enumerate(sets)
        ]
        db.add_all(items)
        db.flush()
        assert items[0].id != items[1].id
        retained_id = items[0].id
        items[0].label = "Synthetic renamed"
        items[0].active = False
        db.flush()
        assert items[0].id == retained_id


@pytest.mark.parametrize(
    "kind,options",
    [
        ("integer", {"default": True}),
        ("date", {"default": "2026-02-31"}),
        ("text", {"default": "too long", "constraints": {"max_length": 2}}),
        ("text", {"constraints": {"pattern": "(a+)+$"}}),
        ("integer", {"constraints": {"min": 10, "max": 1}}),
    ],
)
def test_schema_rejects_invalid_defaults_constraints_and_expressions(kind, options):
    from app.registry.validation import validate_schema

    with pytest.raises(ValueError):
        validate_schema(schema(kind, **options))
