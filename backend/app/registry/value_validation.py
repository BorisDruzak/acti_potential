import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select

from app.api.errors import fail
from app.identity.models import User
from app.registry.models import Document, DocumentFile, ReferenceItem, ReferenceSet
from app.registry.policy import (
    accessible_document_clause,
    condition_matches,
    field_accessible,
    field_readable,
    field_writable,
)

SYSTEM_KEYS = frozenset({"reg_number", "reg_date", "title", "status"})


def invalid(key, reason="Некорректное значение"):
    fail(422, "field_validation", f"{key}: {reason}")


def uuid_value(value, key):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        invalid(key, "Ожидается идентификатор")


def reference_set(session, field, registry_id):
    if field.get("reference_set_id"):
        result = session.get(ReferenceSet, uuid_value(field["reference_set_id"], field["key"]))
    elif field.get("reference_code"):
        result = session.scalar(
            select(ReferenceSet).where(
                ReferenceSet.code == field["reference_code"],
                ReferenceSet.registry_id == registry_id,
            )
        )
        if result is None:
            result = session.scalar(
                select(ReferenceSet).where(
                    ReferenceSet.code == field["reference_code"], ReferenceSet.registry_id.is_(None)
                )
            )
    else:
        return None
    if result is None or result.registry_id not in (None, registry_id):
        invalid(field["key"], "Справочник недоступен в этом реестре")
    return result


def validate_value(
    session,
    actor,
    field,
    value,
    registry_id,
    previous=None,
    document_id=None,
    root_values=None,
    root_fields=None,
    trusted_upload_ids=None,
):
    key, kind = field["key"], field["type"]
    constraints = field.get("constraints") or {}
    if value is None:
        return None
    if kind in {"text", "textarea"}:
        if not isinstance(value, str):
            invalid(key, "Ожидается текст")
        if len(value) > constraints.get("max_length", 100000 if kind == "textarea" else 1000):
            invalid(key, "Текст слишком длинный")
        system_limit = {"reg_number": 255, "status": 100}.get(field.get("system"))
        if system_limit is not None and len(value) > system_limit:
            invalid(key, "Текст превышает размер системного реквизита")
        if len(value) < constraints.get("min_length", 0):
            invalid(key, "Текст слишком короткий")
        # Restricted declarative pattern: bounded pattern and bounded input; reject
        # nested quantifiers/backreferences in schema validation as well.
        if constraints.get("pattern") and not re.fullmatch(constraints["pattern"], value):
            invalid(key, "Значение не соответствует формату")
        choices = constraints.get("choices") or constraints.get("enum")
        if choices is not None and value not in choices:
            invalid(key, "Значение не входит в допустимый список")
    elif kind in {"integer", "decimal"}:
        if kind == "integer":
            if type(value) is not int:
                invalid(key, "Ожидается целое число")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                invalid(key, "Ожидается десятичное число")
            try:
                numeric = Decimal(str(value))
                if not numeric.is_finite():
                    invalid(key)
                value = str(numeric)
            except InvalidOperation:
                invalid(key, "Ожидается десятичное число")
        number = Decimal(str(value))
        minimum = constraints.get("min", constraints.get("minimum"))
        maximum = constraints.get("max", constraints.get("maximum"))
        if minimum is not None and number < Decimal(str(minimum)):
            invalid(key, "Число меньше допустимого")
        if maximum is not None and number > Decimal(str(maximum)):
            invalid(key, "Число больше допустимого")
        if kind == "decimal" and constraints.get("scale") is not None:
            if -number.as_tuple().exponent > constraints["scale"]:
                invalid(key, "Слишком много десятичных знаков")
    elif kind in {"date", "datetime"}:
        if not isinstance(value, str):
            invalid(key, "Ожидается дата ISO")
        try:
            if kind == "date":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    invalid(key)
                value = date.fromisoformat(value).isoformat()
            else:
                value = datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            invalid(key, "Дата не существует")
    elif kind == "boolean":
        if type(value) is not bool:
            invalid(key, "Ожидается Да, Нет или Не указано")
    elif kind in {"reference", "multi_reference", "department"}:
        multiple = kind == "multi_reference"
        if multiple and not isinstance(value, list):
            invalid(key, "Ожидается список")
        entries = value if multiple else [value]
        if len(entries) > 500:
            invalid(key, "Слишком много значений")
        ref = reference_set(session, field, registry_id)
        if ref is None:
            invalid(key, "Не выбран справочник")
        previous_entries = previous if isinstance(previous, list) else [previous]
        normalized = []
        for entry in entries:
            item_id = uuid_value(entry, key)
            item = session.get(ReferenceItem, item_id)
            if (
                item is None
                or item.reference_set_id != ref.id
                or (not item.active and str(item_id) not in previous_entries)
            ):
                invalid(key, "Значение справочника недоступно")
            if str(item_id) not in normalized:
                normalized.append(str(item_id))
        value = normalized if multiple else normalized[0]
    elif kind == "user":
        person = session.get(User, uuid_value(value, key))
        if person is None or (not person.active and str(person.id) != previous):
            invalid(key, "Пользователь недоступен")
        value = str(person.id)
    elif kind == "document":
        target = session.scalar(
            select(Document).where(
                Document.id == uuid_value(value, key), accessible_document_clause(session, actor)
            )
        )
        if target is None:
            invalid(key, "Связанный документ недоступен")
        value = str(target.id)
    elif kind == "file":
        if not isinstance(value, list):
            invalid(key, "Ожидается список файлов")
        if len(value) > 500:
            invalid(key)
        for entry in value:
            row = session.get(DocumentFile, uuid_value(entry, key))
            if row is None or document_id is None or row.document_id != document_id:
                invalid(key, "Файл не принадлежит карточке")
            from app.registry.documents import file_visible, get_schema, raw_values

            owner = session.get(Document, document_id)
            owner_schema = get_schema(session, owner)
            if row.id not in (trusted_upload_ids or set()) and not file_visible(
                owner_schema, actor, row, raw_values(owner, owner_schema)
            ):
                invalid(key, "Файл недоступен")
        value = list(dict.fromkeys(str(uuid_value(v, key)) for v in value))
    elif kind == "group":
        if not isinstance(value, list) or len(value) > constraints.get("max_items", 100):
            invalid(key, "Некорректная повторяемая группа")
        if len(value) < constraints.get("min_items", 0):
            invalid(key, "Недостаточно строк в группе")
        if isinstance(previous, list) and len(previous) != len(value):
            protected = [
                child for child in field.get("children", []) if not field_writable(child, actor)
            ]
            if protected and any(
                any(child["key"] in row for child in protected) for row in previous
            ):
                fail(403, "field_forbidden", "Нельзя удалять строки с защищёнными значениями")
        nested_schema = {"sections": [{"fields": field.get("children", [])}]}
        rows = []
        for index, row in enumerate(value):
            before = previous[index] if isinstance(previous, list) and index < len(previous) else {}
            rows.append(
                validate_values(
                    session,
                    actor,
                    nested_schema,
                    row,
                    registry_id,
                    before,
                    document_id=document_id,
                    _root_values=root_values,
                    _root_fields=root_fields,
                    trusted_upload_ids=trusted_upload_ids,
                )
            )
        value = rows
    else:
        invalid(key, "Неизвестный тип поля")
    if isinstance(value, float) and not math.isfinite(value):
        invalid(key)
    return value


def validate_values(
    session,
    actor,
    schema,
    values,
    registry_id,
    previous=None,
    *,
    document_id=None,
    _root_values=None,
    _root_fields=None,
    trusted_upload_ids=None,
):
    if not isinstance(values, dict):
        invalid("values", "Ожидается объект")
    fields = {f["key"]: f for s in schema.get("sections", []) for f in s.get("fields", [])}
    if values.keys() - fields.keys():
        invalid("values", "Переданы незарегистрированные поля")
    result = dict(previous or {})
    proposed = {**result, **values}
    root_values = proposed if _root_values is None else _root_values
    root_fields = _root_fields or fields
    for key, value in values.items():
        field = fields[key]
        if not field_writable(field, actor):
            fail(403, "field_forbidden", "Изменение поля не разрешено")
        required_condition = field.get("required_when")
        required_dependency = (
            root_fields.get(required_condition["field"]) if required_condition else None
        )
        if required_condition and (
            required_dependency is None
            or not field_accessible(required_dependency, actor, root_fields)
        ):
            fail(403, "field_forbidden", "Изменение поля не разрешено")
        if not field_accessible(field, actor, root_fields, values=root_values):
            fail(403, "field_forbidden", "Поле недоступно в текущем состоянии формы")
        result[key] = validate_value(
            session,
            actor,
            field,
            value,
            registry_id,
            (previous or {}).get(key),
            document_id,
            root_values,
            root_fields,
            trusted_upload_ids,
        )
    for key, field in fields.items():
        if key not in result and "default" in field and field.get("default") is not None:
            result[key] = validate_value(
                session,
                actor,
                field,
                field["default"],
                registry_id,
                document_id=document_id,
                root_values=root_values,
                root_fields=root_fields,
                trusted_upload_ids=trusted_upload_ids,
            )
        required_condition = field.get("required_when")
        dependency = root_fields.get(required_condition["field"]) if required_condition else None
        if required_condition and (
            dependency is None or not field_accessible(dependency, actor, root_fields)
        ):
            if previous is not None or result.get(key) not in (None, "", []):
                continue
            fail(403, "field_forbidden", "Форма содержит защищённое правило обязательности")
        if not field_readable(field, actor) and previous is not None:
            continue
        required = field.get("required", False) or (
            bool(field.get("required_when"))
            and condition_matches(field["required_when"], root_values)
        )
        if required and condition_matches(field.get("visible_when"), root_values):
            if (
                result.get(key) is None
                or result.get(key) == []
                or isinstance(result.get(key), str)
                and not result[key].strip()
            ):
                if not field_readable(field, actor):
                    fail(403, "field_forbidden", "Форма содержит обязательные защищённые поля")
                invalid(key, "Обязательное поле не заполнено")
    return result
