from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ContentExtraction(Base):
    __tablename__ = "content_extractions"
    __table_args__ = (
        UniqueConstraint("fingerprint"),
        CheckConstraint(
            "state IN ('pending','running','completed','no_text','unsupported','encrypted','failed')",
            name="content_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("document_files.id"), index=True)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    parser: Mapped[str] = mapped_column(String(30))
    parser_version: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    full_text: Mapped[str] = mapped_column(Text, default="", deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentBlock(Base):
    __tablename__ = "document_blocks"
    __table_args__ = (
        UniqueConstraint("extraction_id", "ordinal", name="uq_content_block_order"),
        Index("ix_document_blocks_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_document_blocks_trigram",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    extraction_id: Mapped[UUID] = mapped_column(ForeignKey("content_extractions.id"), index=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_blocks.id"))
    page: Mapped[int | None] = mapped_column(Integer)
    anchor: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    table_data: Mapped[list | None] = mapped_column("table_data", JSONB)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('russian'::regconfig, text)", persisted=True)
    )


class MetadataFragment(Base):
    __tablename__ = "content_metadata_fragments"
    __table_args__ = (
        UniqueConstraint("document_id", "field_key", name="uq_content_metadata_field"),
        Index("ix_metadata_fragments_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_metadata_fragments_trigram",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    document_revision: Mapped[int] = mapped_column(Integer)
    field_key: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('russian'::regconfig, text)", persisted=True)
    )


class DocumentIndexState(Base):
    __tablename__ = "content_index_states"
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    document_revision: Mapped[int] = mapped_column(Integer)
    index_version: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkRecord(Base):
    """Compact source map only; text remains in immutable DocumentBlock."""

    __tablename__ = "content_chunks"
    __table_args__ = (UniqueConstraint("extraction_id", "algorithm_version", "ordinal"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    extraction_id: Mapped[UUID] = mapped_column(ForeignKey("content_extractions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    ranges: Mapped[list] = mapped_column(JSONB)
    block_ids: Mapped[list] = mapped_column(JSONB)
    page: Mapped[int | None] = mapped_column(Integer)
    anchor: Mapped[str] = mapped_column(String(255))
    checksum: Mapped[str] = mapped_column(String(64))
    algorithm_version: Mapped[str] = mapped_column(String(30))


# Fixed-size fingerprint avoids btree tuple limits for long metadata. Exact
# normalized text equality is always rechecked after the indexed hash lookup.
MetadataFragment.__table__.append_constraint(
    Index(
        "ix_metadata_fragments_number_fingerprint",
        func.md5(
            func.regexp_replace(
                func.replace(
                    func.lower(MetadataFragment.text), literal_column("'ё'"), literal_column("'е'")
                ),
                literal_column("'[^[:alnum:]]'"),
                literal_column("''"),
                literal_column("'g'"),
            )
        ),
    )
)
