from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Template(Base):
    __tablename__ = "knowledge_templates"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private','registry') AND lifecycle IN ('active','archived')",
            name="template_access",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    visibility: Mapped[str] = mapped_column(String(20), default="registry")
    lifecycle: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TemplateVersion(Base):
    __tablename__ = "knowledge_template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_template_versions_template_version"),
        CheckConstraint(
            "state IN ('draft','published') AND revision > 0 AND version > 0",
            name="template_version_state",
        ),
        Index("ix_knowledge_template_versions_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_knowledge_template_versions_trigram",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    template_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_templates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(20), default="draft")
    data: Mapped[dict] = mapped_column(JSONB)
    text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('russian'::regconfig, text)", persisted=True)
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PracticeCase(Base):
    __tablename__ = "practice_cases"
    __table_args__ = (
        CheckConstraint(
            "state IN ('draft','published') AND revision > 0", name="practice_case_state"
        ),
        CheckConstraint(
            "visibility IN ('private','registry') AND lifecycle IN ('active','archived')",
            name="practice_case_access",
        ),
        Index("ix_practice_cases_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_practice_cases_trigram",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    visibility: Mapped[str] = mapped_column(String(20), default="registry")
    lifecycle: Mapped[str] = mapped_column(String(20), default="active")
    state: Mapped[str] = mapped_column(String(20), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSONB)
    text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('russian'::regconfig, text)", persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PracticeLink(Base):
    __tablename__ = "practice_links"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("practice_cases.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB)


class KnowledgeCuration(Base):
    __tablename__ = "knowledge_curations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID | None] = mapped_column(ForeignKey("practice_cases.id"), index=True)
    template_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_templates.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeUsage(Base):
    __tablename__ = "knowledge_usage"
    __table_args__ = (
        UniqueConstraint(
            "day", "registry_id", "object_id", "action", name="uq_knowledge_usage_bucket"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    day: Mapped[date] = mapped_column(Date)
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), index=True)
    object_id: Mapped[UUID] = mapped_column()
    action: Mapped[str] = mapped_column(String(30))
    count: Mapped[int] = mapped_column(Integer)
