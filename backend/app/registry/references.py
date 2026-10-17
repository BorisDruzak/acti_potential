"""Shared reference rules. Provenance identities are never accepted by mutation APIs."""

from datetime import date

from app.api.errors import fail
from app.registry.models import ReferenceItem, ReferenceSet

PEOPLE_CODES = frozenset({"executors", "responsible_executors", "signers", "approvers"})
BUSINESS_METADATA = ("position", "department")


def business_metadata(metadata, reference_code):
    if reference_code not in PEOPLE_CODES or not isinstance(metadata, dict):
        return {}
    return {key: " ".join(metadata[key].split()) for key in BUSINESS_METADATA
            if isinstance(metadata.get(key), str) and len(metadata[key]) <= 255 and "\x00" not in metadata[key]}



def selectable(item, today=None):
    today = today or date.today()
    return bool(
        item.active
        and (item.valid_from is None or item.valid_from <= today)
        and (item.valid_until is None or item.valid_until >= today)
    )


def validate_item(session, item):
    with session.no_autoflush:
        book = session.get(ReferenceSet, item.reference_set_id)
    if book and book.code in PEOPLE_CODES:
        for key in BUSINESS_METADATA:
            value = (item.item_metadata or {}).get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 255 or "\x00" in value):
                fail(422, "invalid_reference", "Должность и подразделение должны быть текстом до 255 символов")
    if item.valid_from and item.valid_until and item.valid_from > item.valid_until:
        fail(422, "invalid_reference", "Дата окончания раньше даты начала")
    visited = {item.id} if item.id else set()
    parent_id = item.parent_id
    with session.no_autoflush:
        while parent_id:
            if parent_id in visited:
                fail(422, "invalid_reference", "Иерархия справочника содержит цикл")
            visited.add(parent_id)
            if len(visited) > 100:
                fail(422, "invalid_reference", "Иерархия превышает 100 уровней")
            parent = session.get(ReferenceItem, parent_id)
            if parent is None or parent.reference_set_id != item.reference_set_id:
                fail(422, "invalid_reference", "Родитель принадлежит другому справочнику")
            parent_id = parent.parent_id
