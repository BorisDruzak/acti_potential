from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Process(Base):
    __tablename__ = "processes"
    __table_args__ = (UniqueConstraint("registry_id", "code", name="uq_processes_registry_code"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    metadata_data: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessVersion(Base):
    __tablename__ = "process_versions"
    __table_args__ = (
        UniqueConstraint("process_id", "version", name="uq_process_versions_process_version"),
        CheckConstraint("state IN ('draft','published')", name="process_version_state"),
        CheckConstraint("version > 0 AND revision > 0", name="process_version_positive"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    process_id: Mapped[UUID] = mapped_column(ForeignKey("processes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="draft")
    effective_from: Mapped[date] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    metadata_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessStep(Base):
    __tablename__ = "process_steps"
    __table_args__ = (UniqueConstraint("version_id", "key", name="uq_process_steps_version_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("process_versions.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB)


class ProcessTransition(Base):
    __tablename__ = "process_transitions"
    __table_args__ = (
        UniqueConstraint("version_id", "key", name="uq_process_transitions_version_key"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("process_versions.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB)


class StepSource(Base):
    __tablename__ = "process_step_sources"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("process_versions.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB)


class StepTemplate(Base):
    __tablename__ = "process_step_templates"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("process_versions.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB)
