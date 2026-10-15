"""Public built-in ACTI v2 configuration; no live source records or identities."""

from sqlalchemy import select, text

from app.registry.models import DocumentType, FormSchema, ReferenceItem, ReferenceSet, Registry
from app.registry.validation import validate_schema

REFERENCE_SETS = {
    "status": "Статусы",
    "document_types": "Типы записей ACTI v2",
    "signing_types": "Типы подписания",
    "document_kinds": "Виды документов",
    "themes": "Темы",
    "executors": "Исполнители",
    "responsible_executors": "Ответственные исполнители",
    "published_where": "Места публикации",
    "signers": "Подписанты",
    "approvers": "Согласующие",
}
# Only public classification defaults from the archived schema. People, themes and local
# publication values must be populated separately; no machine data is imported by startup.
REFERENCE_DEFAULTS = {
    "status": [
        "Внесены дополнения",
        "Внесены дополнения и изменения",
        "Внесены изменения",
        "Действует",
        "Утратило силу",
        "Отменено",
    ],
    "document_types": ["Постановление", "Распоряжение"],
    "signing_types": ["Одностороннее", "Многостороннее"],
    "document_kinds": ["Ненормативный правовой акт", "Нормативный правовой акт"],
}


def _field(key, label, kind="text", **options):
    return {
        "key": key,
        "label": label,
        "type": kind,
        "filterable": kind != "file",
        "exportable": kind != "file",
        **options,
    }


def _ref(key, label, code, multiple=False):
    return _field(key, label, "multi_reference" if multiple else "reference", reference_code=code)


BUILTIN_SCHEMA = {
    "sections": [
        {
            "key": "main",
            "title": "Основные сведения",
            "fields": [
                _field(
                    "reg_number",
                    "Регистрационный номер",
                    system="reg_number",
                    constraints={"max_length": 255},
                ),
                _field("reg_date", "Дата регистрации", "date", system="reg_date"),
                _field("title", "Наименование документа", system="title", required=True),
                _ref("type_id", "Тип документа ACTI v2", "document_types"),
                _ref("status_id", "Статус правового акта", "status"),
                _ref("document_kind_id", "Вид документа", "document_kinds"),
                _ref("signing_type_id", "Тип подписания", "signing_types"),
                _ref("theme_id", "Тема", "themes"),
                _ref("executor_id", "Исполнитель", "executors"),
                _ref(
                    "responsible_executor_id", "Ответственный исполнитель", "responsible_executors"
                ),
                _field(
                    "pages_count",
                    "Количество страниц",
                    "integer",
                    default=0,
                    constraints={"min": 0, "max": 9999},
                ),
                _field(
                    "attachments_count",
                    "Количество приложений",
                    "integer",
                    default=0,
                    constraints={"min": 0, "max": 999},
                ),
            ],
        },
        {
            "key": "publication",
            "title": "Публикация",
            "fields": [
                _field(
                    "should_publish",
                    "Подлежит опубликованию",
                    "boolean",
                    default=None,
                    description="Не указано / Да / Нет; исторический NULL сохраняется как отсутствие значения.",
                ),
                _ref("published_where_id", "Где опубликовано", "published_where"),
                _field("published_date", "Дата публикации", "date"),
                _field("number", "Номер публикации / газеты", constraints={"max_length": 255}),
            ],
        },
        {
            "key": "control",
            "title": "Контроль исполнения",
            "fields": [
                _field("control_date", "Дата контроля", "date"),
                _field("removed_from_control", "Снято с контроля", "boolean", default=False),
                _field("execution_result", "Результат исполнения", "textarea"),
            ],
        },
        {
            "key": "filing",
            "title": "Делопроизводство",
            "fields": [
                _field("case_number", "Номер дела", constraints={"max_length": 255}),
                _field("volume_number", "Номер тома", constraints={"max_length": 255}),
                _field("sheets", "Листы", constraints={"max_length": 255}),
            ],
        },
        {
            "key": "people",
            "title": "Подписанты и согласующие",
            "fields": [
                _ref("signers", "Подписанты", "signers", multiple=True),
                _ref("approvers", "Согласующие", "approvers", multiple=True),
            ],
        },
        {
            "key": "files",
            "title": "Оригиналы и вложения",
            "fields": [
                _field(
                    "document_path",
                    "Файл документа",
                    "file",
                    description="Новая связь с версией файла; исторический путь переносится только контролируемым импортом.",
                ),
            ],
        },
    ]
}


def seed_builtin(session):
    """Idempotently initialize configuration only; grants and documents remain explicit."""
    session.execute(text("SELECT pg_advisory_xact_lock(208091002)"))
    kind = session.scalar(select(DocumentType).where(DocumentType.code == "acti-v2"))
    if kind is None:
        kind = DocumentType(
            code="acti-v2",
            name="ACTI v2 — нормативные и распорядительные документы",
            description="Встроенная конфигурация универсального движка ACTI v3",
        )
        session.add(kind)
        session.flush()
    if session.scalar(select(FormSchema.id).where(FormSchema.document_type_id == kind.id)) is None:
        session.add(
            FormSchema(
                document_type_id=kind.id,
                version=1,
                state="published",
                schema_data=validate_schema(BUILTIN_SCHEMA),
            )
        )
    for code, name in (("okrug", "Округ"), ("raion", "Район")):
        registry = session.scalar(select(Registry).where(Registry.code == code))
        if registry is None:
            registry = Registry(
                code=code, name=name, description="", document_type_ids=[str(kind.id)]
            )
            session.add(registry)
            session.flush()
        for ref_code, ref_name in REFERENCE_SETS.items():
            ref = session.scalar(
                select(ReferenceSet).where(
                    ReferenceSet.registry_id == registry.id, ReferenceSet.code == ref_code
                )
            )
            if ref is None:
                ref = ReferenceSet(code=ref_code, name=ref_name, registry_id=registry.id)
                session.add(ref)
                session.flush()
                for label in REFERENCE_DEFAULTS.get(ref_code, []):
                    session.add(
                        ReferenceItem(
                            reference_set_id=ref.id, label=label, active=True, item_metadata={}
                        )
                    )
    session.flush()
