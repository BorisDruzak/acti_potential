from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Key = str


class ProcessInput(Strict):
    registry_id: UUID
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=10000)
    owner_id: UUID | None = None
    department: str = Field(default="", max_length=300)
    category: str = Field(default="", max_length=300)
    purpose: str = Field(default="", max_length=10000)
    status: Literal["active", "archived"] = "active"


class ProcessPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    owner_id: UUID | None = None
    department: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=300)
    purpose: str | None = Field(default=None, max_length=10000)
    status: Literal["active", "archived"] | None = None


class Source(Strict):
    document_id: UUID
    version_id: UUID | None = None
    block_id: UUID | None = None
    note: str = Field(default="", max_length=2000)


class Template(Source):
    version_id: UUID
    file_id: UUID | None = None


class Step(Strict):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    title: str = Field(min_length=1, max_length=300)
    instruction: str = Field(default="", max_length=20000)
    executor_role: str = Field(default="", max_length=300)
    department: str = Field(default="", max_length=300)
    duration_hours: float | None = Field(default=None, ge=0, le=100000, allow_inf_nan=False)
    deadline: str = Field(default="", max_length=2000)
    inputs: list[str] = Field(default_factory=list, max_length=100)
    outputs: list[str] = Field(default_factory=list, max_length=100)
    system: str = Field(default="", max_length=300)
    control_note: str = Field(default="", max_length=2000)
    is_start: bool = False
    is_terminal: bool = False
    form_schema_id: UUID | None = None
    sources: list[Source] = Field(default_factory=list, max_length=50)
    templates: list[Template] = Field(default_factory=list, max_length=50)


class Transition(Strict):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    from_step: str = Field(max_length=100)
    to_step: str = Field(max_length=100)
    label: str = Field(default="", max_length=300)
    condition: dict | None = None
    is_else: bool = False


class DraftInput(Strict):
    revision: int = Field(ge=1)
    effective_from: date
    steps: list[Step] = Field(default_factory=list, max_length=200)
    transitions: list[Transition] = Field(default_factory=list, max_length=500)


class RevisionInput(Strict):
    revision: int = Field(ge=1)


class PreviewInput(Strict):
    step_key: str = Field(max_length=100)
    values: dict = Field(default_factory=dict, max_length=200)
