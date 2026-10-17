"""Small serialized execution domain over frozen declarative process snapshots."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.errors import fail
from app.execution.models import (
    ChecklistItem,
    ProcessInstance,
    StepExecution,
    StepResult,
)
from app.identity.models import Role, User, UserRole
from app.identity.policy import Actor
from app.knowledge import service as knowledge
from app.processes import service as processes
from app.registry.archives import configuration_active_clause
from app.registry.documents import (
    document_summary,
    file_visible,
    get_document,
    get_schema,
    historical_values,
    raw_values,
)
from app.registry.models import (
    DocumentFile,
    DocumentVersion,
    FormSchema,
    MetadataRevision,
    Registry,
    RegistryAccess,
)
from app.registry.policy import (
    condition_matches,
    field_accessible,
    permissions_for,
    redact_schema,
    redact_values,
    role_actions,
)
from app.registry.schema_compatibility import compatible_people_schema, pinned_scalar_values
from app.registry.validation import iter_fields
from app.registry.value_validation import validate_value, validate_values


def now():
    return datetime.now(UTC)


def uuid(value):
    return UUID(str(value))


def invalid(message="Некорректные данные исполнения"):
    fail(422, "execution_validation", message)


def hidden():
    fail(404, "execution_not_found", "Исполнение не найдено")


def scope(session, actor, registry_id, action="read"):
    allowed = permissions_for(session, actor, registry_id)
    if "read" not in allowed:
        hidden()
    if action not in allowed:
        fail(403, "execution_forbidden", "Недостаточно прав на исполнение")


def get_instance(session, actor, identity, *, lock=False):
    if "read" not in role_actions(actor):
        hidden()
    query = (
        select(ProcessInstance)
        .join(RegistryAccess, RegistryAccess.registry_id == ProcessInstance.registry_id)
        .where(
            ProcessInstance.id == identity,
            RegistryAccess.user_id == actor.id,
            RegistryAccess.permissions.contains(["read"]),
            configuration_active_clause(Registry, "registry", ProcessInstance.registry_id),
        )
    )
    if lock:
        query = query.with_for_update(of=ProcessInstance)
    if actor.scoped_registry_ids is not None:
        query = query.where(ProcessInstance.registry_id.in_(actor.scoped_registry_ids))
    instance = session.scalar(query)
    if instance is None:
        hidden()
    return instance


def get_attempt(session, instance, identity=None):
    attempt = session.get(StepExecution, identity or instance.current_execution_id)
    if attempt is None or attempt.instance_id != instance.id:
        hidden()
    return attempt


def step_definition(instance, key):
    return next(step for step in instance.snapshot["steps"] if step["key"] == key)


def schema_for(session, instance, step, *, required=False):
    identity = step.get("form_schema_id")
    if not identity:
        return None
    form = session.get(FormSchema, uuid(identity))
    registry = session.get(Registry, instance.registry_id)
    if form is None or str(form.document_type_id) not in registry.document_type_ids:
        if required:
            fail(403, "execution_form_restricted", "Форма результата недоступна")
        return None
    return instance.snapshot["schemas"].get(str(identity))


def assignee(session, registry_id, identity):
    user = session.get(User, identity)
    roles = frozenset(
        session.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == identity)
        )
    )
    if user is None or not user.active or not roles.intersection({"editor", "user", "admin"}):
        invalid("Исполнитель должен быть действующим редактором")
    actor = Actor(user.id, user.display_name, roles)
    if not {"read", "update"} <= permissions_for(session, actor, registry_id):
        invalid("Исполнитель не имеет прав на работу в реестре")
    return user


def can_work(session, actor, instance):
    return (
        instance.assignee_id == actor.id
        and "update" in permissions_for(session, actor, instance.registry_id)
        and session.get(User, actor.id).active
    )


def can_control(session, actor, instance):
    return actor.id in {instance.created_by, instance.assignee_id} and "update" in permissions_for(
        session, actor, instance.registry_id
    )


def mutation(session, actor, identity, revision, *, work=False):
    instance = get_instance(session, actor, identity, lock=True)
    if instance.revision != revision:
        fail(409, "revision_conflict", "Исполнение изменено другим пользователем")
    if instance.state in {"completed", "cancelled"}:
        fail(409, "execution_terminal", "Завершённое исполнение неизменяемо")
    if not (can_work(session, actor, instance) if work else can_control(session, actor, instance)):
        fail(403, "execution_forbidden", "Действие недоступно текущему пользователю")
    if work:
        assignee(session, instance.registry_id, actor.id)
        if instance.state == "suspended":
            fail(409, "execution_suspended", "Сначала возобновите исполнение")
    return instance, get_attempt(session, instance)


def require_active(attempt):
    if attempt.state != "active":
        fail(409, "execution_step_not_open", "Сначала откройте текущий шаг")


def check_reason(reason):
    if not reason.strip():
        invalid("Укажите причину действия")


def deadline(value, reason=""):
    if value and value < now() and not reason.strip():
        invalid("Для срока в прошлом укажите причину")
    return value


def make_attempt(session, instance, key):
    definition = step_definition(instance, key)
    previous = session.scalar(
        select(func.max(StepExecution.attempt)).where(
            StepExecution.instance_id == instance.id, StepExecution.step_key == key
        )
    )
    attempt = StepExecution(
        instance_id=instance.id,
        step_key=key,
        attempt=(previous or 0) + 1,
        assignee_id=instance.assignee_id,
    )
    session.add(attempt)
    session.flush()
    instance.current_execution_id = attempt.id
    session.flush()
    items = [
        (kind, label)
        for kind, source in (("input", "inputs"), ("output", "outputs"))
        for label in definition.get(source, [])
    ]
    if definition.get("control_note"):
        items.append(("control", definition["control_note"]))
    for ordinal, (kind, label) in enumerate(items):
        session.add(
            ChecklistItem(step_execution_id=attempt.id, ordinal=ordinal, label=label, kind=kind)
        )
    session.flush()
    return attempt


def freeze_ref(session, actor, supplied, *, allow_closed=False):
    result = deepcopy(supplied)
    if result.get("version_id") or result.get("block_id"):
        if not allow_closed:
            processes.basis(session, actor, result)
        return result
    if result.get("file_id"):
        file = session.get(DocumentFile, uuid(result["file_id"]))
        if file is None:
            invalid("Версия результата недоступна")
        result["version_id"] = str(file.version_id)
        processes.basis(session, actor, result)
        return result
    document_id = uuid(result["document_id"])
    has_files = session.scalar(
        select(DocumentFile.id).where(DocumentFile.document_id == document_id).limit(1)
    )
    if not has_files:
        if not allow_closed:
            get_document(session, actor, document_id)
        return result
    try:
        document = get_document(session, actor, document_id, "download")
        schema = get_schema(session, document)
        file = next(
            (
                file
                for file, version in session.execute(
                    select(DocumentFile, DocumentVersion)
                    .join(DocumentVersion, DocumentVersion.id == DocumentFile.version_id)
                    .where(DocumentFile.document_id == document_id)
                    .order_by(DocumentVersion.version.desc(), DocumentFile.id)
                )
                if file_visible(schema, actor, file, raw_values(document, schema))
            ),
            None,
        )
        if file is None:
            raise HTTPException(404)
        result["version_id"] = str(file.version_id)
        return result
    except HTTPException:
        fail(403, "execution_basis_unavailable", "Не удалось закрепить версию основания")


def build_snapshot(session, actor, process, version):
    steps, transitions, forms = processes.validate_graph(
        session, actor, process, version, sources=False
    )
    schemas = {}
    for step in steps:
        for key in ("sources", "templates"):
            step[key] = [freeze_ref(session, actor, item, allow_closed=True) for item in step[key]]
        form = forms[step["key"]]
        if form:
            schemas[str(form.id)] = deepcopy(form.schema_data)
    return {
        "version": version.version,
        "metadata_snapshot": deepcopy(version.metadata_snapshot),
        "steps": steps,
        "transitions": transitions,
        "schemas": schemas,
    }


def projected_snapshot(session, actor, instance, mode=None):
    result = deepcopy(instance.snapshot)
    result.pop("schemas", None)
    forms = {}
    for step in result["steps"]:
        for key in ("sources", "templates"):
            projected = []
            for item in step[key]:
                basis = processes.safe_basis(session, actor, item, mode)
                projected.append(basis if basis["restricted"] else {**item, "restricted": False})
            step[key] = projected
        projected = []
        for identity in step.get("template_versions", []):
            try:
                obj, pinned = knowledge.get_version(session, actor, uuid(identity))
                if knowledge.safe_original(session, actor, pinned.data["original"], mode)[
                    "restricted"
                ]:
                    raise HTTPException(404)
                projected.append(
                    {
                        "template_version_id": pinned.id,
                        "name": pinned.data["name"],
                        "version": pinned.version,
                        "restricted": False,
                    }
                )
            except HTTPException:
                projected.append({"restricted": True, "label": "Шаблон ограничен"})
        step["template_versions"] = projected
        schema = schema_for(session, instance, step)
        fields = {field["key"]: field for field in iter_fields(schema or {})}
        forms[step["key"]] = fields
        step["form_schema"] = redact_schema(schema, actor, mode) if schema else None
        if schema:
            for section in step["form_schema"]["sections"]:
                section["fields"] = [
                    field
                    for field in section["fields"]
                    if field_accessible(fields[field["key"]], actor, fields, mode)
                ]
        elif step.get("form_schema_id"):
            step["form_schema_id"] = None
            step["form_restricted"] = True
    for transition in result["transitions"]:
        condition = transition.get("condition")
        fields = forms[transition["from_step"]]
        if condition and (
            condition["field"] not in fields
            or not field_accessible(fields[condition["field"]], actor, fields, mode)
        ):
            transition["condition"], transition["restricted"], transition["label"] = (
                None,
                True,
                "Условный переход ограничен",
            )
    return result


def result_document(session, actor, instance, definition, supplied):
    document = get_document(session, actor, supplied.document_id, "update", lock=True)
    if document.revision != supplied.revision:
        fail(409, "execution_document_revision", "Документ результата изменён")
    if not compatible_people_schema(session, document.schema_version_id, definition.get("form_schema_id")):
        invalid("Документ результата использует другую версию формы")
    revision = session.scalar(
        select(MetadataRevision).where(
            MetadataRevision.document_id == document.id,
            MetadataRevision.revision == supplied.revision,
        )
    )
    if revision is None:
        invalid("Подтверждённая редакция метаданных не найдена")
    edition = session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version.desc())
        .limit(1)
    )
    if supplied.version_id and (edition is None or edition.id != supplied.version_id):
        invalid("Выберите текущую версию оригинала документа результата")
    schema = schema_for(session, instance, definition, required=True)
    previous = deepcopy(revision.values)
    if str(document.schema_version_id) != definition.get("form_schema_id"):
        try:
            previous = pinned_scalar_values(schema, get_schema(session, document), previous)
        except ValueError as error:
            invalid(str(error))
    values = validate_values(
        session,
        actor,
        schema,
        {},
        document.registry_id,
        previous=previous,
        document_id=document.id,
    )
    fields = {field["key"]: field for field in iter_fields(schema)}
    required_access(actor, fields, values)
    for field in fields.values():
        if not field_accessible(field, actor, fields, values=values):
            continue
        if field["key"] in values:
            validate_value(
                session,
                actor,
                field,
                values[field["key"]],
                document.registry_id,
                document_id=document.id,
                root_values=values,
                root_fields=fields,
            )
    return values, {
        "document_id": str(document.id),
        "metadata_revision_id": str(revision.id),
        "revision": revision.revision,
        "schema_version_id": str(document.schema_version_id),
        "version_id": str(edition.id) if edition else None,
    }


def required_access(actor, fields, values):
    for field in fields.values():
        for condition in (field.get("required_when"), field.get("visible_when")):
            if (
                condition
                and (field.get("required") or field.get("required_when"))
                and not field_accessible(fields[condition["field"]], actor, fields, values=values)
            ):
                fail(
                    403,
                    "execution_condition_restricted",
                    "Нет доступа к обязательным данным результата",
                )
        required = field.get("required", False) or bool(
            field.get("required_when") and condition_matches(field["required_when"], values, fields)
        )
        if (
            required
            and condition_matches(field.get("visible_when"), values, fields)
            and not field_accessible(field, actor, fields, values=values)
        ):
            fail(
                403,
                "execution_condition_restricted",
                "Нет доступа к обязательным данным результата",
            )
        if required and condition_matches(field.get("visible_when"), values, fields):
            value = values.get(field["key"])
            if value is None or value == [] or isinstance(value, str) and not value.strip():
                invalid("Обязательное поле результата не заполнено")


def confirmed_values(session, actor, instance, attempt, data):
    definition = step_definition(instance, attempt.step_key)
    schema = schema_for(session, instance, definition, required=True)
    if data.result_document:
        if data.values:
            invalid("Для документа результата значения читаются сервером")
        return result_document(session, actor, instance, definition, data.result_document)
    if schema is None:
        if data.values:
            invalid("Шаг не связан с формой результата")
        return {}, None
    fields = list(iter_fields(schema))
    if any(
        field["type"] == "file"
        and (field.get("required") or field["key"] in data.values)
        or field.get("number_series_id")
        and field["key"] in data.values
        for field in fields
    ):
        invalid("Для файлов и управляемых номеров выберите документ результата")
    values = validate_values(session, actor, schema, data.values, instance.registry_id)
    if any(
        field.get("number_series_id")
        and field.get("required")
        and values.get(field["key"]) in (None, "", [])
        for field in fields
    ):
        invalid("Для обязательного управляемого номера выберите документ результата")
    required_access(actor, {field["key"]: field for field in fields}, values)
    return values, None


def result_output(session, actor, instance, attempt, result, mode=None):
    schema = schema_for(session, instance, step_definition(instance, attempt.step_key))
    values = redact_values(schema, result.values, actor, mode) if schema else {}
    provenance = deepcopy(result.result_document)
    if provenance:
        try:
            document = get_document(session, actor, uuid(provenance["document_id"]), mode=mode)
            if not compatible_people_schema(session, document.schema_version_id, provenance["schema_version_id"]):
                raise HTTPException(404)
            current_schema = get_schema(session, document)
            allowed = redact_values(current_schema, historical_values(current_schema, result.values), actor, mode)
            values = {key: value for key, value in values.items() if key in allowed}
            provenance["title"] = document_summary(session, document, actor, mode=mode).get("title")
        except HTTPException:
            values = {}
            provenance = {"restricted": True, "label": "Документ результата ограничен"}
    attachments = []
    for item in result.attachments:
        basis = processes.safe_basis(session, actor, item, mode)
        attachments.append(basis if basis["restricted"] else {**item, "restricted": False})
    return {
        "id": result.id,
        "values": values,
        "comment": result.comment,
        "attachments": attachments,
        "result_document": provenance,
        "actor_name": session.get(User, result.actor_id).display_name,
        "created_at": result.created_at,
    }


def trajectory_restricted(session, actor, instance, mode=None):
    for attempt in session.scalars(
        select(StepExecution).where(
            StepExecution.instance_id == instance.id, StepExecution.state == "completed"
        )
    ):
        definition = step_definition(instance, attempt.step_key)
        schema = schema_for(session, instance, definition)
        fields = {field["key"]: field for field in iter_fields(schema or {})}
        result = session.get(StepResult, attempt.result_id) if attempt.result_id else None
        values = result.values if result else {}
        if result and result.result_document:
            try:
                get_document(session, actor, uuid(result.result_document["document_id"]), mode=mode)
            except HTTPException:
                if any(
                    edge.get("condition")
                    for edge in instance.snapshot["transitions"]
                    if edge["from_step"] == attempt.step_key
                ):
                    return True
        for edge in instance.snapshot["transitions"]:
            condition = edge.get("condition")
            if (
                edge["from_step"] == attempt.step_key
                and condition
                and (
                    condition["field"] not in fields
                    or not field_accessible(
                        fields[condition["field"]], actor, fields, mode, values=values
                    )
                )
            ):
                return True
    return False


def overdue(instance, attempt=None, *, restricted=False):
    if instance.state in {"completed", "cancelled"}:
        return False
    deadlines = [instance.deadline_at]
    if attempt and not restricted:
        deadlines.append(attempt.deadline_at)
    return any(value is not None and value < now() for value in deadlines)


def instance_output(session, actor, instance, mode=None, *, summary=False):
    restricted = trajectory_restricted(session, actor, instance, mode)
    current = get_attempt(session, instance)
    user = session.get(User, instance.assignee_id)
    result = {
        "id": instance.id,
        "registry_id": instance.registry_id,
        "process_version_id": instance.process_version_id,
        "name": instance.name,
        "state": instance.state,
        "revision": instance.revision,
        "created_by": instance.created_by,
        "created_by_name": session.get(User, instance.created_by).display_name,
        "created_at": instance.created_at,
        "completed_at": instance.completed_at,
        "assignee_id": None if restricted else instance.assignee_id,
        "assignee_name": None if restricted else user.display_name,
        "current_step_key": None if restricted else current.step_key,
        "deadline_at": instance.deadline_at,
        "current_step_execution_id": None if restricted else current.id,
        "overdue": overdue(instance, current, restricted=restricted),
        "trajectory_restricted": restricted,
        "permissions": {
            "control": can_control(session, actor, instance)
            and instance.state not in {"completed", "cancelled"},
            "work": can_work(session, actor, instance)
            and not restricted
            and instance.state not in {"completed", "cancelled", "suspended"},
        },
        "steps": [],
        "history": [],
    }
    if summary:
        return result
    result["process_snapshot"] = projected_snapshot(session, actor, instance, mode)
    if restricted:
        return result
    for attempt in session.scalars(
        select(StepExecution)
        .where(StepExecution.instance_id == instance.id)
        .order_by(StepExecution.started_at.asc().nullsfirst(), StepExecution.id)
    ):
        definition = step_definition(instance, attempt.step_key)
        items = [
            {
                "id": item.id,
                "label": item.label,
                "kind": item.kind,
                "required": item.required,
                "checked": item.checked,
            }
            for item in session.scalars(
                select(ChecklistItem)
                .where(ChecklistItem.step_execution_id == attempt.id)
                .order_by(ChecklistItem.ordinal)
            )
        ]
        stored = session.get(StepResult, attempt.result_id) if attempt.result_id else None
        result["steps"].append(
            {
                "id": attempt.id,
                "step_key": attempt.step_key,
                "title": definition["title"],
                "attempt": attempt.attempt,
                "state": attempt.state,
                "assignee_id": attempt.assignee_id,
                "assignee_name": session.get(User, attempt.assignee_id).display_name,
                "started_at": attempt.started_at,
                "completed_at": attempt.completed_at,
                "deadline_at": attempt.deadline_at,
                "overdue": attempt.state in {"pending", "active"} and overdue(instance, attempt),
                "checklist": items,
                "result": result_output(session, actor, instance, attempt, stored, mode)
                if stored
                else None,
            }
        )
    return result


def route(session, actor, instance, attempt, result):
    definition = step_definition(instance, attempt.step_key)
    schema = schema_for(session, instance, definition, required=True)
    if result.result_document:
        document = get_document(
            session, actor, uuid(result.result_document["document_id"]), "update"
        )
        if document.revision != result.result_document["revision"]:
            fail(
                409,
                "execution_document_revision",
                "Документ результата изменён; подтвердите новый результат",
            )
        validate_values(
            session,
            actor,
            schema,
            {},
            document.registry_id,
            previous=result.values,
            document_id=document.id,
        )
    elif schema:
        validate_values(session, actor, schema, result.values, instance.registry_id)
    fields = {field["key"]: field for field in iter_fields(schema or {})}
    required_access(actor, fields, result.values)
    paths = [
        edge for edge in instance.snapshot["transitions"] if edge["from_step"] == attempt.step_key
    ]
    for edge in paths:
        condition = edge.get("condition")
        if condition and not field_accessible(
            fields[condition["field"]], actor, fields, values=result.values
        ):
            fail(403, "execution_condition_restricted", "Нет доступа к данным условия")
    for item in [*definition["sources"], *definition["templates"], *result.attachments]:
        processes.basis(session, actor, item)
    for identity in definition.get("template_versions", []):
        _, template = knowledge.get_version(session, actor, uuid(identity))
        knowledge.original_basis(session, actor, template.data["original"])
    selected = next(
        (
            edge
            for edge in paths
            if not edge["is_else"]
            and condition_matches(edge.get("condition"), result.values, fields)
        ),
        None,
    )
    return selected or next((edge for edge in paths if edge["is_else"]), None)
