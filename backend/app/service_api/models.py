from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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


class ServiceAccount(Base):
    __tablename__ = "service_accounts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20), default="active")
    registry_ids: Mapped[list] = mapped_column(JSONB)
    operations: Mapped[list] = mapped_column(JSONB)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServiceToken(Base):
    __tablename__ = "service_tokens"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("service_accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(30))
    registry_ids: Mapped[list] = mapped_column(JSONB)
    operations: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceEventCursor(Base):
    __tablename__ = "service_event_cursors"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_id: Mapped[UUID] = mapped_column(ForeignKey("service_tokens.id"), index=True)
    scope_hash: Mapped[str] = mapped_column(String(64))
    after_ordinal: Mapped[int] = mapped_column(BigInteger, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ApiUsageBucket(Base):
    __tablename__ = "api_usage_buckets"
    __table_args__ = (
        UniqueConstraint("day", "registry_id", "operation", name="uq_api_usage_bucket"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    day: Mapped[date] = mapped_column(Date)
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    requests: Mapped[int] = mapped_column(Integer)
    duration_ms_total: Mapped[int] = mapped_column(Integer)
