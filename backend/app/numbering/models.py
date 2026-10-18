from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NumberSeries(Base):
    __tablename__ = "number_series"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), index=True)
    document_type_ids: Mapped[list] = mapped_column(JSONB, default=list)
    organization_scope: Mapped[str] = mapped_column(String(255), default="")
    period_mode: Mapped[str] = mapped_column(String(20), default="annual")
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    boundary_month: Mapped[int] = mapped_column(Integer, default=1)
    boundary_day: Mapped[int] = mapped_column(Integer, default=1)
    current_period: Mapped[str | None] = mapped_column(String(32))
    start: Mapped[int] = mapped_column(BigInteger, default=1)
    step: Mapped[int] = mapped_column(Integer, default=1)
    prefix: Mapped[str] = mapped_column(String(50), default="")
    suffix: Mapped[str] = mapped_column(String(50), default="")
    padding: Mapped[int] = mapped_column(Integer, default=4)
    format: Mapped[str] = mapped_column(String(160), default="{prefix}{ordinal}{suffix}")
    state: Mapped[str] = mapped_column(String(20), default="active")
    max_batch: Mapped[int] = mapped_column(Integer, default=20)
    reservation_hours: Mapped[int | None] = mapped_column(Integer().evaluates_none(), default=24)
    max_reservation_hours: Mapped[int] = mapped_column(Integer, default=8760)
    allow_indefinite: Mapped[bool] = mapped_column(Boolean, default=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class NumberGrant(Base):
    __tablename__ = "number_grants"
    series_id: Mapped[UUID] = mapped_column(ForeignKey("number_series.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    permissions: Mapped[list] = mapped_column(JSONB, default=list)


class NumberCounter(Base):
    __tablename__ = "number_counters"
    series_id: Mapped[UUID] = mapped_column(ForeignKey("number_series.id"), primary_key=True)
    period_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    next_ordinal: Mapped[int] = mapped_column(BigInteger)


class NumberRequest(Base):
    __tablename__ = "number_requests"
    series_id: Mapped[UUID] = mapped_column(ForeignKey("number_series.id"), primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    entry_ids: Mapped[list] = mapped_column(JSONB)
    response: Mapped[dict] = mapped_column(JSONB)


class NumberEntry(Base):
    __tablename__ = "number_entries"
    __table_args__ = (
        UniqueConstraint("series_id", "period_key", "ordinal", name="uq_number_entry_ordinal"),
        UniqueConstraint(
            "series_id", "period_key", "number_string", name="uq_number_entry_rendering"
        ),
        Index("uq_number_entries_reserved_document", "document_id", unique=True,
              postgresql_where=text("state='reserved'")),
        CheckConstraint(
            "state IN ('reserved','assigned','cancelled','expired','void')", name="number_state"
        ),
        CheckConstraint("ordinal >= 1", name="positive_ordinal"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    series_id: Mapped[UUID] = mapped_column(ForeignKey("number_series.id"), index=True)
    period_key: Mapped[str] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column(BigInteger)
    number_string: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), index=True)


class NumberOperation(Base):
    __tablename__ = "number_operations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    series_id: Mapped[UUID] = mapped_column(ForeignKey("number_series.id"), index=True)
    entry_id: Mapped[UUID] = mapped_column(ForeignKey("number_entries.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str] = mapped_column(String(1000), default="")
    source: Mapped[str] = mapped_column(String(30), default="api")
    idempotency_key: Mapped[str] = mapped_column(String(120))


class RegistryNumberClaim(Base):
    """Occupied registry numbers, including retained cards and spent reservations."""

    __tablename__ = "registry_number_claims"
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), primary_key=True)
    number_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), index=True)
    number_entry_id: Mapped[UUID | None] = mapped_column(ForeignKey("number_entries.id"), unique=True)
