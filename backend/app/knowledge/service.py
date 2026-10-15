"""Independent, versioned knowledge with live object and source authorization."""

import json
from copy import deepcopy
from datetime import date
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import fail
from app.content.models import ContentExtraction, DocumentBlock
from app.content.search import context_snippet
from app.identity.models import User
from app.knowledge.models import (
    KnowledgeCuration,
    KnowledgeUsage,
    PracticeCase,
    PracticeLink,
    Template,
    TemplateVersion,
)
from app.processes import service as processes
from app.processes.models import ProcessStep
from app.registry.documents import get_document, get_schema, raw_values
from app.registry.models import DocumentFile, FormSchema, RegistryAccess
from app.registry.policy import field_accessible, permissions_for, redact_values, role_actions
from app.registry.value_validation import validate_value


def uuid(value):
    return UUID(str(value))


def denied():
    fail(404, "knowledge_not_found", "Материал не найден")


def invalid(message="Некорректные данные материала"):
    fail(422, "knowledge_validation", message)


def scope(session, actor, registry_id, action="read"):
    permissions = permissions_for(session, actor, registry_id)
    if "read" not in permissions:
        denied()
    if action not in permissions:
        fail(403, "knowledge_forbidden", "Недостаточно прав на материал")


def visible_clause(actor, model):
    return and_(
        RegistryAccess.user_id == actor.id,
        RegistryAccess.permissions.contains(["read"]),
        or_(
            model.visibility == "registry", model.owner_id == actor.id, model.created_by == actor.id
        ),
    )


def rights(session, actor, obj):
    permissions = permissions_for(session, actor, obj.registry_id)
    editor = "update" in permissions
    return {
        "edit": editor,
        "publish": editor,
        "curate": editor and "curate_knowledge" in permissions,
        "access": editor
        and (actor.id in {obj.owner_id, obj.created_by} or "curate_knowledge" in permissions),
    }


def get_object(session, actor, model, identity, *, write=False, lock=False):
    query = (
        select(model)
        .join(RegistryAccess, RegistryAccess.registry_id == model.registry_id)
        .where(model.id == identity, visible_clause(actor, model))
    )
    if "read" not in role_actions(actor):
        denied()
    if lock:
        query = query.with_for_update(of=model)
    obj = session.scalar(query)
    if obj is None:
        denied()
    scope(session, actor, obj.registry_id, "update" if write else "read")
    if (
        isinstance(obj, PracticeCase)
        and obj.state == "draft"
        and not rights(session, actor, obj)["edit"]
    ):
        denied()
    return obj


def get_version(session, actor, identity, *, write=False, lock=False):
    query = select(TemplateVersion).where(TemplateVersion.id == identity)
    if lock:
        query = query.with_for_update()
    version = session.scalar(query)
    if version is None:
        denied()
    obj = get_object(session, actor, Template, version.template_id, write=write)
    if version.state == "draft" and not rights(session, actor, obj)["edit"]:
        denied()
    return obj, version


def latest(session, actor, obj):
    query = select(TemplateVersion).where(TemplateVersion.template_id == obj.id)
    if not rights(session, actor, obj)["edit"]:
        query = query.where(TemplateVersion.state == "published")
    return session.scalar(query.order_by(TemplateVersion.version.desc()).limit(1))


def current_published(session, obj):
    return session.scalar(
        select(TemplateVersion)
        .where(TemplateVersion.template_id == obj.id, TemplateVersion.state == "published")
        .order_by(TemplateVersion.version.desc())
        .limit(1)
    )


def validate_metadata(session, actor, registry_id, data):
    owner = data.get("owner_id")
    if owner and session.get(User, uuid(owner)) is None:
        invalid("Владелец не найден")
    from app.registry.models import Registry

    registry = session.get(Registry, registry_id)
    dtype, form_id = data.get("document_type_id"), data.get("form_schema_id")
    if dtype and str(dtype) not in registry.document_type_ids:
        invalid("Тип документа недоступен в реестре")
    if form_id:
        form = session.get(FormSchema, uuid(form_id))
        if (
            form is None
            or form.state != "published"
            or str(form.document_type_id) not in registry.document_type_ids
            or dtype
            and str(form.document_type_id) != str(dtype)
        ):
            invalid("Форма недоступна или несовместима")
        fields = processes.condition_fields(form)
        characteristics = data.get("characteristics", {})
        values = {
            key: items
            if fields.get(key, {}).get("type") == "multi_reference"
            else (items[0] if items else None)
            for key, items in characteristics.items()
        }
        for key, items in characteristics.items():
            field = fields.get(key)
            if (
                field is None
                or not field.get("filterable", False)
                or field.get("type") in {"file", "group"}
            ):
                invalid("Характеристика должна быть разрешённым фильтруемым полем формы")
            if not field_accessible(field, actor, fields, values=values):
                fail(403, "knowledge_characteristic_restricted", "Характеристика недоступна")
            for item in items:
                typed = (
                    {**field, "type": "reference"} if field["type"] == "multi_reference" else field
                )
                validate_value(session, actor, typed, item, registry_id)
    process_id, key = data.get("process_version_id"), data.get("step_key")
    if key and not process_id:
        invalid("Шаг требует точную опубликованную версию процесса")
    if process_id:
        _, version = processes.get_version(session, actor, uuid(process_id))
        if (
            version.state != "published"
            or key
            and session.scalar(
                select(ProcessStep.id).where(
                    ProcessStep.version_id == version.id, ProcessStep.key == key
                )
            )
            is None
        ):
            invalid("Версия процесса или шаг недоступны")


def original_basis(session, actor, original, mode=None):
    result = processes.basis(session, actor, {**original, "note": ""}, mode)
    if not any(str(file["id"]) == str(original["file_id"]) for file in result["files"]):
        denied()
    file = session.get(DocumentFile, uuid(original["file_id"]))
    if file.document_id != uuid(original["document_id"]) or file.version_id != uuid(
        original["version_id"]
    ):
        invalid("Оригинал не относится к указанной версии")
    return result, file


def safe_original(session, actor, original, mode=None):
    try:
        result, file = original_basis(session, actor, original, mode)
        return {
            **original,
            "restricted": False,
            "name": file.name,
            "version": result["anchor"]["version"],
        }
    except HTTPException as error:
        if error.status_code not in {403, 404, 422}:
            raise
        return {"restricted": True, "label": "Оригинал ограничен"}


def redacted_bindings(session, actor, data, mode=None):
    result = deepcopy(data)
    if result.get("process_version_id"):
        try:
            processes.get_version(session, actor, uuid(result["process_version_id"]))
        except HTTPException:
            result["process_version_id"], result["step_key"] = None, None
            result["process_restricted"] = True
    if result.get("form_schema_id"):
        form = session.get(FormSchema, uuid(result["form_schema_id"]))
        fields = processes.condition_fields(form) if form else {}
        characteristics = result.get("characteristics", {})
        values = {
            key: items
            if fields.get(key, {}).get("type") == "multi_reference"
            else (items[0] if items else None)
            for key, items in characteristics.items()
        }
        result["characteristics"] = {
            key: items
            for key, items in characteristics.items()
            if key in fields and field_accessible(fields[key], actor, fields, mode, values=values)
        }
        if len(result["characteristics"]) != len(characteristics):
            result["characteristics_restricted"] = True
    return result


def preserve_hidden(session, actor, previous, proposed):
    projected = redacted_bindings(session, actor, previous)
    if projected.get("process_restricted"):
        if proposed.get("process_version_id") not in {None, previous.get("process_version_id")}:
            fail(403, "knowledge_binding_restricted", "Связь с процессом недоступна")
        proposed["process_version_id"], proposed["step_key"] = (
            previous.get("process_version_id"),
            previous.get("step_key"),
        )
    if projected.get("characteristics_restricted"):
        if proposed.get("form_schema_id") != previous.get("form_schema_id"):
            fail(403, "knowledge_characteristic_restricted", "Характеристика недоступна")
        hidden = {
            key: value
            for key, value in previous.get("characteristics", {}).items()
            if key not in projected.get("characteristics", {})
        }
        proposed["characteristics"] = {**proposed.get("characteristics", {}), **hidden}
    return proposed


def version_output(session, actor, obj, version, mode=None):
    data = redacted_bindings(session, actor, version.data, mode)
    data["original"] = safe_original(session, actor, data["original"], mode)
    return {
        **data,
        "id": version.id,
        "template_id": obj.id,
        "registry_id": obj.registry_id,
        "version": version.version,
        "revision": version.revision,
        "state": version.state,
        "visibility": obj.visibility,
        "owner_id": obj.owner_id,
        "lifecycle": obj.lifecycle,
        "published_at": version.published_at,
        "permissions": rights(session, actor, obj),
    }


def template_output(session, actor, obj, mode=None, version=None):
    version = version or latest(session, actor, obj)
    if version is None:
        denied()
    data = version_output(session, actor, obj, version, mode)
    return {**data, "id": obj.id, "version": data}


def case_links(session, obj):
    return [
        row.data
        for row in session.scalars(
            select(PracticeLink)
            .where(PracticeLink.case_id == obj.id)
            .order_by(PracticeLink.ordinal)
        )
    ]


def curation(session, obj):
    events = list(
        session.scalars(
            select(KnowledgeCuration)
            .where(KnowledgeCuration.case_id == obj.id)
            .order_by(KnowledgeCuration.created_at, KnowledgeCuration.id)
        )
    )
    confirmed = any(event.action == "confirm" for event in events)
    recommendation = [
        event.action for event in events if event.action in {"recommend", "unrecommend"}
    ]
    return confirmed, bool(recommendation and recommendation[-1] == "recommend")


def case_output(session, actor, obj, mode=None):
    data = redacted_bindings(session, actor, obj.data, mode)
    links = []
    for item in case_links(session, obj):
        resolved = processes.safe_basis(session, actor, item, mode)
        links.append(resolved if resolved["restricted"] else {**item, "restricted": False})
    confirmed, recommended = curation(session, obj)
    return {
        **data,
        "id": obj.id,
        "registry_id": obj.registry_id,
        "owner_id": obj.owner_id,
        "visibility": obj.visibility,
        "lifecycle": obj.lifecycle,
        "state": obj.state,
        "revision": obj.revision,
        "links": links,
        "confirmed": confirmed,
        "recommended": recommended,
        "author_name": session.get(User, obj.created_by).display_name,
        "created_at": obj.created_at,
        "published_at": obj.published_at,
        "permissions": rights(session, actor, obj),
    }


def record_usage(session, registry_id, object_id, action):
    statement = insert(KnowledgeUsage).values(
        id=uuid4(),
        day=date.today(),
        registry_id=registry_id,
        object_id=object_id,
        action=action,
        count=1,
    )
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_knowledge_usage_bucket", set_={"count": KnowledgeUsage.count + 1}
        )
    )


def text_for(data):
    return "\n".join(
        str(data.get(key) or "") for key in ("name", "purpose", "category", "description", "result")
    )


def visible_objects(session, actor, model, registry_id=None):
    if "read" not in role_actions(actor):
        return []
    query = (
        select(model)
        .join(RegistryAccess, RegistryAccess.registry_id == model.registry_id)
        .where(visible_clause(actor, model))
    )
    if registry_id:
        query = query.where(model.registry_id == registry_id)
    rows = list(session.scalars(query.order_by(model.id).limit(10001)))
    if len(rows) > 10000:
        fail(422, "knowledge_narrow_search", "Уточните реестр поиска")
    return rows


def search_rank(session, model, identity, text):
    query = func.websearch_to_tsquery(literal_column("'russian'::regconfig"), text)
    lexical = model.search_vector.op("@@")(query)
    row = session.execute(
        select(
            model.text,
            func.greatest(
                func.ts_rank(model.search_vector, query),
                func.word_similarity(text, model.text) * 0.1,
            ),
        ).where(model.id == identity, or_(lexical, model.text.op("%>")(text)))
    ).first()
    return (float(row[1]), context_snippet(row[0], text)) if row else (0, None)


def file_rank(session, actor, data, text, mode=None):
    try:
        _, file = original_basis(session, actor, data["original"], mode)
    except HTTPException:
        return 0, None
    query = func.websearch_to_tsquery(literal_column("'russian'::regconfig"), text)
    rank = func.greatest(
        func.ts_rank(DocumentBlock.search_vector, query),
        func.word_similarity(text, DocumentBlock.text) * 0.1,
    )
    row = session.execute(
        select(DocumentBlock.text, rank)
        .join(ContentExtraction, ContentExtraction.id == DocumentBlock.extraction_id)
        .where(
            ContentExtraction.file_id == file.id,
            ContentExtraction.state == "completed",
            or_(DocumentBlock.search_vector.op("@@")(query), DocumentBlock.text.op("%>")(text)),
        )
        .order_by(rank.desc(), DocumentBlock.id)
        .limit(1)
    ).first()
    return (float(row[1]), context_snippet(row[0], text)) if row else (0, None)


def search(session, actor, kind, params):
    model = Template if kind == "templates" else PracticeCase
    session.execute(select(func.set_config("pg_trgm.word_similarity_threshold", "0.45", True)))
    matches = []
    for obj in visible_objects(session, actor, model, params.registry_id):
        if obj.lifecycle != "active":
            continue
        version = latest(session, actor, obj) if model is Template else None
        if (
            model is Template
            and version is None
            or model is PracticeCase
            and obj.state != "published"
            and not rights(session, actor, obj)["edit"]
        ):
            continue
        data = version.data if version else obj.data
        projected = redacted_bindings(session, actor, data, params.mode)
        if any(
            getattr(params, key) is not None
            and str(projected.get(key)) != str(getattr(params, key))
            for key in (
                "category",
                "document_type_id",
                "form_schema_id",
                "process_version_id",
                "step_key",
            )
        ):
            continue
        if (
            params.recommended is not None
            and (False if model is Template else curation(session, obj)[1]) != params.recommended
        ):
            continue
        score, snippet = 0, None
        if params.text.strip():
            score, snippet = search_rank(
                session,
                TemplateVersion if version else PracticeCase,
                version.id if version else obj.id,
                params.text,
            )
            if version and "download" in permissions_for(session, actor, obj.registry_id):
                file_score, file_snippet = file_rank(session, actor, data, params.text, params.mode)
                if file_score > score:
                    score, snippet = file_score, file_snippet
            if snippet is None:
                continue
        output = (
            template_output(session, actor, obj, params.mode, version)
            if version
            else case_output(session, actor, obj, params.mode)
        )
        output["snippet"] = snippet
        matches.append((score, str(obj.id), output))
    matches.sort(key=lambda item: (-item[0], item[1]))
    total = len(matches)
    start = (params.page - 1) * params.page_size
    if params.text.strip():
        counted = set()
        for _, _, item in matches:
            if item["registry_id"] not in counted:
                record_usage(session, item["registry_id"], item["id"], "search")
                counted.add(item["registry_id"])
    return {
        "items": [item[2] for item in matches[start : start + params.page_size]],
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
    }


def token(value):
    return json.dumps([type(value).__name__, value], ensure_ascii=False, sort_keys=True)


def related(session, actor, document_id=None, process_version_id=None, step_key=None, mode=None):
    dtype, category, values, form_id = None, None, {}, None
    if document_id:
        document = get_document(session, actor, document_id)
        schema = get_schema(session, document)
        values = redact_values(schema, raw_values(document, schema), actor, mode)
        dtype, category, form_id = (
            str(document.document_type_id),
            values.get("category"),
            str(document.schema_version_id),
        )
    elif process_version_id:
        process, version = processes.get_version(session, actor, process_version_id)
        if version.state != "published":
            invalid("Для подбора требуется опубликованный процесс")
        if (
            step_key
            and session.scalar(
                select(ProcessStep.id).where(
                    ProcessStep.version_id == version.id, ProcessStep.key == step_key
                )
            )
            is None
        ):
            invalid("Шаг не найден")
        category = version.metadata_snapshot.get("category")
    else:
        invalid("Укажите документ или точную версию процесса")
    result = {"templates": [], "cases": []}
    for key, model in (("templates", Template), ("cases", PracticeCase)):
        for obj in visible_objects(session, actor, model):
            if obj.lifecycle != "active":
                continue
            version = current_published(session, obj) if model is Template else None
            if (
                model is Template
                and version is None
                or model is PracticeCase
                and obj.state != "published"
            ):
                continue
            data = version.data if version else obj.data
            reasons = []
            if dtype and data.get("document_type_id") == dtype:
                reasons.append("Совпадает тип документа")
            if category and data.get("category") == category:
                reasons.append("Совпадает категория")
            if (
                process_version_id
                and data.get("process_version_id") == str(process_version_id)
                and (not step_key or data.get("step_key") in {None, step_key})
            ):
                reasons.append("Связано с этим процессом")
            authorized = redacted_bindings(session, actor, data, mode)
            for field, expected in (
                authorized.get("characteristics", {})
                if form_id
                and data.get("form_schema_id") == form_id
                and data.get("document_type_id") in {None, dtype}
                else {}
            ).items():
                actual = values.get(field)
                actuals = actual if isinstance(actual, list) else [actual]
                if actual is not None and {token(item) for item in expected} & {
                    token(item) for item in actuals
                }:
                    reasons.append("Совпадают разрешённые реквизиты")
                    break
            if not reasons:
                continue
            output = (
                template_output(session, actor, obj, mode, version)
                if version
                else case_output(session, actor, obj, mode)
            )
            result[key].append({**output, "reasons": reasons})
        result[key].sort(
            key=lambda item: (
                -int(item.get("recommended", False)),
                -len(item["reasons"]),
                str(item["id"]),
            )
        )
        result[key] = result[key][:20]
    return result
