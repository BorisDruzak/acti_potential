"""Present configuration captions without changing immutable schema snapshots."""

import re
from copy import deepcopy


def configuration_text(value):
    if not re.search(r"\bACTI[\s_-]*v\s*2\b", value, flags=re.I):
        return value
    return re.sub(r"\s+", " ", re.sub(r"\bACTI[\s_-]*v\s*2\b", "", value, flags=re.I)).strip(" —–:,-") or "Документы"


def configuration_schema(schema):
    result = deepcopy(schema)

    def fields(items):
        for field in items:
            for key in ("label", "description"):
                if isinstance(field.get(key), str):
                    field[key] = configuration_text(field[key])
            fields(field.get("children", []))

    for section in result.get("sections", []):
        section["title"] = configuration_text(section["title"])
        fields(section.get("fields", []))
    for column in result.get("list_view", {}).get("columns", []):
        if isinstance(column.get("label"), str):
            column["label"] = configuration_text(column["label"])
    return result
