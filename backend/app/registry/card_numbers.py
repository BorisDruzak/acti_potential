"""Keep a card's reserved number and its publication in the caller's transaction."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.errors import fail
from app.numbering import service
from app.numbering.contracts import AssignInput, ReserveInput
from app.numbering.models import NumberEntry


def sync_card_number(session, request, actor, document, schema, number_entry_id=None, *, target_state=None):
    try:
        with session.no_autoflush:
            service.lock_registry(session, document.registry_id)
            _sync_card_number(session, request, actor, document, schema, number_entry_id, target_state)
        session.flush()
    except IntegrityError:
        fail(409, "number_conflict", "Регистрационный номер уже занят в этом реестре")


def _sync_card_number(session, request, actor, document, schema, number_entry_id, target_state):
    bound = next(
        (
            field
            for section in schema.get("sections", [])
            for field in section.get("fields", [])
            if field.get("system") == "reg_number" and field.get("number_series_id")
        ),
        None,
    )
    linked_entries = list(session.scalars(select(NumberEntry).where(
        NumberEntry.document_id == document.id).with_for_update()))
    scope = service.registration_scope(document.document_type_id, document.reg_date, document.custom_data)
    existing = next((entry for entry in linked_entries if entry.number_string == document.reg_number
                     and (not entry.scope_bound or (entry.registration_year, entry.registration_type_key) == scope)), None)
    if document.card_state == "draft":
        superseded = [old for old in linked_entries if old is not existing and old.state == "reserved"]
        for old in superseded:
            old.state = "cancelled"
            service.operation(session, request, actor, old, "cancelled",
                              f"card:{document.id}:replace:{document.revision}",
                              "Номер заменён в черновике")
        # Release only the live-reservation slot, never its occupied number claim.
        # Historical document links and original response receipts remain intact.
        if superseded:
            session.flush(superseded)
    if number_entry_id is not None:
        if existing is not None and existing.id != number_entry_id:
            fail(409, "number_already_present", "Существующий регистрационный номер защищён")
        candidate = session.get(NumberEntry, number_entry_id)
        if candidate is None:
            fail(404, "number_entry_not_found", "Резерв номера не найден")
        if candidate.document_id not in (None, document.id):
            fail(409, "number_already_linked", "Резерв уже связан с другим документом")
        if candidate.owner_id != actor.id and candidate.document_id != document.id:
            fail(403, "number_forbidden", "Резерв принадлежит другому пользователю")
        if document.reg_number not in (None, "", candidate.number_string):
            fail(422, "number_entry_mismatch", "Номер не соответствует выбранному резерву")
        existing = candidate
    if bound is None and existing is None:
        return
    if existing is None and document.reg_number:
        # Binding a series later must never replace a manual or imported number.
        return
    if existing is not None and existing.state in {"assigned", "void"}:
        if document.reg_number != existing.number_string:
            fail(409, "number_already_present", "Существующий регистрационный номер защищён")
        return

    if existing is not None and bound is not None and str(existing.series_id) != bound["number_series_id"]:
        fail(422, "number_document_scope", "Резерв использует другую серию")

    series = service.lock_series_scope(session, existing.series_id if existing is not None else UUID(bound["number_series_id"]))
    service.ensure_allocation_safe(session, series)
    service.assignment_field(session, actor, series, document, schema)
    published = (target_state or document.card_state) == "published"
    if existing is None:
        reserved = service.reserve(
            session, request, actor, series.id,
            ReserveInput(idempotency_key=f"card:{document.id}:reserve:{document.revision}"),
            registration_scope=service.registration_scope(document.document_type_id, document.reg_date, document.custom_data),
        )
        existing = session.get(NumberEntry, UUID(reserved["items"][0]["id"]))
    permission = "assign" if published else "reserve"
    locked_series, entry = service.locked_entry(session, actor, existing.id, permission)
    if locked_series.id != series.id:
        fail(422, "number_document_scope", "Резерв использует другую серию")
    if entry.state != "reserved" or entry.expires_at and entry.expires_at <= datetime.now(UTC):
        fail(409, "number_not_reserved", "Резерв недоступен или срок истёк")
    linked = entry.document_id is not None
    service.write_entry_number(session, actor, series, entry, document, schema, supplied=number_entry_id is not None)
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
