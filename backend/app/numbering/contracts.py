from datetime import date
from string import Formatter
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

PERMISSIONS = frozenset({"view", "reserve", "assign", "cancel", "void"})


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SeriesInput(Input):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    registry_id: UUID
    document_type_ids: list[UUID] = Field(default_factory=list, max_length=100)
    organization_scope: str = Field(default="", max_length=255)
    period_mode: Literal["annual", "manual"] = "annual"
    timezone: str = Field(default="UTC", max_length=100)
    boundary_month: int = Field(default=1, ge=1, le=12)
    boundary_day: int = Field(default=1, ge=1, le=31)
    current_period: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    start: int = Field(default=1, ge=1, le=10**12)
    step: int = Field(default=1, ge=1, le=1000000)
    prefix: str = Field(default="", max_length=50)
    suffix: str = Field(default="", max_length=50)
    padding: int = Field(default=4, ge=1, le=18)
    format: str = Field(default="{prefix}{ordinal}{suffix}", max_length=160)
    state: Literal["active", "blocked"] = "active"
    max_batch: int = Field(default=20, ge=1, le=100)
    reservation_hours: int | None = Field(default=24, ge=1, le=87600)
    max_reservation_hours: int = Field(default=8760, ge=1, le=87600)
    allow_indefinite: StrictBool = False
    synthetic: StrictBool = True

    @model_validator(mode="after")
    def valid_configuration(self):
        try:
            ZoneInfo(self.timezone)
            date(2025, self.boundary_month, self.boundary_day)
            parts = list(Formatter().parse(self.format))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Invalid timezone, annual boundary or format") from None
        placeholders = [name for _, name, _, _ in parts if name is not None]
        if placeholders.count("ordinal") != 1 or any(
            name not in {"ordinal", "period", "prefix", "suffix", "code"} or spec or conversion
            for _, name, spec, conversion in parts
            if name is not None
        ):
            raise ValueError("Format needs one ordinal and only approved simple placeholders")
        if (
            self.period_mode == "manual"
            and not self.current_period
            or self.reservation_hours is None
            and not self.allow_indefinite
            or self.reservation_hours is not None
            and self.reservation_hours > self.max_reservation_hours
        ):
            raise ValueError("Invalid period or reservation policy")
        if any(ord(c) < 32 for c in self.prefix + self.suffix + self.format):
            raise ValueError("Number formats cannot contain control characters")
        return self


class SeriesPatch(Input):
    # Validate a merged SeriesInput so defaults never replace omitted settings.
    code: str | None = None
    name: str | None = None
    description: str | None = None
    registry_id: UUID | None = None
    document_type_ids: list[UUID] | None = None
    organization_scope: str | None = None
    period_mode: str | None = None
    timezone: str | None = None
    boundary_month: int | None = None
    boundary_day: int | None = None
    current_period: str | None = None
    start: int | None = None
    step: int | None = None
    prefix: str | None = None
    suffix: str | None = None
    padding: int | None = None
    format: str | None = None
    state: str | None = None
    max_batch: int | None = None
    reservation_hours: int | None = None
    max_reservation_hours: int | None = None
    allow_indefinite: StrictBool | None = None
    synthetic: StrictBool | None = None


class GrantInput(Input):
    user_id: UUID
    permissions: list[Literal["view", "reserve", "assign", "cancel", "void"]] = Field(max_length=5)


class GrantsInput(Input):
    entries: list[GrantInput] = Field(max_length=200)


class ReserveInput(Input):
    count: int = Field(default=1, ge=1, le=100)
    reservation_hours: int | None = Field(default=None, ge=1, le=87600)
    idempotency_key: str = Field(min_length=1, max_length=120)


class AssignInput(Input):
    document_id: UUID
    revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=120)


class NextNumberInput(Input):
    document_type_id: UUID
    schema_version_id: UUID | None = None
    document_id: UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TransitionInput(Input):
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=120)
