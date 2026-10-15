"""Bridge form metadata to the authoritative registration-number book."""

from uuid import UUID

from sqlalchemy import select

from app.api.errors import fail
from app.registry.models import DocumentType, Registry
from app.registry.validation import iter_fields


def validate_number_bindings(session, schema, registry_ids, document_type_id=None):
    bindings = [f for f in iter_fields(schema) if f.get("number_series_id")]
    if not bindings:
        return
    from app.numbering.models import NumberSeries

    for field in bindings:
        series = session.get(NumberSeries, UUID(field["number_series_id"]))
        if series is None:
            raise ValueError("Number series does not exist")
        registry = session.get(Registry, series.registry_id)
        if registry is None or registry.code in {"okrug", "raion"} or not series.synthetic:
            raise ValueError("Historical number issuance is blocked until controlled migration")
        if any(r != series.registry_id for r in registry_ids):
            raise ValueError("Number series is outside the assigned registry")
        if document_type_id is not None:
            kind = session.get(DocumentType, document_type_id)
            if kind is None or kind.code == "acti-v2":
                raise ValueError("Historical ACTI v2 numbering must remain unchanged")
            if series.document_type_ids and str(document_type_id) not in series.document_type_ids:
                raise ValueError("Number series does not allow this document type")


def protect_registered_number(session, document_id):
    from app.numbering.models import NumberEntry

    if session.scalar(select(NumberEntry.id).where(NumberEntry.document_id == document_id).limit(1)):
        fail(403, "number_assignment_required", "Присвоенный номер защищён реестром номеров")
