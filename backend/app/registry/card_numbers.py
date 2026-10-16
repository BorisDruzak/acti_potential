"""Keep a card's reserved number and its publication in the caller's transaction."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.api.errors import fail
from app.numbering import service
from app.numbering.contracts import AssignInput, ReserveInput
from app.numbering.models import NumberEntry


def sync_card_number(session, request, actor, document, schema):
    bound = next(
        (
            field
            for section in schema.get("sections", [])
            for field in section.get("fields", [])
            if field.get("system") == "reg_number" and field.get("number_series_id")
        ),
        None,
    )
    if bound is None:
        return
    existing = session.scalar(select(NumberEntry).where(NumberEntry.document_id == document.id))
    if existing is None and document.reg_number:
        # Binding a series later must never replace a manual or imported number.
        return
    if existing is not None and existing.state in {"assigned", "void"}:
        if document.reg_number != existing.number_string:
            fail(409, "number_already_present", "Существующий регистрационный номер защищён")
        return

    if existing is not None and str(existing.series_id) != bound["number_series_id"]:
        fail(422, "number_document_scope", "Резерв использует другую серию")

    series = service.get_series(session, UUID(bound["number_series_id"]), lock=True)
    service.ensure_allocation_safe(session, series)
    service.assignment_field(session, actor, series, document, schema)
    published = document.card_state == "published"
    if existing is None:
        reserved = service.reserve(
            session, request, actor, series.id,
            ReserveInput(idempotency_key=f"card:{document.id}:reserve"),
        )
        existing = session.get(NumberEntry, UUID(reserved["items"][0]["id"]))
    permission = "assign" if published else "reserve"
    locked_series, entry = service.locked_entry(session, actor, existing.id, permission)
    if locked_series.id != series.id:
        fail(422, "number_document_scope", "Резерв использует другую серию")
    if entry.state != "reserved" or entry.expires_at and entry.expires_at <= datetime.now(UTC):
        fail(409, "number_not_reserved", "Резерв недоступен или срок истёк")
    linked = entry.document_id is not None
    service.write_entry_number(session, actor, series, entry, document, schema)
    if published:
        data = AssignInput(
            document_id=document.id,
            revision=document.revision,
            idempotency_key=f"card:{document.id}:publish",
        )
        entry.state = "assigned"
        session.flush()
        service.operation(session, request, actor, entry, "assigned", data.idempotency_key)
        service.remember(
            session, actor, series, "assign:" + str(entry.id), data,
            [entry], service.output(session, actor, entry),
        )
    elif not linked:
        session.flush()
        service.operation(session, request, actor, entry, "linked", f"card:{document.id}:link")
