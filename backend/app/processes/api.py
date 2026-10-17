from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, false, func, or_, select

from app.api.dependencies import current_actor, db_session
from app.api.errors import fail
from app.processes import service
from app.processes.contracts import (
    DraftInput,
    PreviewInput,
    ProcessInput,
    ProcessPatch,
    RevisionInput,
)
from app.processes.models import Process, ProcessVersion
from app.registry.archives import configuration_active_clause
from app.registry.events import emit
from app.registry.models import Registry, RegistryAccess
from app.service_api.contracts import Page, ProcessVersionRead

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]
Mode = Literal["editor", "viewer"]


class CatalogItem(ProcessInput):
    id: UUID
    created_at: datetime


class CatalogVersion(ProcessVersionRead):
    effective_from: date


def event(session, request, actor, action, process, version=None):
    emit(
        session,
        request,
        actor,
        "process." + action,
        "process",
        process.id,
        {
            "registry_id": str(process.registry_id),
            **({"process_version_id": str(version.id)} if version else {}),
        },
    )


@router.get("/processes")
def listing(session: Db, actor: Actor, registry_id: UUID | None = None):
    query = (
        select(Process)
        .join(RegistryAccess, RegistryAccess.registry_id == Process.registry_id)
        .where(RegistryAccess.user_id == actor.id, RegistryAccess.permissions.contains(["read"]),
               configuration_active_clause(Registry, "registry", Process.registry_id))
    )
    if registry_id:
        query = query.where(Process.registry_id == registry_id)
    if actor.scoped_registry_ids is not None:
        query = query.where(Process.registry_id.in_(actor.scoped_registry_ids))
    from app.registry.policy import role_actions

    if "read" not in role_actions(actor):
        return []
    result = []
    for row in session.scalars(query.order_by(Process.created_at, Process.id).limit(1000)):
        if service.can_configure(session, actor, row):
            result.append(service.process_output(row))
            continue
        current = session.scalar(
            select(ProcessVersion)
            .where(
                ProcessVersion.process_id == row.id,
                ProcessVersion.state == "published",
                ProcessVersion.effective_from <= date.today(),
            )
            .order_by(ProcessVersion.effective_from.desc(), ProcessVersion.version.desc())
            .limit(1)
        )
        if current:
            result.append({**service.process_output(row), **current.metadata_snapshot})
    return result


@router.get("/processes/catalog", response_model=Page[CatalogItem])
def catalog(
    session: Db,
    actor: Actor,
    registry_id: UUID | None = None,
    q: str = Query("", max_length=300),
    mode: Mode | None = None,
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(20, ge=1, le=100),
):
    from app.registry.policy import role_actions

    if "read" not in role_actions(actor):
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    current = (
        select(ProcessVersion.id)
        .where(
            ProcessVersion.process_id == Process.id,
            ProcessVersion.state == "published",
            ProcessVersion.effective_from <= date.today(),
        )
        .order_by(ProcessVersion.effective_from.desc(), ProcessVersion.version.desc())
        .limit(1)
        .correlate(Process)
        .scalar_subquery()
    )
    configured = (
        RegistryAccess.permissions.contains(["update"]) if "admin" in actor.roles else false()
    )
    query = (
        select(Process, ProcessVersion)
        .join(RegistryAccess, RegistryAccess.registry_id == Process.registry_id)
        .outerjoin(ProcessVersion, ProcessVersion.id == current)
        .where(
            RegistryAccess.user_id == actor.id,
            RegistryAccess.permissions.contains(["read"]),
            configuration_active_clause(Registry, "registry", Process.registry_id),
            or_(configured, ProcessVersion.id.is_not(None)),
        )
    )
    if registry_id:
        query = query.where(Process.registry_id == registry_id)
    if actor.scoped_registry_ids is not None:
        query = query.where(Process.registry_id.in_(actor.scoped_registry_ids))
    if q.strip():
        pattern = (
            "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        visible_name = case(
            (configured, Process.metadata_data["name"].astext),
            else_=ProcessVersion.metadata_snapshot["name"].astext,
        )
        visible_code = case(
            (configured, Process.code), else_=ProcessVersion.metadata_snapshot["code"].astext
        )
        query = query.where(
            or_(visible_name.ilike(pattern, escape="\\"), visible_code.ilike(pattern, escape="\\"))
        )
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    items = []
    for process, version in session.execute(
        query.order_by(Process.created_at, Process.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ):
        item = service.process_output(process)
        if not service.can_configure(session, actor, process):
            item.update(version.metadata_snapshot)
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/processes", status_code=201)
def create(data: ProcessInput, request: Request, session: Db, actor: Actor):
    return create_process(data, request, session, actor)


def create_process(data, request, session, actor, original=None):
    service.scope(session, actor, data.registry_id, configure=True)
    session.scalar(select(Registry).where(Registry.id == data.registry_id).with_for_update())
    if session.scalar(
        select(Process.id).where(Process.registry_id == data.registry_id, Process.code == data.code)
    ):
        fail(409, "process_code_conflict", "Код процесса уже используется")
    metadata = service.metadata(data)
    service.check_owner(session, metadata)
    row = Process(
        registry_id=data.registry_id, code=data.code, metadata_data=metadata, created_by=actor.id
    )
    session.add(row)
    session.flush()
    version = service.new_version(session, actor, row, original)
    event(session, request, actor, "created", row, version)
    return service.process_output(row)


@router.patch("/processes/{process_id}")
def update(process_id: UUID, data: ProcessPatch, request: Request, session: Db, actor: Actor):
    row = service.get_process(session, actor, process_id, configure=True, lock=True)
    values = data.model_dump(mode="json", exclude_unset=True)
    if any(value is None for key, value in values.items() if key != "owner_id"):
        service.invalid("Пустые реквизиты процесса недопустимы")
    service.check_owner(session, values)
    row.metadata_data = {**row.metadata_data, **values}
    event(session, request, actor, "updated", row)
    return service.process_output(row)


@router.post("/processes/{process_id}/copy", status_code=201)
def copy_process(process_id: UUID, data: ProcessInput, request: Request, session: Db, actor: Actor):
    original = service.get_process(session, actor, process_id, configure=True)
    version = session.scalar(
        select(ProcessVersion)
        .where(ProcessVersion.process_id == original.id)
        .order_by(ProcessVersion.version.desc())
        .limit(1)
        .with_for_update()
    )
    return create_process(data, request, session, actor, version)


@router.get("/processes/{process_id}/versions")
def versions(process_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    process = service.get_process(session, actor, process_id)
    from app.registry.policy import permissions_for

    configured = "admin" in actor.roles and "update" in permissions_for(
        session, actor, process.registry_id
    )
    query = select(ProcessVersion).where(ProcessVersion.process_id == process.id)
    if not configured:
        query = query.where(
            ProcessVersion.state == "published", ProcessVersion.effective_from <= date.today()
        )
    return [
        service.version_output(session, actor, process, row, mode)
        for row in session.scalars(query.order_by(ProcessVersion.version.desc()))
    ]


@router.post("/processes/{process_id}/versions", status_code=201)
def create_version(process_id: UUID, request: Request, session: Db, actor: Actor):
    process = service.get_process(session, actor, process_id, configure=True, lock=True)
    previous = session.scalar(
        select(ProcessVersion)
        .where(ProcessVersion.process_id == process.id)
        .order_by(ProcessVersion.version.desc())
        .limit(1)
        .with_for_update()
    )
    version = service.new_version(session, actor, process, previous)
    event(session, request, actor, "version.created", process, version)
    return service.version_output(session, actor, process, version)


@router.get("/processes/{process_id}/version-catalog", response_model=Page[CatalogVersion])
def version_catalog(
    process_id: UUID,
    session: Db,
    actor: Actor,
    mode: Mode | None = None,
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(20, ge=1, le=50),
):
    process = service.get_process(session, actor, process_id)
    query = select(ProcessVersion).where(ProcessVersion.process_id == process.id)
    if not service.can_configure(session, actor, process):
        query = query.where(
            ProcessVersion.state == "published", ProcessVersion.effective_from <= date.today()
        )
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(
        query.order_by(ProcessVersion.version.desc(), ProcessVersion.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [service.version_output(session, actor, process, row, mode) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/processes/{process_id}")
def detail(
    process_id: UUID,
    session: Db,
    actor: Actor,
    version_id: UUID | None = None,
    as_of: date | None = None,
    mode: Mode | None = None,
):
    process = service.get_process(session, actor, process_id)
    if version_id:
        owner, version = service.get_version(session, actor, version_id)
        if owner.id != process.id:
            service.invalid("Версия не относится к процессу")
    else:
        selected_date = as_of or date.today()
        if not service.can_configure(session, actor, process):
            selected_date = min(selected_date, date.today())
        version = session.scalar(
            select(ProcessVersion)
            .where(
                ProcessVersion.process_id == process.id,
                ProcessVersion.state == "published",
                ProcessVersion.effective_from <= selected_date,
            )
            .order_by(ProcessVersion.effective_from.desc(), ProcessVersion.version.desc())
            .limit(1)
        )
    result = service.process_output(process)
    if version and version.state == "published":
        result.update(version.metadata_snapshot)
    from app.service_api.admin import usage

    usage(session, process.registry_id, "process_open", 0)
    return {
        **result,
        "version": service.version_output(session, actor, process, version, mode)
        if version
        else None,
    }


@router.get("/process-versions/{version_id}")
def version_detail(version_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    process, version = service.get_version(session, actor, version_id)
    from app.service_api.admin import usage

    usage(session, process.registry_id, "process_open", 0)
    return service.version_output(session, actor, process, version, mode)


@router.put("/process-versions/{version_id}")
def save(version_id: UUID, data: DraftInput, request: Request, session: Db, actor: Actor):
    process, version = service.get_version(session, actor, version_id, configure=True, lock=True)
    service.editable(version, data.revision)
    steps = [step.model_dump(mode="json", exclude_none=True) for step in data.steps]
    transitions = [item.model_dump(mode="json", exclude_none=True) for item in data.transitions]
    if len({step["key"] for step in steps}) != len(steps) or len(
        {item["key"] for item in transitions}
    ) != len(transitions):
        service.invalid("Ключи шагов и переходов должны быть уникальны")
    version.effective_from, version.revision = data.effective_from, version.revision + 1
    service.save_graph(session, version, steps, transitions)
    event(session, request, actor, "version.updated", process, version)
    return service.version_output(session, actor, process, version)


@router.post("/process-versions/{version_id}/validate")
def validate(version_id: UUID, data: RevisionInput, session: Db, actor: Actor):
    process, version = service.get_version(session, actor, version_id, configure=True, lock=True)
    service.editable(version, data.revision)
    service.validate_graph(session, actor, process, version)
    return {"valid": True}


@router.post("/process-versions/{version_id}/publish")
def publish(version_id: UUID, data: RevisionInput, request: Request, session: Db, actor: Actor):
    process, version = service.get_version(session, actor, version_id, configure=True, lock=True)
    service.editable(version, data.revision)
    service.validate_graph(session, actor, process, version)
    version.metadata_snapshot = {
        "registry_id": str(process.registry_id),
        "code": process.code,
        **process.metadata_data,
    }
    version.state, version.published_at, version.revision = (
        "published",
        datetime.now(UTC),
        version.revision + 1,
    )
    session.flush()
    event(session, request, actor, "version.published", process, version)
    return service.version_output(session, actor, process, version)


@router.post("/process-versions/{version_id}/preview")
def preview(
    version_id: UUID, data: PreviewInput, session: Db, actor: Actor, mode: Mode | None = None
):
    process, version = service.get_version(session, actor, version_id)
    return service.preview(session, actor, process, version, data, mode)


@router.get("/process-versions/{version_id}/steps/{step_key}/{kind}")
def step_basis(
    version_id: UUID,
    step_key: str,
    kind: Literal["basis", "templates"],
    session: Db,
    actor: Actor,
    mode: Mode | None = None,
):
    _, version = service.get_version(session, actor, version_id)
    steps, _ = service.graph(session, version)
    step = next((step for step in steps if step["key"] == step_key), None)
    if step is None:
        fail(404, "process_step_not_found", "Шаг не найден")
    result = {
        "items": [
            service.safe_basis(session, actor, item, mode)
            for item in step["sources" if kind == "basis" else "templates"]
        ]
    }
    if kind == "templates":
        from fastapi import HTTPException

        from app.knowledge.service import get_version as template_version
        from app.knowledge.service import original_basis

        for identity in step.get("template_versions", []):
            try:
                _, pinned = template_version(session, actor, UUID(str(identity)))
                if pinned.state != "published":
                    raise HTTPException(404)
                resolved, _ = original_basis(session, actor, pinned.data["original"], mode)
                result["items"].append(
                    {
                        **resolved,
                        "template": {
                            "name": pinned.data["name"],
                            "version": pinned.version,
                            "template_version_id": pinned.id,
                        },
                    }
                )
            except HTTPException:
                result["items"].append({"restricted": True, "label": "Шаблон ограничен"})
    return result
