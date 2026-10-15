from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentRelation(Base):
    __tablename__ = "document_relations"
    __table_args__ = (
        CheckConstraint("state IN ('proposed','confirmed','rejected')", name="relation_state"),
        CheckConstraint(
            "relation_type IN ('changes','repeals','replaces','approves','attachment','reference','in_execution','previous_revision','topic')",
            name="relation_type",
        ),
        CheckConstraint(
            "source IN ('manual','deterministic','imported','ai_suggested')", name="relation_source"
        ),
        CheckConstraint(
            "state = 'proposed' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL AND effective_date IS NOT NULL AND reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="relation_decision",
        ),
        CheckConstraint(
            "source_document_id <> target_document_id OR relation_type = 'previous_revision'",
            name="relation_self",
        ),
        CheckConstraint(
            "(target_document_id IS NULL) <> (external_target IS NULL)", name="relation_target"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    source_document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    target_document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), index=True)
    source_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_versions.id"))
    target_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_versions.id"))
    source_block_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_blocks.id"))
    target_block_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_blocks.id"))
    relation_type: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    external_target: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)


class RelationEvent(Base):
    __tablename__ = "relation_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    relation_id: Mapped[UUID] = mapped_column(ForeignKey("document_relations.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    effective_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text, default="")
