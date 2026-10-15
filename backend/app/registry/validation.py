"""Bounded declarative schema validation. No executable expressions are evaluated."""

import re
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

FIELD_TYPES = frozenset(
    {
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
    }
)
SYSTEM_TYPES = {"reg_number": "text", "reg_date": "date", "title": "text", "status": "text"}
FIELD_KEYS = frozenset(
    {
        "key",
        "label",
        "type",
        "description",
        "required",
        "default",
        "constraints",
        "reference_set_id",
        "reference_code",
        "system",
        "editable",
        "modes",
        "read_roles",
        "write_roles",
        "filterable",
        "exportable",
        "visible_when",
        "required_when",
        "children",
    }
)
CONSTRAINT_KEYS = frozenset(
    {"min", "max", "min_length", "max_length", "min_items", "max_items", "choices", "pattern"}
)
ROLES = frozenset({"admin", "user", "editor", "viewer"})
MODES = frozenset({"editor", "viewer"})
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
REFERENCE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,99}$")


def iter_fields(schema):
    """Yield top-level field definitions in stored display order."""
    for section in schema.get("sections", []):
        yield from section.get("fields", [])


def field_map(schema):
    result = {}
    for field in iter_fields(schema):
        result[field["key"]] = field
        for child in field.get("children", []):
            result[f"{field['key']}.{child['key']}"] = child
    return result


def _valid_key(value):
    return isinstance(value, str) and KEY_PATTERN.fullmatch(value) is not None


def _text(value, maximum, required=False):
    return (
        isinstance(value, str) and len(value) <= maximum and (not required or bool(value.strip()))
    )


def validate_pattern(pattern):
    """Linear, portable regular-expression subset: no groups or alternation.

    A single variable repetition avoids polynomial/exponential backtracking.
    Exact repetitions and character classes cover common identifier formats.
    """
    if not isinstance(pattern, str) or not 1 <= len(pattern) <= 256:
        raise ValueError("Pattern must contain between one and 256 characters")
    index, variable_repeats, previous_atom, previous_repeat = 0, 0, False, False
    while index < len(pattern):
        token = pattern[index]
        if token == "\\":
            index += 1
            if index >= len(pattern) or pattern[index].isdigit() or pattern[index] in "AbBZG":
                raise ValueError("Unsupported pattern escape")
            previous_atom, previous_repeat = True, False
        elif token == "[":
            index += 1
            while index < len(pattern) and pattern[index] != "]":
                if pattern[index] == "\\":
                    index += 1
                index += 1
            if index >= len(pattern):
                raise ValueError("Unclosed character class")
            previous_atom, previous_repeat = True, False
        elif token in "()|":
            raise ValueError("Pattern groups and alternation are unsupported")
        elif token in "*+?{":
            if not previous_atom or previous_repeat:
                raise ValueError("Invalid repeated pattern")
            if token == "{":
                end = pattern.find("}", index)
                spec = pattern[index + 1 : end] if end >= 0 else ""
                if not re.fullmatch(r"\d{1,4}(,\d{1,4})?", spec):
                    raise ValueError("Use bounded pattern repetitions")
                limits = [int(v) for v in spec.split(",")]
                if max(limits) > 1000 or limits[0] > limits[-1]:
                    raise ValueError("Pattern repetition exceeds limit")
                variable_repeats += limits[0] != limits[-1]
                index = end
            else:
                variable_repeats += 1
            previous_repeat = True
        elif token in "^$":
            previous_atom = False
        else:
            previous_atom, previous_repeat = True, False
        index += 1
    if variable_repeats > 1:
        raise ValueError("Pattern allows only one variable repetition")
    try:
        re.compile(pattern)
    except re.error:
        raise ValueError("Invalid regular expression") from None


def _default_type(field):
    value = field.get("default")
    if value is None:
        return
    kind = field["type"]
    valid = True
    if kind in {"text", "textarea"}:
        valid = isinstance(value, str)
    elif kind == "integer":
        valid = type(value) is int
    elif kind == "decimal":
        try:
            valid = not isinstance(value, bool) and Decimal(str(value)).is_finite()
        except (InvalidOperation, TypeError, ValueError):
            valid = False
    elif kind == "boolean":
        valid = type(value) is bool
    elif kind in {"multi_reference", "group", "file"}:
        valid = isinstance(value, list)
        if kind == "group":
            valid = valid and all(isinstance(item, dict) for item in value)
            if valid:
                children = {child["key"]: child for child in field.get("children", [])}
                for row in value:
                    if row.keys() - children.keys():
                        raise ValueError("Group default contains unknown child fields")
                    for key, entry in row.items():
                        _default_type({**children[key], "default": entry})
        elif valid:
            for item in value:
                try:
                    UUID(str(item))
                except (ValueError, TypeError):
                    valid = False
        if kind == "file" and value:
            valid = False
    elif kind in {"reference", "department", "user", "document"}:
        try:
            UUID(str(value))
        except (ValueError, TypeError):
            valid = False
    elif kind in {"date", "datetime"}:
        try:
            if not isinstance(value, str):
                raise ValueError()
            (date.fromisoformat if kind == "date" else datetime.fromisoformat)(value)
        except (ValueError, TypeError):
            valid = False
    if not valid:
        raise ValueError("Default does not match the field type")
    constraints = field.get("constraints", {})
    if "choices" in constraints and value not in constraints["choices"]:
        raise ValueError("Default is outside choices")
    if isinstance(value, (str, list)):
        minimum = constraints.get("min_length" if isinstance(value, str) else "min_items")
        maximum = constraints.get("max_length" if isinstance(value, str) else "max_items")
        if minimum is not None and len(value) < minimum:
            raise ValueError("Default is below minimum length")
        if maximum is not None and len(value) > maximum:
            raise ValueError("Default exceeds maximum length")
    if constraints.get("pattern") and isinstance(value, str):
        if not re.fullmatch(constraints["pattern"], value):
            raise ValueError("Default does not match pattern")
    if kind in {"integer", "decimal"}:
        for name, compare in (("min", lambda a, b: a < b), ("max", lambda a, b: a > b)):
            if name in constraints and compare(
                Decimal(str(value)), Decimal(str(constraints[name]))
            ):
                raise ValueError("Default is outside the numeric range")


def validate_schema(value: dict, previous: dict | None = None) -> dict:
    if (
        not isinstance(value, dict)
        or "sections" not in value
        or set(value) - {"sections", "list_view"}
    ):
        raise ValueError("Schema must contain sections and optional list_view")
    sections = value["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= 30:
        raise ValueError("Provide between one and thirty sections")
    section_keys, top_keys, systems = set(), set(), set()
    count = 0

    def check_field(field, depth=0):
        nonlocal count
        count += 1
        if count > 300 or not isinstance(field, dict) or set(field) - FIELD_KEYS:
            raise ValueError("Invalid field definition")
        if not _valid_key(field.get("key")) or not _text(field.get("label"), 255, True):
            raise ValueError("A field needs a stable key and label")
        kind = field.get("type")
        if not isinstance(kind, str) or kind not in FIELD_TYPES:
            raise ValueError("Unsupported field type")
        if "description" in field and not _text(field["description"], 5000):
            raise ValueError("Invalid description")
        for flag in ("required", "editable", "filterable", "exportable"):
            if flag in field and type(field[flag]) is not bool:
                raise ValueError("Field flags must be booleans")
        for key, allowed in (("modes", MODES), ("read_roles", ROLES), ("write_roles", ROLES)):
            if key in field and (
                not isinstance(field[key], list)
                or any(not isinstance(v, str) or v not in allowed for v in field[key])
            ):
                raise ValueError("Unknown role or display mode")
        system = field.get("system")
        if system is not None:
            if (
                depth
                or not isinstance(system, str)
                or system not in SYSTEM_TYPES
                or kind != SYSTEM_TYPES[system]
            ):
                raise ValueError("Invalid system binding")
            if system in systems:
                raise ValueError("A system value may only be bound once")
            systems.add(system)
        constraints = field.get("constraints", {})
        if not isinstance(constraints, dict) or set(constraints) - CONSTRAINT_KEYS:
            raise ValueError("Unknown constraint")
        if "pattern" in constraints:
            if kind not in {"text", "textarea"}:
                raise ValueError("Patterns apply to text fields only")
            validate_pattern(constraints["pattern"])
        for key in ("min_length", "max_length", "min_items", "max_items"):
            if key in constraints and (
                type(constraints[key]) is not int or not 0 <= constraints[key] <= 100000
            ):
                raise ValueError("Invalid length constraint")
        for key in ("min", "max"):
            if key in constraints:
                try:
                    if (
                        isinstance(constraints[key], bool)
                        or not Decimal(str(constraints[key])).is_finite()
                    ):
                        raise ValueError()
                except (InvalidOperation, TypeError, ValueError):
                    raise ValueError("Invalid numeric constraint") from None
        for lower, upper in (
            ("min", "max"),
            ("min_length", "max_length"),
            ("min_items", "max_items"),
        ):
            if lower in constraints and upper in constraints:
                if Decimal(str(constraints[lower])) > Decimal(str(constraints[upper])):
                    raise ValueError("Minimum exceeds maximum")
        if "choices" in constraints:
            choices = constraints["choices"]
            if (
                not isinstance(choices, list)
                or not 1 <= len(choices) <= 500
                or any(
                    not isinstance(v, (str, int, float, bool)) and v is not None for v in choices
                )
            ):
                raise ValueError("Invalid choices")
        for name in ("reference_set_id", "reference_code"):
            if field.get(name) is not None and kind not in {
                "reference",
                "multi_reference",
                "department",
            }:
                raise ValueError("Only reference fields may select a reference set")
        if field.get("reference_set_id"):
            try:
                UUID(str(field["reference_set_id"]))
            except (ValueError, TypeError):
                raise ValueError("Invalid reference set UUID") from None
        if field.get("reference_code") and (
            not isinstance(field["reference_code"], str)
            or REFERENCE_CODE_PATTERN.fullmatch(field["reference_code"]) is None
        ):
            raise ValueError("Invalid reference code")
        if kind in {"reference", "multi_reference"} and not (
            field.get("reference_set_id") or field.get("reference_code")
        ):
            raise ValueError("A reference field needs a reference set")
        if kind == "group":
            if depth:
                raise ValueError("Nested repeating groups are not supported")
            children = field.get("children", [])
            if not isinstance(children, list) or not children:
                raise ValueError("A repeating group needs children")
            child_keys = set()
            for child in children:
                check_field(child, 1)
                if child["key"] in child_keys:
                    raise ValueError("Duplicate child key")
                child_keys.add(child["key"])
        elif field.get("children"):
            raise ValueError("Only repeating groups have children")
        _default_type(field)

    for section in sections:
        if not isinstance(section, dict) or set(section) != {"key", "title", "fields"}:
            raise ValueError("Invalid section")
        if not _valid_key(section["key"]) or section["key"] in section_keys:
            raise ValueError("Section keys must be unique")
        section_keys.add(section["key"])
        if not _text(section["title"], 255, True) or not isinstance(section["fields"], list):
            raise ValueError("Invalid section title or fields")
        for field in section["fields"]:
            check_field(field)
            if field["key"] in top_keys:
                raise ValueError("Field keys must be unique")
            top_keys.add(field["key"])

    all_fields = field_map(value)
    dependencies = {key: set() for key in top_keys}
    for path, field in all_fields.items():
        for name in ("visible_when", "required_when"):
            condition = field.get(name)
            if condition is None:
                continue
            if not isinstance(condition, dict) or set(condition) - {"field", "op", "value"}:
                raise ValueError("Conditions use field, op and value only")
            if not isinstance(condition.get("op"), str) or condition.get("op") not in {
                "eq",
                "ne",
                "in",
                "not_in",
                "present",
                "absent",
            }:
                raise ValueError("Unknown condition operator")
            if (
                not isinstance(condition.get("field"), str)
                or condition.get("field") not in top_keys
                or condition["field"] == path.split(".")[0]
            ):
                raise ValueError("Condition must refer to another top-level field")
            dependencies[path.split(".")[0]].add(condition["field"])
            if condition["op"] in {"eq", "ne", "in", "not_in"} and "value" not in condition:
                raise ValueError("Condition needs a comparison value")
            if condition["op"] in {"present", "absent"}:
                if "value" in condition:
                    raise ValueError("Presence conditions do not take a value")
                continue
            operands = condition.get("value")
            if condition["op"] in {"in", "not_in"}:
                if not isinstance(operands, list) or not 1 <= len(operands) <= 500:
                    raise ValueError("Membership condition needs a bounded nonempty list")
            else:
                operands = [operands]
            dependency = all_fields[condition["field"]]
            if dependency["type"] in {"group", "file"}:
                raise ValueError("Composite fields support presence conditions only")
            for operand in operands:
                _default_type({**dependency, "constraints": {}, "default": operand})
    visiting, visited = set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError("Conditional field dependencies cannot form a cycle")
        if key in visited:
            return
        visiting.add(key)
        for dependency in dependencies[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in top_keys:
        visit(key)
    if "list_view" in value:
        view = value["list_view"]
        if not isinstance(view, dict) or set(view) != {"columns"}:
            raise ValueError("List view contains columns only")
        columns = view["columns"]
        if not isinstance(columns, list) or not 1 <= len(columns) <= 50:
            raise ValueError("List view needs one to fifty columns")
        keys = set()
        for column in columns:
            if (
                not isinstance(column, dict)
                or set(column) != {"key", "label"}
                or not isinstance(column.get("key"), str)
                or column["key"] not in top_keys
                or column["key"] in keys
                or not _text(column.get("label"), 255, True)
            ):
                raise ValueError("Invalid or duplicate list column")
            keys.add(column["key"])
    if previous is not None:
        old_fields = field_map(previous)
        for key, field in field_map(value).items():
            old = old_fields.get(key)
            if old is not None and any(
                field.get(p) != old.get(p)
                for p in ("type", "system", "reference_set_id", "reference_code")
            ):
                raise ValueError("An existing key cannot change its type or meaning")
    return deepcopy(value)
