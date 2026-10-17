"""Create participants through an authorized document field, never an arbitrary catalogue."""

from sqlalchemy import select

from app.api.errors import fail
from app.registry.documents import get_document, raw_values
from app.registry.events import emit
from app.registry.models import FormSchema, ReferenceItem, ReferenceSet, Registry
from app.registry.policy import field_accessible, field_writable, permissions_for
from app.registry.references import selectable
from app.registry.validation import iter_fields
from app.registry.value_validation import reference_set

PEOPLE_CODES = frozenset({"executors", "responsible_executors", "signers", "approvers"})


def normalized_label(label):
    return " ".join(label.split())


def create_person(session, request, actor, registry_id, data):
    # Service retrieval scopes do not grant participant administration. A future
    # service mutation needs its own explicit operation and authentication boundary.
    if not actor.roles.intersection({"admin", "editor"}) or actor.scoped_operations is not None:
        fail(403, "forbidden", "Добавление участников не разрешено")

    document = None
    if data.document_id is not None:
        document = get_document(session, actor, data.document_id, "update", lock=True)
        if document.registry_id != registry_id or document.schema_version_id != data.schema_version_id:
            fail(422, "person_context_mismatch", "Контекст карточки изменился")
    else:
        registry = session.get(Registry, registry_id)
        if registry is None or not {"read", "create"}.issubset(permissions_for(session, actor, registry_id)):
            fail(403, "forbidden", "Создание документов в реестре не разрешено")

    schema = session.get(FormSchema, data.schema_version_id)
    if schema is None or schema.state != "published":
        fail(422, "schema_unavailable", "Нет опубликованной формы")
    if document is None and str(schema.document_type_id) not in registry.document_type_ids:
        fail(422, "document_type_unavailable", "Тип не назначен реестру")
    if document is not None and schema.document_type_id != document.document_type_id:
        fail(422, "person_context_mismatch", "Контекст карточки изменился")

    fields = {field["key"]: field for field in iter_fields(schema.schema_data)}
    field = fields.get(data.field_key)
    if field is None or field["type"] not in {"reference", "multi_reference"}:
        fail(422, "people_field_required", "Поле участников недоступно")
    values = (
        raw_values(document, schema.schema_data)
        if document is not None
        else {key: f["default"] for key, f in fields.items() if "default" in f}
    )
    if not field_writable(field, actor) or not field_accessible(field, actor, fields, "editor", values=values):
        fail(403, "forbidden", "Добавление участников в это поле не разрешено")

    book = reference_set(session, field, registry_id)
    if book is None or book.registry_id != registry_id or book.code not in PEOPLE_CODES:
        fail(422, "people_reference_required", "Нужен справочник участников этого реестра")
    # All existing administration mutations acquire this same set lock. Read
    # candidates only after acquiring it so parallel normalized duplicates serialize.
    book = session.scalar(
        select(ReferenceSet).where(ReferenceSet.id == book.id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if book.registry_id != registry_id or book.code not in PEOPLE_CODES:
        fail(422, "people_reference_required", "Нужен справочник участников этого реестра")
    label = normalized_label(data.label)
    matches = [item for item in session.scalars(
        select(ReferenceItem).where(ReferenceItem.reference_set_id == book.id)
        .order_by(ReferenceItem.id).execution_options(populate_existing=True)
    ) if normalized_label(item.label).casefold() == label.casefold()]
    if any(not selectable(item) for item in matches):
        fail(409, "person_unavailable", "Участник уже существует и недоступен для выбора")
    if matches:
        return matches[0], False

    item = ReferenceItem(reference_set_id=book.id, label=label)
    session.add(item)
    session.flush()
    emit(session, request, actor, "reference.item.created", "reference_item", item.id, {
        "reference_set_id": str(book.id),
        "registry_id": str(registry_id),
        "schema_version_id": str(schema.id),
    })
    return item, True
