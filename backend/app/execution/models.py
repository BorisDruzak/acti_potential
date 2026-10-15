from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProcessInstance(Base):
    __tablename__ = "execution_instances"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending','active','suspended','completed','cancelled') AND revision > 0",
            name="execution_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), index=True)
    process_version_id: Mapped[UUID] = mapped_column(ForeignKey("process_versions.id"))
    name: Mapped[str] = mapped_column(String(300))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    current_execution_id: Mapped[UUID | None] = mapped_column()
    path: Mapped[list] = mapped_column(JSONB, default=list)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StepExecution(Base):
    __tablename__ = "step_executions"
    __table_args__ = (
        UniqueConstraint("instance_id", "step_key", "attempt", name="uq_step_execution_attempt"),
        CheckConstraint(
            "state IN ('pending','active','completed','returned') AND attempt > 0",
            name="step_execution_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    instance_id: Mapped[UUID] = mapped_column(ForeignKey("execution_instances.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    assignee_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    result_id: Mapped[UUID | None] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StepResult(Base):
    __tablename__ = "step_results"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    step_execution_id: Mapped[UUID] = mapped_column(ForeignKey("step_executions.id"), index=True)
    values: Mapped[dict] = mapped_column(JSONB)
    comment: Mapped[str] = mapped_column(String(20000))
    attachments: Mapped[list] = mapped_column(JSONB)
    result_document: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChecklistItem(Base):
    __tablename__ = "execution_checklist"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    step_execution_id: Mapped[UUID] = mapped_column(ForeignKey("step_executions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    checked: Mapped[bool] = mapped_column(Boolean, default=False)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    instance_id: Mapped[UUID] = mapped_column(ForeignKey("execution_instances.id"), index=True)
    step_execution_id: Mapped[UUID | None] = mapped_column(ForeignKey("step_executions.id"))
    action: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
