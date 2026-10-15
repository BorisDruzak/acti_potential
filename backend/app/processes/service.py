"""Versioned descriptive routes. No process execution or user expressions."""

from copy import deepcopy
from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api.errors import fail
from app.content.api import block_output
from app.content.models import DocumentBlock
from app.identity.models import User
from app.processes.models import (
    Process,
    ProcessStep,
    ProcessTransition,
    ProcessVersion,
    StepSource,
    StepTemplate,
)
from app.registry.documents import (
    document_summary,
    file_visible,
    get_document,
    get_schema,
    raw_values,
)
from app.registry.models import DocumentFile, FormSchema, Registry
from app.registry.policy import condition_matches, field_accessible, permissions_for, redact_schema
from app.registry.validation import validate_schema
from app.registry.value_validation import validate_value
from app.relations.service import anchor


def invalid(message="Некорректная схема процесса"):
    fail(422, "process_validation", message)


def scope(session, actor, registry_id, *, configure=False):
    permissions = permissions_for(session, actor, registry_id)
    if "read" not in permissions:
        fail(404, "process_not_found", "Процесс не найден")
    if configure and ("admin" not in actor.roles or "update" not in permissions):
        fail(403, "process_configuration_denied", "Недостаточно прав на настройку процесса")


def get_process(session, actor, identity, *, configure=False, lock=False):
    query = select(Process).where(Process.id == identity)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None:
        fail(404, "process_not_found", "Процесс не найден")
    scope(session, actor, row.registry_id, configure=configure)
    if (
        not configure
        and not can_configure(session, actor, row)
        and session.scalar(
            select(ProcessVersion.id)
            .where(
                ProcessVersion.process_id == row.id,
                ProcessVersion.state == "published",
                ProcessVersion.effective_from <= date.today(),
            )
            .limit(1)
        )
        is None
    ):
        fail(404, "process_not_found", "Процесс не найден")
    return row


def can_configure(session, actor, process):
    return "admin" in actor.roles and {"read", "update"} <= permissions_for(
        session, actor, process.registry_id
    )


def get_version(session, actor, identity, *, configure=False, lock=False):
    query = select(ProcessVersion).where(ProcessVersion.id == identity)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None:
        fail(404, "process_not_found", "Процесс не найден")
    process = get_process(
        session, actor, row.process_id, configure=configure or row.state == "draft"
    )
    if not can_configure(session, actor, process) and row.effective_from > date.today():
        fail(404, "process_not_found", "Процесс не найден")
    return process, row


def metadata(data):
    return data.model_dump(mode="json", exclude={"registry_id", "code"})


def check_owner(session, data):
    if data.get("owner_id") and session.get(User, UUID(str(data["owner_id"]))) is None:
        invalid("Владелец процесса не найден")


def process_output(process):
    return {
        "id": process.id,
        "registry_id": process.registry_id,
        "code": process.code,
        **process.metadata_data,
        "created_at": process.created_at,
    }


def graph(session, version):
    steps = [
        {"key": row.key, **deepcopy(row.data)}
        for row in session.scalars(
            select(ProcessStep)
            .where(ProcessStep.version_id == version.id)
            .order_by(ProcessStep.ordinal)
        )
    ]
    for step in steps:
        for model, key in ((StepSource, "sources"), (StepTemplate, "templates")):
            step[key] = [
                deepcopy(row.data)
                for row in session.scalars(
                    select(model)
                    .where(model.version_id == version.id, model.step_key == step["key"])
                    .order_by(model.ordinal)
                )
            ]
    transitions = [
        {"key": row.key, **deepcopy(row.data), "ordinal": row.ordinal}
        for row in session.scalars(
            select(ProcessTransition)
            .where(ProcessTransition.version_id == version.id)
            .order_by(ProcessTransition.ordinal)
        )
    ]
    return steps, transitions


def save_graph(session, version, steps, transitions):
    for model in (StepSource, StepTemplate, ProcessTransition, ProcessStep):
        session.execute(delete(model).where(model.version_id == version.id))
    for ordinal, supplied in enumerate(steps):
        data = deepcopy(supplied)
        key, sources, templates = (
            data.pop("key"),
            data.pop("sources", []),
            data.pop("templates", []),
        )
        session.add(ProcessStep(version_id=version.id, key=key, ordinal=ordinal, data=data))
        for model, items in ((StepSource, sources), (StepTemplate, templates)):
            for index, item in enumerate(items):
                session.add(model(version_id=version.id, step_key=key, ordinal=index, data=item))
    for ordinal, supplied in enumerate(transitions):
        data = deepcopy(supplied)
        key = data.pop("key")
        data.pop("ordinal", None)
        session.add(ProcessTransition(version_id=version.id, key=key, ordinal=ordinal, data=data))
    session.flush()


def pinned_form(session, process, step):
    if not step.get("form_schema_id"):
        return None
    form = session.get(FormSchema, UUID(str(step["form_schema_id"])))
    registry = session.get(Registry, process.registry_id)
    if (
        form is None
        or form.state != "published"
        or str(form.document_type_id) not in registry.document_type_ids
    ):
        invalid("Закреплённая форма недоступна или несовместима с реестром")
    return form


def basis(session, actor, source, mode=None):
    document = get_document(session, actor, UUID(source["document_id"]))
    version_id = UUID(source["version_id"]) if source.get("version_id") else None
    block_id = UUID(source["block_id"]) if source.get("block_id") else None
    if (
        version_id is None
        and block_id is None
        and session.scalar(
            select(DocumentFile.id).where(DocumentFile.document_id == document.id).limit(1)
        )
    ):
        invalid("Для основания с оригиналом укажите точную версию")
    resolved = anchor(session, actor, document.id, version_id, block_id, mode)
    files = []
    if resolved:
        schema, values = (
            get_schema(session, document),
            raw_values(document, get_schema(session, document)),
        )
        for file in session.scalars(
            select(DocumentFile).where(DocumentFile.version_id == resolved["version_id"])
        ):
            if not file_visible(schema, actor, file, values, mode):
                continue
            if source.get("file_id") and str(file.id) != source["file_id"]:
                continue
            files.append(
                {
                    "id": file.id,
                    "name": file.name,
                    "version_id": file.version_id,
                    "version": resolved["version"],
                }
            )
        if source.get("file_id") and not files:
            fail(404, "process_basis_restricted", "Основание ограничено")
    return {
        "restricted": False,
        "document": document_summary(session, document, actor, mode=mode),
        "anchor": resolved,
        "files": files,
        "blocks": [block_output(session.get(DocumentBlock, block_id))] if block_id else [],
        "note": source.get("note", ""),
    }


def safe_basis(session, actor, source, mode=None):
    try:
        return basis(session, actor, source, mode)
    except HTTPException as error:
        if error.status_code not in {403, 404, 422}:
            raise
        return {"restricted": True, "label": "Основание ограничено"}


def condition_fields(form):
    return (
        {
            f["key"]: f
            for section in form.schema_data.get("sections", [])
            for f in section.get("fields", [])
        }
        if form
        else {}
    )


def condition_valid(condition, fields):
    if (
        not isinstance(condition, dict)
        or not isinstance(condition.get("field"), str)
        or condition.get("field") not in fields
    ):
        invalid("Условие ссылается на неизвестное поле закреплённой формы")
    controller = deepcopy(fields[condition["field"]])
    for key in ("visible_when", "required_when", "default", "system", "number_series_id"):
        controller.pop(key, None)
    dummy_key = (
        "process_condition"
        if controller["key"] != "process_condition"
        else "process_condition_other"
    )
    try:
        validate_schema(
            {
                "sections": [
                    {
                        "key": "routing",
                        "title": "Routing",
                        "fields": [
                            controller,
                            {
                                "key": dummy_key,
                                "type": "text",
                                "label": "Route",
                                "visible_when": condition,
                            },
                        ],
                    }
                ]
            }
        )
    except (ValueError, TypeError, KeyError):
        invalid("Некорректное типизированное условие перехода")


def validate_graph(session, actor, process, version, *, sources=True):
    steps, transitions = graph(session, version)
    keys = {step["key"] for step in steps}
    if (
        not steps
        or len(keys) != len(steps)
        or sum(step.get("is_start", False) for step in steps) != 1
    ):
        invalid("Требуется ровно один начальный шаг и уникальные ключи")
    outgoing = {key: [] for key in keys}
    forms = {}
    seen = set()
    for step in steps:
        forms[step["key"]] = pinned_form(session, process, step)
        if sources:
            for item in [*step["sources"], *step["templates"]]:
                basis(session, actor, item)
            from app.knowledge.service import get_version as template_version
            from app.knowledge.service import original_basis

            for identity in step.get("template_versions", []):
                _, pinned = template_version(session, actor, UUID(str(identity)))
                if pinned.state != "published":
                    invalid("Требуется опубликованная версия шаблона")
                original_basis(session, actor, pinned.data["original"])
    for transition in transitions:
        if transition["from_step"] not in keys or transition["to_step"] not in keys:
            invalid("Переход ссылается на отсутствующий шаг")
        identity = (
            transition["from_step"],
            transition["to_step"],
            str(transition.get("condition")),
            transition["is_else"],
        )
        if identity in seen:
            invalid("Повторяющийся переход")
        seen.add(identity)
        outgoing[transition["from_step"]].append(transition)
        if transition.get("condition") is not None:
            condition_valid(
                transition["condition"], condition_fields(forms[transition["from_step"]])
            )
        if transition["is_else"] and transition.get("condition") is not None:
            invalid("Путь иначе не содержит условия")
    for step in steps:
        paths = outgoing[step["key"]]
        if step["is_terminal"]:
            if paths:
                invalid("Конечный шаг не имеет исходящих переходов")
        elif not paths:
            invalid("Неконечный шаг должен иметь продолжение")
        elif any(path.get("condition") is not None for path in paths):
            if sum(path["is_else"] for path in paths) != 1 or any(
                not path["is_else"] and path.get("condition") is None for path in paths
            ):
                invalid("Условной группе требуется ровно один путь иначе")
            conditions = [path["condition"] for path in paths if path.get("condition") is not None]
            if any(item in conditions[:index] for index, item in enumerate(conditions)):
                invalid("Повторяющиеся условия")
        elif len(paths) != 1 or paths[0]["is_else"]:
            invalid("Безусловный шаг имеет ровно один переход")
    visiting, visited = set(), set()

    def visit(key):
        if key in visiting:
            invalid("Циклы в описании процесса не поддерживаются")
        if key in visited:
            return
        visiting.add(key)
        for edge in outgoing[key]:
            visit(edge["to_step"])
        visiting.remove(key)
        visited.add(key)

    visit(next(step["key"] for step in steps if step["is_start"]))
    if visited != keys:
        invalid("Все шаги должны быть достижимы из начала")
    return steps, transitions, forms


def version_output(session, actor, process, version, mode=None):
    steps, transitions = graph(session, version)
    forms = {}
    for step in steps:
        from app.knowledge.service import get_version as template_version
        from app.knowledge.service import safe_original

        pinned_templates = []
        for identity in step.get("template_versions", []):
            try:
                obj, pinned = template_version(session, actor, UUID(str(identity)))
                if (
                    pinned.state != "published"
                    or safe_original(session, actor, pinned.data["original"], mode)["restricted"]
                ):
                    raise HTTPException(404)
                pinned_templates.append(
                    {
                        "template_version_id": pinned.id,
                        "name": pinned.data["name"],
                        "version": pinned.version,
                        "restricted": False,
                    }
                )
            except HTTPException:
                pinned_templates.append({"restricted": True, "label": "Шаблон ограничен"})
        if "template_versions" in step:
            step["template_versions"] = pinned_templates
        for key in ("sources", "templates"):
            output = []
            for item in step[key]:
                resolved = safe_basis(session, actor, item, mode)
                output.append(resolved if resolved["restricted"] else {**item, "restricted": False})
            step[key] = output
        try:
            form = pinned_form(session, process, step)
        except HTTPException:
            form = None
            step["form_schema_id"] = None
            step["form_restricted"] = True
        forms[step["key"]] = form
        step["form_schema"] = redact_schema(form.schema_data, actor, mode) if form else None
        if form:
            fields = condition_fields(form)
            for section in step["form_schema"]["sections"]:
                section["fields"] = [
                    field
                    for field in section["fields"]
                    if field_accessible(fields[field["key"]], actor, fields, mode)
                ]
    for transition in transitions:
        condition = transition.get("condition")
        fields = condition_fields(forms.get(transition["from_step"]))
        if condition and (
            not isinstance(condition.get("field"), str)
            or condition["field"] not in fields
            or not field_accessible(fields[condition["field"]], actor, fields, mode)
        ):
            transition["condition"] = None
            transition["restricted"] = True
            transition["label"] = "Условный переход ограничен"
    return {
        "id": version.id,
        "process_id": version.process_id,
        "version": version.version,
        "state": version.state,
        "effective_from": version.effective_from,
        "revision": version.revision,
        "metadata_snapshot": version.metadata_snapshot
        if version.state == "published"
        else process.metadata_data,
        "steps": steps,
        "transitions": transitions,
        "published_at": version.published_at,
    }


def preview(session, actor, process, version, data, mode=None):
    steps, transitions, forms = validate_graph(session, actor, process, version, sources=False)
    if data.step_key not in {step["key"] for step in steps}:
        invalid("Шаг не найден")
    fields = condition_fields(forms[data.step_key])
    paths = [edge for edge in transitions if edge["from_step"] == data.step_key]
    values = {key: field["default"] for key, field in fields.items() if "default" in field}
    values.update(data.values)
    for path in paths:
        condition = path.get("condition")
        if condition and not field_accessible(
            fields[condition["field"]], actor, fields, mode, values=values
        ):
            fail(403, "process_condition_restricted", "Нет доступа к данным условия")
    for key, value in data.values.items():
        if key not in fields or not field_accessible(
            fields[key], actor, fields, mode, values=values
        ):
            fail(403, "process_condition_restricted", "Нет доступа к данным условия")
        values[key] = validate_value(session, actor, fields[key], value, process.registry_id)
    for key in {edge["condition"]["field"] for edge in paths if edge.get("condition")}:
        field = fields[key]
        condition = field.get("required_when")
        if condition and not field_accessible(
            fields[condition["field"]], actor, fields, mode, values=values
        ):
            fail(403, "process_condition_restricted", "Нет доступа к данным условия")
        required = field.get("required", False) or bool(
            condition and condition_matches(condition, values, fields)
        )
        value = values.get(key)
        if (
            required
            and field_accessible(field, actor, fields, mode, values=values)
            and (value is None or value == [] or isinstance(value, str) and not value.strip())
        ):
            invalid("Обязательное поле условия не заполнено")
    selected = next(
        (
            edge
            for edge in paths
            if not edge["is_else"] and condition_matches(edge.get("condition"), values, fields)
        ),
        None,
    )
    selected = selected or next((edge for edge in paths if edge["is_else"]), None)
    return {
        "next_step_keys": [selected["to_step"]] if selected else [],
        "transition_key": selected["key"] if selected else None,
    }


def editable(version, revision):
    if version.state != "draft":
        fail(409, "process_version_immutable", "Опубликованная версия неизменяема")
    if version.revision != revision:
        fail(409, "revision_conflict", "Черновик изменён другим пользователем")


def new_version(session, actor, process, original=None):
    latest = session.scalar(
        select(ProcessVersion)
        .where(ProcessVersion.process_id == process.id)
        .order_by(ProcessVersion.version.desc())
        .limit(1)
    )
    version = ProcessVersion(
        process_id=process.id,
        version=latest.version + 1 if latest else 1,
        effective_from=date.today(),
        created_by=actor.id,
    )
    session.add(version)
    session.flush()
    if original:
        save_graph(session, version, *graph(session, original))
    return version
