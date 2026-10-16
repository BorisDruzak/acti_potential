from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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


class Registry(Base):
    __tablename__ = "registries"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    document_type_ids: Mapped[list] = mapped_column(JSONB, default=list)


class RegistryAccess(Base):
    __tablename__ = "registry_access"
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    permissions: Mapped[list] = mapped_column(JSONB, default=list)


class DocumentType(Base):
    __tablename__ = "document_types"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")


class FormSchema(Base):
    __tablename__ = "form_schemas"
    __table_args__ = (
        UniqueConstraint("document_type_id", "version"),
        CheckConstraint("state IN ('draft', 'published')", name="schema_state"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_type_id: Mapped[UUID] = mapped_column(ForeignKey("document_types.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="draft")
    schema_data: Mapped[dict] = mapped_column("schema", JSONB, default=dict)


class ReferenceSet(Base):
    __tablename__ = "reference_sets"
    __table_args__ = (
        UniqueConstraint("registry_id", "code"),
        Index(
            "uq_reference_sets_global_code",
            "code",
            unique=True,
            postgresql_where=text("registry_id IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    registry_id: Mapped[UUID | None] = mapped_column(ForeignKey("registries.id"), index=True)


class ReferenceItem(Base):
    __tablename__ = "reference_items"
    __table_args__ = (
        UniqueConstraint("reference_set_id", "label"),
        UniqueConstraint("source_database", "source_entity", "source_id"),
        Index(
            "uq_reference_item_code",
            "reference_set_id",
            "code",
            unique=True,
            postgresql_where=text("code <> ''"),
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_from <= valid_until",
            name="reference_validity",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    reference_set_id: Mapped[UUID] = mapped_column(ForeignKey("reference_sets.id"), index=True)
    label: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(100), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("reference_items.id"), index=True)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    item_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    source_database: Mapped[str | None] = mapped_column(String(100))
    source_entity: Mapped[str | None] = mapped_column(String(100))
    source_id: Mapped[str | None] = mapped_column(String(255))
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_database", "source_entity", "source_id"),
        Index("ix_documents_registry_date_id", "registry_id", "reg_date", "id"),
        Index("ix_documents_custom_data", "custom_data", postgresql_using="gin"),
        Index("ix_documents_publication_targets", "publication_targets", postgresql_using="gin"),
        CheckConstraint("card_state IN ('draft','published')", name="document_card_state"),
        CheckConstraint("jsonb_typeof(publication_targets) = 'array' AND publication_targets <@ '[\"internal\",\"shared\"]'::jsonb AND jsonb_array_length(publication_targets) <= 2", name="document_publication_targets"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    registry_id: Mapped[UUID] = mapped_column(ForeignKey("registries.id"), index=True)
    document_type_id: Mapped[UUID] = mapped_column(ForeignKey("document_types.id"), index=True)
    schema_version_id: Mapped[UUID] = mapped_column(ForeignKey("form_schemas.id"), index=True)
    reg_number: Mapped[str | None] = mapped_column(String(255), index=True)
    reg_date: Mapped[date | None] = mapped_column(Date, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(100), index=True)
    card_state: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft", index=True)
    publication_targets: Mapped[list] = mapped_column(JSONB, default=lambda: ["internal"], server_default=text("'[\"internal\"]'::jsonb"))
    custom_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_database: Mapped[str | None] = mapped_column(String(100))
    source_entity: Mapped[str | None] = mapped_column(String(100))
    source_id: Mapped[str | None] = mapped_column(String(255))
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))


class MetadataRevision(Base):
    __tablename__ = "metadata_revisions"
    __table_args__ = (UniqueConstraint("document_id", "revision"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    values: Mapped[dict] = mapped_column(JSONB)
    card_state: Mapped[str | None] = mapped_column(String(20))
    publication_targets: Mapped[list | None] = mapped_column(JSONB)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentFile(Base):
    __tablename__ = "document_files"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)
    field_key: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentACL(Base):
    __tablename__ = "document_acl"
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    permissions: Mapped[list] = mapped_column(JSONB, default=list)
