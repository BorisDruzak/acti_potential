from copy import deepcopy
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, func, select

from app.api.dependencies import current_actor, db_session
from app.api.errors import fail
from app.identity.models import User
from app.knowledge import service
from app.knowledge.contracts import (
    AccessInput,
    CaseInput,
    CaseUpdate,
    CurationInput,
    SearchInput,
    TemplateInput,
    UsageInput,
    VersionInput,
)
from app.knowledge.models import (
    KnowledgeCuration,
    KnowledgeUsage,
    PracticeCase,
    PracticeLink,
    Template,
    TemplateVersion,
)
from app.processes import service as processes
from app.processes.contracts import RevisionInput
from app.registry.events import emit
from app.registry.file_api import download_file

router = APIRouter(prefix="/knowledge")
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]
Mode = Literal["editor", "viewer"]


def event(session, request, actor, action, obj, version_id=None):
    emit(
        session,
        request,
        actor,
        "knowledge." + action,
        "knowledge",
        obj.id,
        {"registry_id": str(obj.registry_id), **({"template_version_id": str(version_id)} if version_id else {})},
    )


def data_for(body):
    return body.model_dump(
        mode="json", exclude={"registry_id", "revision", "links", "visibility", "owner_id"}
    )


def draft(obj, revision):
    if obj.state != "draft":
        fail(409, "knowledge_immutable", "Опубликованное содержание неизменяемо")
    if obj.revision != revision:
        fail(409, "revision_conflict", "Черновик изменён другим пользователем")


@router.get("/templates")
def templates(session: Db, actor: Actor, params: Annotated[SearchInput, Depends()]):
    return service.search(session, actor, "templates", params)


@router.get("/cases")
def cases(session: Db, actor: Actor, params: Annotated[SearchInput, Depends()]):
    return service.search(session, actor, "cases", params)


@router.post("/templates", status_code=201)
def create_template(body: TemplateInput, request: Request, session: Db, actor: Actor):
    service.scope(session, actor, body.registry_id, "update")
    service.validate_metadata(session, actor, body.registry_id, body.model_dump(mode="json"))
    obj = Template(
        registry_id=body.registry_id,
        owner_id=body.owner_id or actor.id,
        created_by=actor.id,
        visibility=body.visibility,
    )
    session.add(obj)
    session.flush()
    data = data_for(body)
    version = TemplateVersion(template_id=obj.id, version=1, data=data, text=service.text_for(data))
    session.add(version)
    session.flush()
    event(session, request, actor, "template.created", obj)
    return service.template_output(session, actor, obj, version=version)


@router.get("/templates/{template_id}")
def template_detail(template_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    obj = service.get_object(session, actor, Template, template_id)
    return service.template_output(session, actor, obj, mode)


@router.get("/templates/{template_id}/versions")
def template_versions(template_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    obj = service.get_object(session, actor, Template, template_id)
    query = select(TemplateVersion).where(TemplateVersion.template_id == obj.id)
    if not service.rights(session, actor, obj)["edit"]:
        query = query.where(TemplateVersion.state == "published")
    return [
        service.version_output(session, actor, obj, version, mode)
        for version in session.scalars(query.order_by(TemplateVersion.version.desc()))
    ]


@router.post("/templates/{template_id}/versions", status_code=201)
def new_template_version(template_id: UUID, request: Request, session: Db, actor: Actor):
    obj = service.get_object(session, actor, Template, template_id, write=True, lock=True)
    previous = session.scalar(
        select(TemplateVersion)
        .where(TemplateVersion.template_id == obj.id)
        .order_by(TemplateVersion.version.desc())
        .limit(1)
        .with_for_update()
    )
    version = TemplateVersion(
        template_id=obj.id,
        version=previous.version + 1,
        data=deepcopy(previous.data),
        text=previous.text,
    )
    session.add(version)
    session.flush()
    event(session, request, actor, "template.version.created", obj)
    return service.version_output(session, actor, obj, version)


@router.get("/template-versions/{version_id}")
def version_detail(version_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    obj, version = service.get_version(session, actor, version_id)
    return service.version_output(session, actor, obj, version, mode)


@router.put("/template-versions/{version_id}")
def update_template_version(
    version_id: UUID, body: VersionInput, request: Request, session: Db, actor: Actor
):
    obj, version = service.get_version(session, actor, version_id, write=True, lock=True)
    draft(version, body.revision)
    service.validate_metadata(session, actor, obj.registry_id, body.model_dump(mode="json"))
    version.data = service.preserve_hidden(session, actor, version.data, data_for(body))
    version.text = service.text_for(version.data)
    version.revision += 1
    event(session, request, actor, "template.version.updated", obj)
    return service.version_output(session, actor, obj, version)


@router.post("/template-versions/{version_id}/publish")
def publish_template(
    version_id: UUID, body: RevisionInput, request: Request, session: Db, actor: Actor
):
    obj, version = service.get_version(session, actor, version_id, write=True, lock=True)
    draft(version, body.revision)
    service.validate_metadata(session, actor, obj.registry_id, version.data)
    service.original_basis(session, actor, version.data["original"])
    version.state, version.published_at, version.revision = (
        "published",
        datetime.now(UTC),
        version.revision + 1,
    )
    session.flush()
    event(session, request, actor, "template.version.published", obj, version.id)
    return service.version_output(session, actor, obj, version)


@router.get("/template-versions/{version_id}/download")
def template_download(
    version_id: UUID, request: Request, session: Db, actor: Actor, mode: Mode | None = None
):
    obj, version = service.get_version(session, actor, version_id)
    service.scope(session, actor, obj.registry_id, "download")
    _, file = service.original_basis(session, actor, version.data["original"], mode)
    result = download_file(file.id, request, session, actor, mode)
    service.record_usage(session, obj.registry_id, obj.id, "template_download")
    return result


@router.get("/template-versions/{version_id}/basis")
def template_basis(version_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    obj, version = service.get_version(session, actor, version_id)
    try:
        result, _ = service.original_basis(session, actor, version.data["original"], mode)
    except Exception as error:
        from fastapi import HTTPException

        if not isinstance(error, HTTPException) or error.status_code not in {403, 404, 422}:
            raise
        return {"restricted": True, "label": "Оригинал ограничен"}
    service.record_usage(session, obj.registry_id, obj.id, "basis_follow")
    return result


def save_links(session, obj, links):
    session.execute(delete(PracticeLink).where(PracticeLink.case_id == obj.id))
    for ordinal, link in enumerate(links):
        session.add(
            PracticeLink(
                case_id=obj.id,
                ordinal=ordinal,
                data=link.model_dump(mode="json", exclude_none=True),
            )
        )
    session.flush()


@router.post("/cases", status_code=201)
def create_case(body: CaseInput, request: Request, session: Db, actor: Actor):
    service.scope(session, actor, body.registry_id, "update")
    service.validate_metadata(session, actor, body.registry_id, body.model_dump(mode="json"))
    data = data_for(body)
    obj = PracticeCase(
        registry_id=body.registry_id,
        owner_id=body.owner_id or actor.id,
        created_by=actor.id,
        visibility=body.visibility,
        data=data,
        text=service.text_for(data),
    )
    session.add(obj)
    session.flush()
    save_links(session, obj, body.links)
    event(session, request, actor, "case.created", obj)
    return service.case_output(session, actor, obj)


@router.get("/cases/{case_id}")
def case_detail(case_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    return service.case_output(
        session, actor, service.get_object(session, actor, PracticeCase, case_id), mode
    )


@router.put("/cases/{case_id}")
def update_case(case_id: UUID, body: CaseUpdate, request: Request, session: Db, actor: Actor):
    obj = service.get_object(session, actor, PracticeCase, case_id, write=True, lock=True)
    draft(obj, body.revision)
    service.validate_metadata(session, actor, obj.registry_id, body.model_dump(mode="json"))
    obj.data, obj.revision = (
        service.preserve_hidden(session, actor, obj.data, data_for(body)),
        obj.revision + 1,
    )
    obj.text = service.text_for(obj.data)
    save_links(session, obj, body.links)
    event(session, request, actor, "case.updated", obj)
    return service.case_output(session, actor, obj)


@router.post("/cases/{case_id}/publish")
def publish_case(case_id: UUID, body: RevisionInput, request: Request, session: Db, actor: Actor):
    obj = service.get_object(session, actor, PracticeCase, case_id, write=True, lock=True)
    draft(obj, body.revision)
    service.validate_metadata(session, actor, obj.registry_id, obj.data)
    for link in service.case_links(session, obj):
        processes.basis(session, actor, link)
    obj.state, obj.published_at, obj.revision = "published", datetime.now(UTC), obj.revision + 1
    session.flush()
    event(session, request, actor, "case.published", obj)
    return service.case_output(session, actor, obj)


@router.post("/cases/{case_id}/curate")
def curate(case_id: UUID, body: CurationInput, request: Request, session: Db, actor: Actor):
    obj = service.get_object(session, actor, PracticeCase, case_id, write=True, lock=True)
    service.scope(session, actor, obj.registry_id, "curate_knowledge")
    if obj.state != "published":
        fail(409, "knowledge_not_published", "Сначала опубликуйте случай")
    confirmed, recommended = service.curation(session, obj)
    if body.action == "recommend" and not confirmed:
        fail(409, "knowledge_confirmation_required", "Сначала подтвердите случай")
    if (
        body.action == "confirm"
        and confirmed
        or body.action == "recommend"
        and recommended
        or body.action == "unrecommend"
        and not recommended
    ):
        fail(409, "knowledge_curation_conflict", "Состояние рекомендации уже изменено")
    if not body.reason.strip():
        service.invalid("Укажите причину")
    if body.action != "unrecommend":
        service.validate_metadata(session, actor, obj.registry_id, obj.data)
        for link in service.case_links(session, obj):
            processes.basis(session, actor, link)
    session.add(
        KnowledgeCuration(case_id=obj.id, action=body.action, reason=body.reason, actor_id=actor.id)
    )
    session.flush()
    event(session, request, actor, "case.curated", obj)
    return service.case_output(session, actor, obj)


@router.get("/cases/{case_id}/history")
def history(case_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    obj = service.get_object(session, actor, PracticeCase, case_id)
    return {
        "items": [
            {
                "id": row.id,
                "action": row.action,
                "reason": row.reason,
                "actor_name": session.get(User, row.actor_id).display_name,
                "created_at": row.created_at,
            }
            for row in session.scalars(
                select(KnowledgeCuration)
                .where(KnowledgeCuration.case_id == obj.id)
                .order_by(KnowledgeCuration.created_at, KnowledgeCuration.id)
            )
        ]
    }


@router.get("/cases/{case_id}/basis")
def case_basis(case_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    obj = service.get_object(session, actor, PracticeCase, case_id)
    items = [
        processes.safe_basis(session, actor, item, mode)
        for item in service.case_links(session, obj)
    ]
    if any(not item["restricted"] for item in items):
        service.record_usage(session, obj.registry_id, obj.id, "basis_follow")
    return {"items": items}


@router.patch("/{kind}/{identity}/access")
def access(
    kind: Literal["templates", "cases"],
    identity: UUID,
    body: AccessInput,
    request: Request,
    session: Db,
    actor: Actor,
):
    model = Template if kind == "templates" else PracticeCase
    obj = service.get_object(session, actor, model, identity, write=True, lock=True)
    if not service.rights(session, actor, obj)["access"]:
        fail(403, "knowledge_forbidden", "Изменение доступа не разрешено")
    if not body.reason.strip():
        service.invalid("Укажите причину")
    obj.visibility, obj.lifecycle = body.visibility, body.lifecycle
    session.add(
        KnowledgeCuration(
            template_id=obj.id if model is Template else None,
            case_id=obj.id if model is PracticeCase else None,
            action="access_changed",
            reason=body.reason,
            actor_id=actor.id,
        )
    )
    session.flush()
    event(session, request, actor, "access.changed", obj)
    return (
        service.template_output(session, actor, obj)
        if model is Template
        else service.case_output(session, actor, obj)
    )


@router.get("/related")
def related(
    session: Db,
    actor: Actor,
    document_id: UUID | None = None,
    process_version_id: UUID | None = None,
    step_key: str | None = None,
    mode: Mode | None = None,
):
    return service.related(session, actor, document_id, process_version_id, step_key, mode)


@router.post("/usage", status_code=204)
def usage(body: UsageInput, session: Db, actor: Actor):
    if bool(body.template_version_id) == bool(body.case_id):
        service.invalid("Укажите один материал")
    if body.template_version_id:
        obj, version = service.get_version(session, actor, body.template_version_id)
        if body.action == "case_open":
            service.invalid()
        if body.action == "basis_follow":
            service.original_basis(session, actor, version.data["original"])
    else:
        obj = service.get_object(session, actor, PracticeCase, body.case_id)
        if body.action == "template_open":
            service.invalid()
        if body.action == "basis_follow":
            if not any(
                not processes.safe_basis(session, actor, item)["restricted"]
                for item in service.case_links(session, obj)
            ):
                service.denied()
    service.record_usage(session, obj.registry_id, obj.id, body.action)
    return Response(status_code=204)


@router.get("/metrics")
def metrics(registry_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    service.scope(session, actor, registry_id, "curate_knowledge")
    allowed = [
        obj.id
        for model in (Template, PracticeCase)
        for obj in service.visible_objects(session, actor, model, registry_id)
    ]
    return {
        "items": [
            {"action": action, "count": count}
            for action, count in session.execute(
                select(KnowledgeUsage.action, func.sum(KnowledgeUsage.count))
                .where(
                    KnowledgeUsage.registry_id == registry_id, KnowledgeUsage.object_id.in_(allowed)
                )
                .group_by(KnowledgeUsage.action)
                .order_by(KnowledgeUsage.action)
            )
        ]
    }
