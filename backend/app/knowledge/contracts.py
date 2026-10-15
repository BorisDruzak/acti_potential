from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.processes.contracts import Source, Strict


class Original(Strict):
    document_id: UUID
    version_id: UUID
    file_id: UUID


class Metadata(Strict):
    name: str = Field(min_length=1, max_length=300)
    purpose: str = Field(default="", max_length=10000)
    category: str = Field(default="", max_length=300)
    owner_id: UUID | None = None
    visibility: Literal["registry", "private"] = "registry"
    document_type_id: UUID | None = None
    form_schema_id: UUID | None = None
    process_version_id: UUID | None = None
    step_key: str | None = Field(default=None, max_length=100)
    characteristics: dict[
        str, list[Annotated[str, Field(max_length=1000)] | int | float | bool]
    ] = Field(default_factory=dict, max_length=50)

    @model_validator(mode="after")
    def bounded(self):
        if any(len(key) > 100 or len(values) > 100 for key, values in self.characteristics.items()):
            raise ValueError("Characteristics exceed limits")
        return self


class TemplateInput(Metadata):
    registry_id: UUID
    original: Original


class VersionInput(Metadata):
    revision: int = Field(ge=1)
    original: Original


class CaseInput(Metadata):
    registry_id: UUID
    description: str = Field(max_length=20000)
    result: str = Field(max_length=20000)
    links: list[Source] = Field(default_factory=list, max_length=100)


class CaseUpdate(Metadata):
    revision: int = Field(ge=1)
    description: str = Field(max_length=20000)
    result: str = Field(max_length=20000)
    links: list[Source] = Field(default_factory=list, max_length=100)


class CurationInput(Strict):
    action: Literal["confirm", "recommend", "unrecommend"]
    reason: str = Field(min_length=1, max_length=2000)


class AccessInput(Strict):
    visibility: Literal["registry", "private"]
    lifecycle: Literal["active", "archived"]
    reason: str = Field(min_length=1, max_length=2000)


class UsageInput(Strict):
    action: Literal["template_open", "case_open", "basis_follow"]
    template_version_id: UUID | None = None
    case_id: UUID | None = None


class SearchInput(Strict):
    registry_id: UUID | None = None
    text: str = Field(default="", max_length=255)
    category: str | None = Field(default=None, max_length=300)
    document_type_id: UUID | None = None
    form_schema_id: UUID | None = None
    process_version_id: UUID | None = None
    step_key: str | None = Field(default=None, max_length=100)
    recommended: bool | None = None
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=100)
    mode: Literal["editor", "viewer"] | None = None
