from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import current_actor, current_admin, db_session
from app.api.errors import fail
from app.identity.models import User
from app.numbering import service
from app.numbering.contracts import (
    AssignInput,
    GrantsInput,
    ReserveInput,
    SeriesInput,
    SeriesPatch,
    TransitionInput,
)
from app.numbering.models import (
    NumberCounter,
    NumberEntry,
    NumberGrant,
    NumberOperation,
    NumberSeries,
)
from app.registry.events import emit
from app.registry.tabular import export_file

router = APIRouter()


def series_output(session, actor, row):
    return {
        "id": row.id,
        **{key: getattr(row, key) for key in SeriesInput.model_fields},
        "permissions": sorted(service.permissions(session, actor, row)),
    }


def flush(session):
    try:
        session.flush()
    except IntegrityError:
        fail(409, "number_conflict", "Код, номер или привязка уже используются")


@router.get("/number-series")
def list_series(session=Depends(db_session, scope="function"), actor=Depends(current_actor)):
    return [
        series_output(session, actor, row)
        for row in session.scalars(
            select(NumberSeries).order_by(NumberSeries.name, NumberSeries.id)
        )
        if "admin" in actor.roles or "view" in service.permissions(session, actor, row)
    ]


@router.post("/number-series", status_code=201)
def create_series(
    data: SeriesInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    service.validate_scope(session, data)
    values = data.model_dump()
    values["document_type_ids"] = [str(v) for v in data.document_type_ids]
    row = NumberSeries(**values)
    session.add(row)
    flush(session)
    emit(session, request, actor, "number.series.created", "number_series", row.id)
    return series_output(session, actor, row)


@router.patch("/number-series/{series_id}")
def patch_series(
    series_id: UUID,
    data: SeriesPatch,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    row = service.get_series(session, series_id, lock=True)
    changes = data.model_dump(exclude_unset=True)
    try:
        merged = SeriesInput.model_validate(
            {**{key: getattr(row, key) for key in SeriesInput.model_fields}, **changes}
        )
    except ValidationError:
        fail(422, "invalid_number_series", "Настройки серии не прошли проверку")
    service.validate_scope(session, merged)
    if session.scalar(select(exists().where(NumberEntry.series_id == row.id))):
        if merged.registry_id != row.registry_id or set(
            str(v) for v in merged.document_type_ids
        ) != set(row.document_type_ids):
            fail(409, "number_series_scope_frozen", "Область использованной серии нельзя менять")
        if merged.start != row.start or merged.step != row.step:
            fail(
                409, "number_series_scope_frozen", "Начало и шаг использованной серии нельзя менять"
            )
    for key, value in merged.model_dump().items():
        setattr(row, key, [str(v) for v in value] if key == "document_type_ids" else value)
    flush(session)
    emit(session, request, actor, "number.series.updated", "number_series", row.id)
    return series_output(session, actor, row)


@router.get("/number-series/{series_id}/grants")
def get_grants(
    series_id: UUID, session=Depends(db_session, scope="function"), actor=Depends(current_admin)
):
    service.get_series(session, series_id)
    return {
        "entries": [
            {"user_id": g.user_id, "permissions": g.permissions}
            for g in session.scalars(
                select(NumberGrant)
                .where(NumberGrant.series_id == series_id)
                .order_by(NumberGrant.user_id)
            )
        ]
    }


@router.put("/number-series/{series_id}/grants")
def set_grants(
    series_id: UUID,
    data: GrantsInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    service.get_series(session, series_id, lock=True)
    identities = [entry.user_id for entry in data.entries]
    if len(set(identities)) != len(identities) or len(
        list(session.scalars(select(User.id).where(User.id.in_(identities))))
    ) != len(identities):
        fail(422, "number_grants_invalid", "Проверьте пользователей и дубликаты")
    for old in session.scalars(select(NumberGrant).where(NumberGrant.series_id == series_id)):
        old.permissions = []
    for item in data.entries:
        row = session.get(NumberGrant, (series_id, item.user_id))
        if row is None:
            row = NumberGrant(series_id=series_id, user_id=item.user_id)
            session.add(row)
        row.permissions = sorted(set(item.permissions))
    flush(session)
    emit(session, request, actor, "number.grants.updated", "number_series", series_id)
    return {"entries": [item.model_dump() for item in data.entries]}


@router.post("/number-series/{series_id}/reserve")
def reserve(
    series_id: UUID,
    data: ReserveInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    try:
        return service.reserve(session, request, actor, series_id, data)
    except IntegrityError:
        fail(409, "number_conflict", "Номер или ключ операции уже используются")


@router.post("/number-entries/{entry_id}/assign")
def assign(
    entry_id: UUID,
    data: AssignInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    try:
        return service.assign(session, request, actor, entry_id, data)
    except IntegrityError:
        fail(409, "number_conflict", "Документ уже получил номер")


@router.post("/number-entries/{entry_id}/{action}")
def transition(
    entry_id: UUID,
    action: str,
    data: TransitionInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    states = {"cancel": "cancelled", "void": "void", "expire": "expired"}
    if action not in states:
        fail(404, "number_action_not_found", "Операция не найдена")
    return service.transition(session, request, actor, entry_id, data, states[action])


def filters(period_key, state, mine, search):
    if state is not None and state not in {"reserved", "assigned", "cancelled", "expired", "void"}:
        fail(422, "number_state", "Неизвестное состояние")
    return {"period": period_key, "state": state, "mine": mine, "search": search}


@router.get("/number-series/{series_id}/entries")
def entries(
    series_id: UUID,
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(50, ge=1, le=200),
    period_key: str | None = Query(None, max_length=32),
    state: str | None = None,
    mine: bool = False,
    search: str | None = Query(None, max_length=255),
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    series = service.get_series(session, series_id)
    total, items = 0, []
    for row in service.visible_entries(
        session, actor, series, **filters(period_key, state, mine, search)
    ):
        if (page - 1) * page_size <= total < page * page_size:
            items.append(service.output(session, actor, row))
        total += 1
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/number-series/{series_id}/stats")
def stats(
    series_id: UUID,
    period_key: str | None = Query(None, max_length=32),
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    series = service.get_series(session, series_id)
    period = period_key or service.period_key(series)
    counts = dict.fromkeys(["reserved", "assigned", "cancelled", "expired", "void"], 0)
    visible_count = 0
    for row in service.visible_entries(session, actor, series, period=period):
        counts[row.state] += 1
        visible_count += 1
    total_count = session.scalar(
        select(func.count())
        .select_from(NumberEntry)
        .where(NumberEntry.series_id == series.id, NumberEntry.period_key == period)
    )
    counter = session.get(NumberCounter, (series.id, period))
    next_ordinal = counter.next_ordinal if counter else series.start
    if "reserve" not in service.permissions(session, actor, series) or total_count != visible_count:
        next_ordinal = None
    return {
        "counts": counts,
        "period_key": period,
        "next_ordinal": next_ordinal,
        "next_number": service.render_number(series, period, next_ordinal)
        if next_ordinal is not None
        else None,
    }


@router.get("/number-series/{series_id}/intervals")
def intervals(
    series_id: UUID,
    period_key: str | None = Query(None, max_length=32),
    from_ordinal: int = Query(1, ge=1, le=10**15),
    to_ordinal: int = Query(100, ge=1, le=10**15),
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    series = service.get_series(session, series_id)
    service.require(session, actor, series, "reserve")
    period = period_key or service.period_key(series)
    first = max(series.start, from_ordinal)
    first += (series.start - first) % series.step
    if from_ordinal > to_ordinal or (to_ordinal - first) // series.step + 1 > 1000:
        fail(422, "number_interval_limit", "Интервал должен содержать не более 1000 позиций")
    all_entries = list(
        session.scalars(
            select(NumberEntry).where(
                NumberEntry.series_id == series.id,
                NumberEntry.period_key == period,
                NumberEntry.ordinal >= first,
                NumberEntry.ordinal <= to_ordinal,
            )
        )
    )
    if any(not service.entry_visible(session, actor, row) for row in all_entries):
        fail(403, "number_interval_unavailable", "Интервалы недоступны в текущей области прав")
    occupied = {row.ordinal: row.state for row in all_entries}
    counter = session.get(NumberCounter, (series.id, period))
    frontier = counter.next_ordinal if counter else series.start
    ranges = []
    for ordinal in range(first, to_ordinal + 1, series.step):
        state = occupied.get(ordinal, "free" if ordinal >= frontier else "unavailable")
        if (
            ranges
            and ranges[-1]["state"] == state
            and ranges[-1]["to_ordinal"] + series.step == ordinal
        ):
            ranges[-1]["to_ordinal"] = ordinal
        else:
            ranges.append({"from_ordinal": ordinal, "to_ordinal": ordinal, "state": state})
    return {
        "period_key": period,
        "from_ordinal": from_ordinal,
        "to_ordinal": to_ordinal,
        "step": series.step,
        "intervals": ranges,
    }


@router.get("/number-entries/{entry_id}/history")
def history(
    entry_id: UUID, session=Depends(db_session, scope="function"), actor=Depends(current_actor)
):
    entry = session.get(NumberEntry, entry_id)
    if entry is None:
        fail(404, "number_entry_not_found", "Номер не найден")
    series = service.get_series(session, entry.series_id)
    service.require(session, actor, series, "view")
    if not service.entry_visible(session, actor, entry):
        fail(404, "number_entry_not_found", "Номер не найден")
    privileged = bool(actor.roles & {"admin", "editor"})
    return {
        "items": [
            {
                "id": row.id,
                "action": row.action,
                "created_at": row.created_at,
                "reason": row.reason if privileged else None,
                "actor_id": row.actor_id if privileged else None,
            }
            for row in session.scalars(
                select(NumberOperation)
                .where(NumberOperation.entry_id == entry.id)
                .order_by(NumberOperation.created_at, NumberOperation.id)
                .limit(200)
            )
        ]
    }


@router.get("/number-series/{series_id}/export")
def export(
    series_id: UUID,
    request: Request,
    format: str = "csv",
    period_key: str | None = Query(None, max_length=32),
    state: str | None = None,
    mine: bool = False,
    search: str | None = Query(None, max_length=255),
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    if format not in {"csv", "xlsx"}:
        fail(422, "number_export_format", "Формат должен быть CSV или XLSX")
    series = service.get_series(session, series_id)
    rows = []
    for entry in service.visible_entries(
        session, actor, series, **filters(period_key, state, mine, search)
    ):
        if len(rows) >= 10000:
            fail(422, "number_export_limit", "Уточните фильтр: предел 10000 строк")
        row = service.output(session, actor, entry)
        rows.append(
            [
                row[key]
                for key in (
                    "period_key",
                    "ordinal",
                    "number",
                    "state",
                    "expires_at",
                    "document_title",
                )
            ]
        )
    content = export_file(
        format,
        ["Период", "Порядковое число", "Номер", "Состояние", "Срок резерва", "Документ"],
        rows,
    )
    emit(session, request, actor, "number.exported", "number_series", series.id)
    return Response(
        content,
        media_type="text/csv"
        if format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="numbers.{format}"'},
    )
