"""Transactional number allocation. Every transition locks the owning series first."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select

from app.api.errors import fail
from app.numbering.contracts import PERMISSIONS
from app.numbering.models import (
    NumberCounter,
    NumberEntry,
    NumberGrant,
    NumberOperation,
    NumberRequest,
    NumberSeries,
    RegistryNumberClaim,
)
from app.registry.archives import configuration_active_clause, is_archived
from app.registry.documents import (
    append_history,
    check_revision,
    get_document,
    get_schema,
    raw_values,
    write_values,
)
from app.registry.events import emit
from app.registry.models import Document, DocumentType, FormSchema, Registry
from app.registry.policy import (
    accessible_document_clause,
    field_accessible,
    field_writable,
    permissions_for,
)


def period_key(series, now=None):
    if series.current_period:
        return series.current_period
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(series.timezone))
    return str(
        local.year - ((local.month, local.day) < (series.boundary_month, series.boundary_day))
    )


def render_number(series, period, ordinal):
    result = series.format.format(
        ordinal=str(ordinal).zfill(series.padding),
        period=period,
        prefix=series.prefix,
        suffix=series.suffix,
        code=series.code,
    )
    if len(result) > 255:
        fail(422, "number_format_limit", "Регистрационный номер превышает 255 символов")
    return result


def get_series(session, series_id, *, lock=False):
    query = select(NumberSeries).where(NumberSeries.id == series_id, configuration_active_clause(NumberSeries, "number_series"))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = session.scalar(query)
    if result is None:
        fail(404, "number_series_not_found", "Серия не найдена")
    return result


def permissions(session, actor, series):
    if is_archived(session, "number_series", series.id):
        return set()
    grant = session.get(NumberGrant, (series.id, actor.id))
    if "read" not in permissions_for(session, actor, series.registry_id):
        return set()
    allowed = set(grant.permissions if grant else []) & PERMISSIONS
    if not actor.roles & {"admin", "editor"}:
        allowed &= {"view"}
    return allowed


def require(session, actor, series, action):
    allowed = permissions(session, actor, series)
    if action not in allowed or "view" not in allowed:
        fail(403, "number_forbidden", "Действие в реестре номеров не разрешено")


def validate_scope(session, data):
    registry = session.get(Registry, data.registry_id)
    if registry is None or is_archived(session, "registry", data.registry_id):
        fail(422, "number_registry", "Реестр не существует")
    types = set(str(v) for v in data.document_type_ids)
    if types - set(registry.document_type_ids):
        fail(422, "number_types", "Типы серии должны быть назначены реестру")
    active_types = {str(identity) for identity in session.scalars(
        select(DocumentType.id).where(DocumentType.id.in_([UUID(value) for value in types]),
                                      configuration_active_clause(DocumentType, "document_type"))
    )}
    if types - active_types:
        fail(422, "number_types", "Типы серии недоступны")


def ensure_allocation_safe(session, series):
    if series.state != "active":
        fail(409, "number_series_blocked", "Серия заблокирована")
    validate_scope(session, series)


def lock_registry(session, registry_id):
    """Registry precedes series locks, including across independent series."""
    # NO KEY UPDATE serializes allocation while remaining compatible with the
    # foreign-key KEY SHARE locks held by newly inserted, still blank cards.
    session.scalar(select(Registry).where(Registry.id == registry_id).with_for_update(key_share=True))


def lock_series_scope(session, series_id):
    with session.no_autoflush:
        registry_id = session.scalar(select(NumberSeries.registry_id).where(NumberSeries.id == series_id))
        if registry_id is None:
            fail(404, "number_series_not_found", "Серия не найдена")
        lock_registry(session, registry_id)
        return get_series(session, series_id, lock=True)


def number_occupied(session, registry_id, number):
    return session.scalar(select(RegistryNumberClaim.number_key).where(
        RegistryNumberClaim.registry_id == registry_id,
        RegistryNumberClaim.number_key == func.acti_number_key(number),
    )) is not None


def next_card_number(session, request, actor, registry_id, data):
    """Reserve for an unsaved form; never save its fields or advance its revision."""
    from app.numbering.contracts import ReserveInput

    document = None
    if data.document_id:
        document = get_document(session, actor, data.document_id, "update", lock=True)
        if document.registry_id != registry_id or document.document_type_id != data.document_type_id:
            fail(422, "number_document_scope", "Документ не соответствует реестру и типу")
        if data.revision is None:
            fail(422, "revision_required", "Укажите версию карточки")
        check_revision(document, data.revision)
        if document.card_state == "published":
            fail(403, "registration_locked", "Номер и дата регистрации опубликованной карточки защищены")
        if data.schema_version_id and document.schema_version_id != data.schema_version_id:
            fail(422, "number_document_scope", "Версия формы не соответствует карточке")
        schema = get_schema(session, document)
    else:
        allowed = permissions_for(session, actor, registry_id)
        if not {"read", "create"}.issubset(allowed):
            fail(403, "number_forbidden", "Создание карточек в реестре не разрешено")
        query = select(FormSchema).where(FormSchema.document_type_id == data.document_type_id,
                                         FormSchema.state == "published")
        if data.schema_version_id:
            query = query.where(FormSchema.id == data.schema_version_id)
        version = session.scalar(query.order_by(FormSchema.version.desc()).limit(1))
        if version is None:
            fail(422, "schema_unavailable", "Нет опубликованной формы")
        schema = version.schema_data
    registry = session.get(Registry, registry_id)
    if (registry is None or str(data.document_type_id) not in registry.document_type_ids
            or is_archived(session, "document_type", data.document_type_id)):
        fail(422, "document_type_unavailable", "Тип не назначен реестру")
    fields = {f["key"]: f for section in schema.get("sections", []) for f in section.get("fields", [])}
    field = next((f for f in fields.values() if f.get("system") == "reg_number"), None)
    if field is None or not field_writable(field, actor) or not field_accessible(
        field, actor, fields, values=raw_values(document, schema) if document else {}
    ):
        fail(403, "number_field_forbidden", "Поле регистрационного номера недоступно")
    lock_registry(session, registry_id)
    if field.get("number_series_id"):
        series = get_series(session, UUID(field["number_series_id"]), lock=True)
        if series.registry_id != registry_id or (series.document_type_ids and
                str(data.document_type_id) not in series.document_type_ids):
            fail(422, "number_document_scope", "Серия не соответствует реестру и типу")
    else:
        # This new v3 sequence has no relationship to a historical SQL counter.
        # A non-resetting period keeps decimal identifiers stable across years.
        code = "registry-v3-" + registry_id.hex
        series = session.scalar(select(NumberSeries).where(NumberSeries.code == code))
        if series is None:
            series = NumberSeries(code=code, name="Номера карточек: " + registry.name[:230],
                                  registry_id=registry_id, document_type_ids=[], synthetic=False,
                                  period_mode="manual", current_period="v3", padding=1,
                                  format="{ordinal}", reservation_hours=None,
                                  allow_indefinite=True)
            session.add(series)
            session.flush()
        if series.registry_id != registry_id:
            fail(409, "number_series_scope", "Серия принадлежит другому реестру")
        # Registry permissions authorize this default card action. Explicit
        # form-bound series retain their independent number-book ACL.
        grant = session.get(NumberGrant, (series.id, actor.id))
        if grant is None:
            session.add(NumberGrant(series_id=series.id, user_id=actor.id,
                                    permissions=["view", "reserve", "assign"]))
            session.flush()
    key = "card-next:" + hashlib.sha256(data.idempotency_key.encode()).hexdigest()
    reserved = reserve(session, request, actor, series.id,
                       ReserveInput(idempotency_key=key), receipt_data=data.model_copy(
                           update={"idempotency_key": key}), receipt_action="card-next")
    item = reserved["items"][0]
    from app.registry.value_validation import validate_value

    validate_value(session, actor, field, item["number"], registry_id)
    return {"number": item["number"], "number_entry_id": item["id"], "series_id": item["series_id"]}


def visible_entries(session, actor, series, *, period=None, state=None, mine=False, search=None):
    require(session, actor, series, "view")
    query = select(NumberEntry).where(NumberEntry.series_id == series.id)
    if period:
        query = query.where(NumberEntry.period_key == period)
    if state:
        query = query.where(NumberEntry.state == state)
    if mine:
        query = query.where(NumberEntry.owner_id == actor.id)
    if search:
        query = query.where(
            NumberEntry.number_string.ilike(
                "%" + search.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"
            )
        )
    documents = select(Document.id).where(accessible_document_clause(session, actor))
    query = query.where(
        or_(NumberEntry.document_id.is_(None), NumberEntry.document_id.in_(documents))
    )
    for entry in session.scalars(
        query.order_by(NumberEntry.period_key.desc(), NumberEntry.ordinal).execution_options(
            yield_per=200
        )
    ):
        if entry_visible(session, actor, entry):
            yield entry


def entry_visible(session, actor, entry):
    if entry.document_id is None:
        return True
    document = session.scalar(
        select(Document).where(
            Document.id == entry.document_id, accessible_document_clause(session, actor)
        )
    )
    if document is None:
        return False
    schema = get_schema(session, document)
    fields = {
        f["key"]: f for section in schema.get("sections", []) for f in section.get("fields", [])
    }
    number_field = next(
        (field for field in fields.values() if field.get("system") == "reg_number"), None
    )
    return bool(
        number_field
        and field_accessible(number_field, actor, fields, values=raw_values(document, schema))
    )


def output(session, actor, entry):
    document = session.get(Document, entry.document_id) if entry.document_id else None
    title = None
    if document:
        from app.registry.policy import redact_values

        schema = get_schema(session, document)
        values = redact_values(schema, raw_values(document, schema), actor)
        title_key = next(
            (
                f["key"]
                for section in schema.get("sections", [])
                for f in section.get("fields", [])
                if f.get("system") == "title"
            ),
            None,
        )
        title = values.get(title_key)
    return {
        "id": entry.id,
        "series_id": entry.series_id,
        "period_key": entry.period_key,
        "ordinal": entry.ordinal,
        "number": entry.number_string,
        "state": entry.state,
        "reserved_at": entry.reserved_at,
        "expires_at": entry.expires_at,
        "document_id": entry.document_id,
        "document_title": title,
        "owner_id": entry.owner_id if actor.roles & {"admin", "editor"} else None,
    }


def request_hash(action, data):
    payload = {
        "action": action,
        "payload": data.model_dump(mode="json", exclude={"idempotency_key"}, exclude_unset=True),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def replay(session, actor, series, action, data):
    previous = session.get(NumberRequest, (series.id, actor.id, data.idempotency_key))
    digest = request_hash(action, data)
    if previous:
        if previous.payload_hash != digest:
            fail(409, "idempotency_conflict", "Ключ уже использован для другой операции")
        current_entries = []
        for identity in previous.entry_ids:
            entry = session.get(NumberEntry, UUID(identity))
            if entry is None or not entry_visible(session, actor, entry):
                fail(404, "number_entry_not_found", "Номер недоступен")
            current_entries.append(output(session, actor, entry))
        # The immutable receipt records the original operation. Delivery always
        # applies today's document/field ACL, including changed title visibility.
        return jsonable_encoder({"items": current_entries} if action in {"reserve", "card-next"} else current_entries[0])
    return None


def remember(session, actor, series, action, data, entries, response):
    encoded = jsonable_encoder(response)
    session.add(
        NumberRequest(
            series_id=series.id,
            actor_id=actor.id,
            key=data.idempotency_key,
            payload_hash=request_hash(action, data),
            entry_ids=[str(e.id) for e in entries],
            response=encoded,
        )
    )
    session.flush()
    return encoded


def operation(session, request, actor, entry, action, key, reason=""):
    session.add(
        NumberOperation(
            id=uuid4(),
            series_id=entry.series_id,
            entry_id=entry.id,
            action=action,
            actor_id=actor.id,
            reason=reason,
            source="api",
            idempotency_key=key,
        )
    )
    emit(
        session,
        request,
        actor,
        "number." + action,
        "number_entry",
        entry.id,
        {"series_id": str(entry.series_id)},
    )


def reserve(session, request, actor, series_id, data, *, receipt_data=None, receipt_action="reserve"):
    series = lock_series_scope(session, series_id)
    require(session, actor, series, "reserve")
    receipt = receipt_data if receipt_data is not None else data
    previous = replay(session, actor, series, receipt_action, receipt)
    if previous is not None:
        return previous
    ensure_allocation_safe(session, series)
    if data.count > series.max_batch:
        fail(422, "number_batch_limit", "Количество превышает лимит серии")
    hours = (
        data.reservation_hours
        if "reservation_hours" in data.model_fields_set
        else series.reservation_hours
    )
    if (
        hours is None
        and not series.allow_indefinite
        or hours is not None
        and hours > series.max_reservation_hours
    ):
        fail(422, "number_expiry_policy", "Срок резервирования не соответствует правилам серии")
    period = period_key(series)
    counter = session.get(NumberCounter, (series.id, period))
    if counter is None:
        counter = NumberCounter(series_id=series.id, period_key=period, next_ordinal=series.start)
        session.add(counter)
    now = datetime.now(UTC)
    entries = []
    ordinal = counter.next_ordinal
    for _ in range(data.count):
        # Retained manual cards, spent reservations, other series and periods
        # all occupy the same registry namespace. Never infer a legacy counter.
        while True:
            if ordinal > 10**15:
                fail(409, "number_exhausted", "Достигнут предел последовательности")
            number = render_number(series, period, ordinal).strip()
            if not number_occupied(session, series.registry_id, number):
                break
            ordinal += series.step
        entry = NumberEntry(
            id=uuid4(),
            series_id=series.id,
            period_key=period,
            ordinal=ordinal,
            number_string=number,
            state="reserved",
            owner_id=actor.id,
            reserved_at=now,
            expires_at=now + timedelta(hours=hours) if hours is not None else None,
        )
        session.add(entry)
        entries.append(entry)
        # Flush each claim so another number in this batch sees it too.
        session.flush()
        ordinal += series.step
    counter.next_ordinal = ordinal
    session.flush()
    for entry in entries:
        operation(session, request, actor, entry, "reserved", data.idempotency_key)
    return remember(
        session,
        actor,
        series,
        receipt_action,
        receipt,
        entries,
        {"items": [output(session, actor, entry) for entry in entries]},
    )


def locked_entry(session, actor, identity, action):
    before = session.get(NumberEntry, identity)
    if before is None:
        fail(404, "number_entry_not_found", "Номер не найден")
    series = lock_series_scope(session, before.series_id)
    require(session, actor, series, action)
    entry = session.scalar(
        select(NumberEntry)
        .where(NumberEntry.id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not entry_visible(session, actor, entry):
        fail(404, "number_entry_not_found", "Номер не найден")
    return series, entry


def assignment_field(session, actor, series, document, schema):
    """Validate the same scope and field policy for explicit and card allocation."""
    if (
        document.registry_id != series.registry_id
        or series.document_type_ids
        and str(document.document_type_id) not in series.document_type_ids
    ):
        fail(422, "number_document_scope", "Документ не соответствует серии")
    fields = {
        f["key"]: f for section in schema.get("sections", []) for f in section.get("fields", [])
    }
    bound = next(
        (
            f
            for f in fields.values()
            if f.get("system") == "reg_number" and f.get("number_series_id") in (None, str(series.id))
        ),
        None,
    )
    if (
        bound is None
        or not field_writable(bound, actor)
        or not field_accessible(bound, actor, fields, values=raw_values(document, schema))
    ):
        fail(403, "number_field_forbidden", "Поле номера недоступно или использует другую серию")
    return bound


def write_entry_number(session, actor, series, entry, document, schema, *, supplied=False):
    bound = assignment_field(session, actor, series, document, schema)
    if entry.document_id not in (None, document.id):
        fail(409, "number_already_linked", "Резерв уже связан с другим документом")
    if document.reg_number and not (
        (entry.document_id == document.id or supplied) and document.reg_number == entry.number_string
    ):
        fail(
            409, "number_already_present", "Существующий регистрационный номер нельзя перезаписать"
        )
    # Move the existing reservation claim before either ORM row flushes; this
    # allows the database triggers to validate both write orders atomically.
    with session.no_autoflush:
        claim = session.scalar(select(RegistryNumberClaim).where(
            RegistryNumberClaim.registry_id == series.registry_id,
            RegistryNumberClaim.number_entry_id == entry.id,
        ).with_for_update())
        if claim is None or claim.document_id not in (None, document.id):
            fail(409, "number_conflict", "Регистрационный номер уже занят")
        session.connection().execute(RegistryNumberClaim.__table__.update().where(
            RegistryNumberClaim.registry_id == series.registry_id,
            RegistryNumberClaim.number_entry_id == entry.id,
        ).values(document_id=document.id))
        session.expire(claim)
    from app.registry.value_validation import validate_value, validate_values

    validated_number = validate_value(
        session,
        actor,
        bound,
        entry.number_string,
        document.registry_id,
        previous=document.reg_number,
    )
    # Earlier reserved receipts may contain surrounding whitespace. Validate
    # them but keep their exact recorded spelling when linking the card.
    validated_number = entry.number_string
    candidate = {**raw_values(document, schema), bound["key"]: validated_number}
    checked = validate_values(
        session,
        actor,
        schema,
        {},
        document.registry_id,
        previous=candidate,
        document_id=document.id,
    )
    write_values(document, schema, checked)
    entry.document_id = document.id


def assign(session, request, actor, identity, data):
    # Card updates already lock the document before its series. Use that order
    # here too, so publication cannot deadlock with an explicit assignment.
    document = get_document(session, actor, data.document_id, "update", lock=True)
    series, entry = locked_entry(session, actor, identity, "assign")
    action = "assign:" + str(identity)
    previous = replay(session, actor, series, action, data)
    if previous is not None:
        return previous
    if document.card_state == "published" and document.reg_number != entry.number_string:
        fail(403, "registration_locked", "Номер и дата регистрации опубликованной карточки защищены")
    ensure_allocation_safe(session, series)
    if entry.state != "reserved" or entry.expires_at and entry.expires_at <= datetime.now(UTC):
        fail(409, "number_not_reserved", "Резерв недоступен или срок истёк")
    check_revision(document, data.revision)
    schema = get_schema(session, document)
    write_entry_number(session, actor, series, entry, document, schema)
    document.revision += 1
    document.updated_at = datetime.now(UTC)
    entry.state = "assigned" if document.card_state == "published" else "reserved"
    session.flush()
    append_history(session, document, schema, actor)
    emit(
        session,
        request,
        actor,
        "document.metadata.updated",
        "document",
        document.id,
        {
            "revision": document.revision,
            "schema_version_id": str(document.schema_version_id),
            "registry_id": str(document.registry_id),
        },
    )
    operation(
        session, request, actor, entry,
        "assigned" if entry.state == "assigned" else "linked", data.idempotency_key,
    )
    return remember(session, actor, series, action, data, [entry], output(session, actor, entry))


def transition(session, request, actor, identity, data, target):
    permission = "void" if target == "void" else "cancel"
    series, entry = locked_entry(session, actor, identity, permission)
    action = target + ":" + str(identity)
    previous = replay(session, actor, series, action, data)
    if previous is not None:
        return previous
    expected = "assigned" if target == "void" else "reserved"
    if entry.state != expected:
        fail(409, "number_transition", "Переход состояния номера не разрешён")
    if target == "expired" and (entry.expires_at is None or entry.expires_at > datetime.now(UTC)):
        fail(409, "number_not_expired", "Срок резерва ещё не истёк")
    entry.state = target
    session.flush()
    operation(session, request, actor, entry, target, data.idempotency_key, data.reason)
    return remember(session, actor, series, action, data, [entry], output(session, actor, entry))
