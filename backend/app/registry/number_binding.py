"""Bridge form metadata to the authoritative registration-number book."""

from uuid import UUID

from app.api.errors import fail
from app.registry.archives import is_archived
from app.registry.models import Document, DocumentType, Registry
from app.registry.validation import iter_fields


def validate_number_bindings(session, schema, registry_ids, document_type_id=None):
    bindings = [f for f in iter_fields(schema) if f.get("number_series_id")]
    if not bindings:
        return
    from app.numbering.models import NumberSeries

    for field in bindings:
        series = session.get(NumberSeries, UUID(field["number_series_id"]))
        if series is None or is_archived(session, "number_series", series.id):
            raise ValueError("Number series does not exist")
        registry = session.get(Registry, series.registry_id)
        if registry is None or is_archived(session, "registry", registry.id):
            raise ValueError("Number series registry does not exist")
        if any(r != series.registry_id for r in registry_ids):
            raise ValueError("Number series is outside the assigned registry")
        if document_type_id is not None:
            kind = session.get(DocumentType, document_type_id)
            if kind is None or is_archived(session, "document_type", document_type_id):
                raise ValueError("Number series document type does not exist")
            if series.document_type_ids and str(document_type_id) not in series.document_type_ids:
                raise ValueError("Number series does not allow this document type")


def protect_registered_number(session, document_id):
    document = session.get(Document, document_id)
    if document is not None and document.card_state == "published":
        fail(403, "registration_locked", "Номер и дата регистрации опубликованной карточки защищены")
