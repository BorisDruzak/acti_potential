from copy import deepcopy
from types import SimpleNamespace

from app.registry.config_api import _schema_output
from app.registry.presentation import configuration_schema, configuration_text


def test_configuration_captions_hide_legacy_brand_without_mutating_snapshot_or_values():
    schema = {"sections": [{"key": "main", "title": "ACTI v2 — Основные сведения", "fields": [
        {"key": "acti-v2", "label": "Тип документа ACTI v2", "type": "text",
         "default": "ACTI v2 original content", "description": "Описание ACTI v2"},
        {"key": "group", "label": "Group", "type": "group", "children": [
            {"key": "nested", "label": "Исполнитель ACTI v2", "type": "text"}]}]}],
        "list_view": {"columns": [{"key": "acti-v2", "label": "Тип документа ACTI v2"}]}}
    snapshot = deepcopy(schema)
    displayed = configuration_schema(schema)
    assert schema == snapshot
    field = displayed["sections"][0]["fields"][0]
    assert field["label"] == "Тип документа"
    assert field["key"] == "acti-v2" and field["default"] == "ACTI v2 original content"
    assert displayed["sections"][0]["fields"][1]["children"][0]["label"] == "Исполнитель"
    assert displayed["list_view"]["columns"][0]["label"] == "Тип документа"
    row = SimpleNamespace(id="schema", document_type_id="type", version=1, state="published", schema_data=schema)
    assert _schema_output(row, SimpleNamespace(roles={"admin"}))["schema"] == displayed
    assert schema == snapshot
    assert configuration_text("ACTI v2 — нормативные документы") == "нормативные документы"
