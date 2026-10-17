from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from app.api.dependencies import current_actor, db_session
from app.api.errors import fail
from app.execution import service
from app.execution.contracts import (
    AssignInput,
    ChecklistInput,
    DeadlineInput,
    ListInput,
    ReasonInput,
    ResultInput,
    StartInput,
)
from app.execution.models import (
    ChecklistItem,
    ExecutionEvent,
    ProcessInstance,
    StepResult,
)
from app.identity.models import Role, User, UserRole
from app.identity.policy import Actor as IdentityActor
from app.knowledge import service as knowledge
from app.processes import service as processes
from app.processes.contracts import RevisionInput
from app.registry.archives import configuration_active_clause
from app.registry.events import emit
from app.registry.models import Registry, RegistryAccess
from app.registry.policy import role_actions

router = APIRouter(prefix="/execution")
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]
Mode = Literal["editor", "viewer"]


def record(session, request, actor, instance, attempt, action, data=None):
    session.add(
        ExecutionEvent(
            instance_id=instance.id,
            step_execution_id=attempt.id if attempt else None,
            action=action,
            actor_id=actor.id,
            data=data or {},
        )
    )
    emit(
        session,
        request,
        actor,
        "execution." + action,
        "process_instance",
        instance.id,
        {
            "registry_id": str(instance.registry_id),
            "process_version_id": str(instance.process_version_id),
            **({"step_execution_id": str(attempt.id)} if attempt else {}),
        },
    )


def finish(session, request, actor, instance, attempt, action, data=None):
    instance.revision += 1
    record(session, request, actor, instance, attempt, action, data)
    session.flush()
    return service.instance_output(session, actor, instance)


@router.get("/assignees")
def assignees(registry_id: UUID, session: Db, actor: Actor):
    service.scope(session, actor, registry_id)
    users = session.scalars(
        select(User)
        .join(RegistryAccess, RegistryAccess.user_id == User.id)
        .where(
            User.active.is_(True),
            RegistryAccess.registry_id == registry_id,
            RegistryAccess.permissions.contains(["read", "update"]),
        )
        .order_by(User.display_name, User.id)
    )
    result = []
    for user in users:
        roles = frozenset(
            session.scalars(
                select(Role.code)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user.id)
            )
        )
        if "update" in role_actions(IdentityActor(user.id, user.display_name, roles)):
            result.append({"id": user.id, "display_name": user.display_name})
    return result


@router.post("/instances", status_code=201)
def start(body: StartInput, request: Request, session: Db, actor: Actor):
    process, version = processes.get_version(session, actor, body.process_version_id)
    service.scope(session, actor, process.registry_id, "create")
    if version.state != "published" or version.effective_from > service.now().date():
        service.invalid("Для запуска требуется действующая опубликованная версия процесса")
    responsible = body.assignee_id or actor.id
    service.assignee(session, process.registry_id, responsible)
    instance = ProcessInstance(
        registry_id=process.registry_id,
        process_version_id=version.id,
        name=body.name,
        snapshot=service.build_snapshot(session, actor, process, version),
        created_by=actor.id,
        assignee_id=responsible,
        deadline_at=service.deadline(body.deadline_at, body.deadline_reason),
    )
    session.add(instance)
    session.flush()
    attempt = service.make_attempt(
        session,
        instance,
        next(step["key"] for step in instance.snapshot["steps"] if step["is_start"]),
    )
    record(session, request, actor, instance, attempt, "started", {"reason": body.deadline_reason})
    session.flush()
    return service.instance_output(session, actor, instance)


@router.get("/instances")
def listing(session: Db, actor: Actor, params: Annotated[ListInput, Depends()]):
    if "read" not in role_actions(actor):
        return {"items": [], "total": 0, "page": params.page, "page_size": params.page_size}
    query = (
        select(ProcessInstance)
        .join(RegistryAccess, RegistryAccess.registry_id == ProcessInstance.registry_id)
        .where(RegistryAccess.user_id == actor.id, RegistryAccess.permissions.contains(["read"]),
               configuration_active_clause(Registry, "registry", ProcessInstance.registry_id))
    )
    if params.registry_id:
        query = query.where(ProcessInstance.registry_id == params.registry_id)
    if actor.scoped_registry_ids is not None:
        query = query.where(ProcessInstance.registry_id.in_(actor.scoped_registry_ids))
    if params.state:
        query = query.where(ProcessInstance.state == params.state)
    items = []
    for index, instance in enumerate(
        session.scalars(query.order_by(ProcessInstance.created_at.desc(), ProcessInstance.id))
    ):
        if index >= 10000:
            service.invalid("Уточните реестр или состояние исполнения")
        item = service.instance_output(session, actor, instance, params.mode, summary=True)
        if params.mine and not (
            instance.created_by == actor.id
            or not item["trajectory_restricted"]
            and instance.assignee_id == actor.id
        ):
            continue
        if params.overdue is not None and item["overdue"] != params.overdue:
            continue
        items.append(item)
    offset = (params.page - 1) * params.page_size
    return {
        "items": items[offset : offset + params.page_size],
        "total": len(items),
        "page": params.page,
        "page_size": params.page_size,
    }


@router.get("/instances/{instance_id}")
def detail(instance_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    return service.instance_output(
        session, actor, service.get_instance(session, actor, instance_id), mode
    )


@router.post("/instances/{instance_id}/open")
def open_step(instance_id: UUID, body: RevisionInput, request: Request, session: Db, actor: Actor):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision, work=True)
    if attempt.state != "pending":
        fail(409, "execution_step_already_open", "Шаг уже открыт")
    attempt.state, attempt.started_at, instance.state = "active", service.now(), "active"
    definition = service.step_definition(instance, attempt.step_key)
    if definition.get("duration_hours") is not None:
        attempt.deadline_at = attempt.started_at + timedelta(hours=definition["duration_hours"])
    return finish(session, request, actor, instance, attempt, "opened")


@router.post("/instances/{instance_id}/assign")
def assign(instance_id: UUID, body: AssignInput, request: Request, session: Db, actor: Actor):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision)
    service.assignee(session, instance.registry_id, body.assignee_id)
    instance.assignee_id, attempt.assignee_id = body.assignee_id, body.assignee_id
    return finish(session, request, actor, instance, attempt, "assigned")


@router.post("/instances/{instance_id}/checklist")
def checklist(instance_id: UUID, body: ChecklistInput, request: Request, session: Db, actor: Actor):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision, work=True)
    service.require_active(attempt)
    items = {
        item.id: item
        for item in session.scalars(
            select(ChecklistItem).where(ChecklistItem.step_execution_id == attempt.id)
        )
    }
    if len({item.id for item in body.items}) != len(body.items) or any(
        item.id not in items for item in body.items
    ):
        service.invalid("Пункт не принадлежит текущему чеклисту")
    for supplied in body.items:
        items[supplied.id].checked = supplied.checked
    return finish(
        session,
        request,
        actor,
        instance,
        attempt,
        "checklist.saved",
        {"checks": [{"id": str(item.id), "checked": item.checked} for item in body.items]},
    )


@router.post("/instances/{instance_id}/result")
def save_result(instance_id: UUID, body: ResultInput, request: Request, session: Db, actor: Actor):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision, work=True)
    service.require_active(attempt)
    values, provenance = service.confirmed_values(session, actor, instance, attempt, body)
    attachments = [
        service.freeze_ref(session, actor, item.model_dump(mode="json", exclude_none=True))
        for item in body.attachments
    ]
    result = StepResult(
        step_execution_id=attempt.id,
        values=values,
        comment=body.comment,
        attachments=attachments,
        result_document=provenance,
        actor_id=actor.id,
    )
    session.add(result)
    session.flush()
    attempt.result_id = result.id
    return finish(
        session, request, actor, instance, attempt, "result.saved", {"result_id": str(result.id)}
    )


@router.post("/instances/{instance_id}/advance")
def advance(instance_id: UUID, body: RevisionInput, request: Request, session: Db, actor: Actor):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision, work=True)
    service.require_active(attempt)
    if attempt.result_id is None:
        fail(422, "execution_result_required", "Сначала подтвердите результат шага")
    if session.scalar(
        select(ChecklistItem.id)
        .where(
            ChecklistItem.step_execution_id == attempt.id,
            ChecklistItem.required.is_(True),
            ChecklistItem.checked.is_(False),
        )
        .limit(1)
    ):
        fail(422, "execution_checklist_required", "Выполните обязательные пункты чеклиста")
    result = session.get(StepResult, attempt.result_id)
    selected = service.route(session, actor, instance, attempt, result)
    attempt.state, attempt.completed_at = "completed", service.now()
    instance.path = [*instance.path, str(attempt.id)]
    session.flush()
    if selected:
        service.make_attempt(session, instance, selected["to_step"])
    else:
        instance.state, instance.completed_at = "completed", service.now()
    return finish(
        session,
        request,
        actor,
        instance,
        attempt,
        "advanced",
        {"transition_key": selected["key"] if selected else None},
    )


@router.post("/instances/{instance_id}/suspend")
def suspend(instance_id: UUID, body: ReasonInput, request: Request, session: Db, actor: Actor):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision)
    service.check_reason(body.reason)
    if instance.state == "suspended":
        fail(409, "execution_already_suspended", "Исполнение уже приостановлено")
    instance.state = "suspended"
    return finish(session, request, actor, instance, attempt, "suspended", {"reason": body.reason})


@router.post("/instances/{instance_id}/resume")
def resume(instance_id: UUID, body: ReasonInput, request: Request, session: Db, actor: Actor):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision)
    service.check_reason(body.reason)
    if instance.state != "suspended":
        fail(409, "execution_not_suspended", "Исполнение не приостановлено")
    instance.state = "active" if attempt.started_at else "pending"
    return finish(session, request, actor, instance, attempt, "resumed", {"reason": body.reason})


@router.post("/instances/{instance_id}/cancel")
def cancel(instance_id: UUID, body: ReasonInput, request: Request, session: Db, actor: Actor):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision)
    service.check_reason(body.reason)
    instance.state, instance.completed_at = "cancelled", service.now()
    return finish(session, request, actor, instance, attempt, "cancelled", {"reason": body.reason})


@router.post("/instances/{instance_id}/return")
def return_step(instance_id: UUID, body: ReasonInput, request: Request, session: Db, actor: Actor):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision)
    service.check_reason(body.reason)
    if instance.state != "active" or not instance.path:
        fail(
            409,
            "execution_return_unavailable",
            "Возврат доступен только на предыдущий завершённый шаг",
        )
    previous = service.get_attempt(session, instance, service.uuid(instance.path[-1]))
    attempt.state = "returned"
    instance.path = instance.path[:-1]
    service.make_attempt(session, instance, previous.step_key)
    return finish(session, request, actor, instance, attempt, "returned", {"reason": body.reason})


@router.post("/instances/{instance_id}/deadline")
def change_deadline(
    instance_id: UUID, body: DeadlineInput, request: Request, session: Db, actor: Actor
):
    instance, attempt = service.mutation(session, actor, instance_id, body.revision)
    service.check_reason(body.reason)
    instance.deadline_at = service.deadline(body.deadline_at, body.reason)
    return finish(
        session, request, actor, instance, attempt, "deadline.changed", {"reason": body.reason}
    )


@router.get("/instances/{instance_id}/history")
def history(
    instance_id: UUID,
    session: Db,
    actor: Actor,
    page: int = 1,
    page_size: int = 20,
    mode: Mode | None = None,
):
    instance = service.get_instance(session, actor, instance_id)
    if not 1 <= page <= 10000 or not 1 <= page_size <= 100:
        service.invalid("Некорректная страница истории")
    if service.trajectory_restricted(session, actor, instance, mode):
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    query = select(ExecutionEvent).where(ExecutionEvent.instance_id == instance.id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    items = []
    for event in session.scalars(
        query.order_by(ExecutionEvent.created_at, ExecutionEvent.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ):
        attempt = (
            service.get_attempt(session, instance, event.step_execution_id)
            if event.step_execution_id
            else None
        )
        items.append(
            {
                "id": event.id,
                "action": event.action,
                "actor_name": session.get(User, event.actor_id).display_name,
                "created_at": event.created_at,
                "step_key": attempt.step_key if attempt else None,
                **event.data,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/instances/{instance_id}/steps/{execution_id}/{kind}")
def bases(
    instance_id: UUID,
    execution_id: UUID,
    kind: Literal["basis", "templates", "results"],
    session: Db,
    actor: Actor,
    mode: Mode | None = None,
):
    instance = service.get_instance(session, actor, instance_id)
    if service.trajectory_restricted(session, actor, instance, mode):
        return {"items": [{"restricted": True, "label": "Маршрут ограничен"}]}
    attempt = service.get_attempt(session, instance, execution_id)
    definition = service.step_definition(instance, attempt.step_key)
    if kind == "results":
        result = session.get(StepResult, attempt.result_id) if attempt.result_id else None
        sources = list(result.attachments) if result else []
        if result and result.result_document:
            sources.append(
                {
                    "document_id": result.result_document["document_id"],
                    "version_id": result.result_document.get("version_id"),
                }
            )
    else:
        sources = definition["sources" if kind == "basis" else "templates"]
    items = [processes.safe_basis(session, actor, item, mode) for item in sources]
    if kind == "templates":
        for identity in definition.get("template_versions", []):
            try:
                _, template = knowledge.get_version(session, actor, service.uuid(identity))
                result, _ = knowledge.original_basis(
                    session, actor, template.data["original"], mode
                )
                items.append(
                    {
                        **result,
                        "template": {
                            "template_version_id": template.id,
                            "name": template.data["name"],
                            "version": template.version,
                        },
                    }
                )
            except Exception as error:
                from fastapi import HTTPException

                if not isinstance(error, HTTPException):
                    raise
                items.append({"restricted": True, "label": "Шаблон ограничен"})
    return {"items": items}
