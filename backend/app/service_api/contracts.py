from datetime import date, datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

Operation = Literal[
    "documents",
    "files",
    "content",
    "relations",
    "processes",
    "knowledge",
    "numbers",
    "execution",
    "events",
]
OPERATIONS = {
    "documents": "Карточки и редакции метаданных",
    "files": "Версии и оригиналы файлов",
    "content": "Извлечённые фрагменты",
    "relations": "Связи документов",
    "processes": "Версии процессов",
    "knowledge": "Шаблоны и практика",
    "numbers": "Реестры номеров",
    "execution": "Исполнение процессов",
    "events": "События разрешённых объектов",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reason(Strict):
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Reason required")
        return value


class AccountInput(Strict):
    name: str = Field(min_length=1, max_length=200)
    registry_ids: list[UUID] = Field(min_length=1, max_length=100)
    operations: list[Operation] = Field(min_length=1, max_length=9)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Name required")
        return value.strip()


class AccountUpdate(AccountInput, Reason):
    state: Literal["active", "disabled"]


class MintInput(Reason):
    expires_in_days: int = Field(ge=1, le=90)
    registry_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)
    operations: list[Operation] | None = Field(default=None, min_length=1, max_length=9)


class TokenRead(BaseModel):
    id: UUID
    prefix: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    registry_ids: list[UUID]
    operations: list[Operation]


class TokenIssued(TokenRead):
    token: str


class AccountRead(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    state: Literal["active", "disabled"]
    registry_ids: list[UUID]
    operations: list[Operation]
    created_at: datetime
    tokens: list[TokenRead]


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Pointer(BaseModel):
    type: str
    id: UUID
    href: str


class EventRead(BaseModel):
    id: UUID
    event_type: str
    payload_version: Literal[1]
    created_at: datetime
    object: Pointer
    origins: list[Pointer]


class EventPage(BaseModel):
    items: list[EventRead]
    next_cursor: UUID


class Restricted(BaseModel):
    restricted: Literal[True]
    label: str


class SourcePointer(BaseModel):
    document_id: UUID
    version_id: UUID | None = None
    block_id: UUID | None = None
    file_id: UUID | None = None
    note: str = ""
    restricted: Literal[False] = False


class FileRead(BaseModel):
    id: UUID
    document_id: UUID
    version_id: UUID
    name: str
    mime: str
    size: int
    sha256: str


class DocumentRead(BaseModel):
    id: UUID
    registry_id: UUID
    document_type_id: UUID
    schema_version_id: UUID
    revision: int
    current_version: int
    title: str | None
    reg_number: str | None
    reg_date: date | None
    status: str | None
    created_at: datetime
    updated_at: datetime
    values: dict[str, JsonValue]
    schema_content: dict[str, JsonValue] = Field(alias="schema")
    files: list[FileRead]


class FileEdition(BaseModel):
    id: UUID
    document_id: UUID
    version: int
    created_at: datetime
    files: list[FileRead]


class MetadataRead(BaseModel):
    id: UUID
    document_id: UUID
    revision: int
    schema_version_id: UUID
    values: dict[str, JsonValue]
    created_at: datetime


class FormRead(BaseModel):
    id: UUID
    document_type_id: UUID
    version: int
    state: Literal["published"]
    schema_content: dict[str, JsonValue] = Field(alias="schema")


class BlockRead(BaseModel):
    id: UUID
    document_id: UUID
    extraction_id: UUID
    file_id: UUID
    version_id: UUID
    kind: str
    ordinal: int
    parent_id: UUID | None
    page: int | None
    anchor: str
    text: str
    table: list[list[str]] | None


class ExtractionRead(BaseModel):
    id: UUID
    document_id: UUID
    file_id: UUID
    version_id: UUID
    version: int
    name: str
    sha256: str
    parser: str
    parser_version: str
    state: str
    attempts: int
    error_code: str | None
    warnings: list[str]
    started_at: datetime | None
    completed_at: datetime | None
    block_count: int


class ConditionRead(BaseModel):
    field: str
    op: Literal["eq", "ne", "in", "not_in", "present", "absent"]
    value: JsonValue = None


class TransitionRead(BaseModel):
    key: str
    from_step: str
    to_step: str
    label: str = ""
    condition: ConditionRead | None = None
    is_else: bool
    restricted: bool = False
    ordinal: int


class StepRead(BaseModel):
    key: str
    title: str
    instruction: str = ""
    executor_role: str = ""
    department: str = ""
    duration_hours: float | None = None
    deadline: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    system: str = ""
    control_note: str = ""
    is_start: bool
    is_terminal: bool
    form_schema_id: UUID | None = None
    form_schema: dict[str, JsonValue] | None = None
    form_restricted: bool = False
    sources: list[SourcePointer | Restricted]
    templates: list[SourcePointer | Restricted]
    template_versions: list[dict[str, JsonValue]] = Field(default_factory=list)


class ProcessVersionRead(BaseModel):
    id: UUID
    process_id: UUID
    version: int
    state: str
    effective_from: date
    revision: int
    metadata_snapshot: dict[str, JsonValue]
    steps: list[StepRead]
    transitions: list[TransitionRead]
    published_at: datetime | None


class RelationRead(BaseModel):
    id: UUID
    relation_type: str
    state: str
    source: str
    source_document_id: UUID
    target_document_id: UUID | None
    source_version_id: UUID | None = None
    target_version_id: UUID | None = None
    source_block_id: UUID | None = None
    target_block_id: UUID | None = None
    external_target: dict[str, JsonValue] | None = None
    effective_date: date | None = None
    reason: str | None = None
    description: str = ""


class KnowledgeRead(BaseModel):
    id: UUID
    registry_id: UUID
    name: str
    state: str
    revision: int
    visibility: str
    lifecycle: str
    purpose: str = ""
    category: str = ""
    description: str | None = None
    result: str | None = None
    version: int | None = None
    template_id: UUID | None = None
    original: dict[str, JsonValue] | None = None
    links: list[SourcePointer | Restricted] = Field(default_factory=list)
    characteristics: dict[str, list[JsonValue]] = Field(default_factory=dict)
    document_type_id: UUID | None = None
    form_schema_id: UUID | None = None
    process_version_id: UUID | None = None
    step_key: str | None = None
    confirmed: bool = False
    recommended: bool = False


class QueueRead(BaseModel):
    id: UUID
    event_type: str
    payload_version: int
    state: str
    attempts: int
    created_at: datetime
    error_code: str | None


class TimingInput(Strict):
    registry_id: UUID
    operation: Literal["search", "time_to_open"]
    duration_ms: int = Field(ge=0, le=3600000)


class NumberRead(BaseModel):
    id: UUID
    series_id: UUID
    period_key: str
    ordinal: int
    number: str | None = None
    number_string: str | None = None
    state: str
    document_id: UUID | None = None
    document_title: str | None = None
    reserved_at: datetime | None = None
    expires_at: datetime | None = None


class SeriesRead(BaseModel):
    id: UUID
    registry_id: UUID
    code: str
    name: str
    state: str
    document_type_ids: list[UUID]
    synthetic: bool


class ResultRead(BaseModel):
    id: UUID
    values: dict[str, JsonValue]
    comment: str
    attachments: list[SourcePointer | Restricted]
    result_document: dict[str, JsonValue] | None = None
    created_at: datetime


class ChecklistRead(BaseModel):
    id: UUID
    label: str
    kind: str
    required: bool
    checked: bool


class AttemptRead(BaseModel):
    id: UUID
    step_key: str
    title: str
    attempt: int
    state: str
    started_at: datetime | None
    completed_at: datetime | None
    deadline_at: datetime | None
    overdue: bool
    checklist: list[ChecklistRead]
    result: ResultRead | None


class SnapshotRead(BaseModel):
    version: int
    metadata_snapshot: dict[str, JsonValue]
    steps: list[StepRead]
    transitions: list[TransitionRead]


class ExecutionRead(BaseModel):
    id: UUID
    registry_id: UUID
    process_version_id: UUID
    name: str
    state: str
    revision: int
    created_at: datetime
    completed_at: datetime | None
    current_step_key: str | None
    current_step_execution_id: UUID | None
    deadline_at: datetime | None
    overdue: bool
    trajectory_restricted: bool
    process_snapshot: SnapshotRead | None = None
    steps: list[AttemptRead]


class ScopeRegistry(BaseModel):
    id: UUID
    name: str
    permissions: list[str]


class OperationOption(BaseModel):
    id: Operation
    label: str


class ScopeOptions(BaseModel):
    registries: list[ScopeRegistry]
    operations: list[OperationOption]


class ActionCount(BaseModel):
    action: str
    count: int


class ParserErrorCount(BaseModel):
    error_code: str
    count: int


class ContentMetrics(BaseModel):
    documents: int
    files: int
    extractions: dict[str, int]
    parser_errors: list[ParserErrorCount]


class NumberMetrics(BaseModel):
    visible_entries: int


class ExecutionMetrics(BaseModel):
    visible_instances: int


class QueueMetrics(BaseModel):
    pending: int
    failed: int


class RetrievalMetrics(BaseModel):
    requests: int
    duration_ms_total: int
    time_to_open_count: int
    time_to_open_ms_total: int


class MetricsRead(BaseModel):
    content: ContentMetrics
    usage: list[ActionCount]
    numbers: NumberMetrics
    execution: ExecutionMetrics
    queue: QueueMetrics
    retrieval: RetrievalMetrics
