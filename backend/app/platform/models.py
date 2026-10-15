from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    object_type: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[UUID | None]
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str] = mapped_column(String(64))
    request_source: Mapped[str] = mapped_column(String(64))


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(80))
    payload_version: Mapped[int] = mapped_column(Integer, default=1)
    object_id: Mapped[UUID]
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    consumer: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("outbox_events.id"), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
