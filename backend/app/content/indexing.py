import hashlib
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.content.models import ContentExtraction, DocumentIndexState, MetadataFragment
from app.registry.documents import get_schema, raw_values
from app.registry.models import Document, DocumentFile


def text_fields(schema, values):
    """Flatten only declared text fields; callers redact the values before querying."""
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            key, value = field["key"], values.get(field["key"])
            if field["type"] in {"text", "textarea"} and isinstance(value, str) and value.strip():
                yield key, field["label"], value
            elif field["type"] == "group" and isinstance(value, list):
                for index, row in enumerate(value):
                    if not isinstance(row, dict):
                        continue
                    for child in field.get("children", []):
                        entry = row.get(child["key"])
                        if (
                            child["type"] in {"text", "textarea"}
                            and isinstance(entry, str)
                            and entry.strip()
                        ):
                            yield f"{key}.{index}.{child['key']}", child["label"], entry


def index_metadata(session, document_id):
    document = session.scalar(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if document is None:
        return
    state = session.get(DocumentIndexState, document.id)
    if state and state.document_revision == document.revision:
        return
    schema = get_schema(session, document)
    session.execute(delete(MetadataFragment).where(MetadataFragment.document_id == document.id))
    for key, label, value in text_fields(schema, raw_values(document, schema)):
        session.add(
            MetadataFragment(
                document_id=document.id,
                document_revision=document.revision,
                field_key=key,
                label=label,
                text=value,
            )
        )
    if state is None:
        state = DocumentIndexState(document_id=document.id, document_revision=document.revision)
        session.add(state)
    state.document_revision, state.indexed_at = document.revision, datetime.now(UTC)
    session.flush()


def queue_file(session, file, *, retry=False):
    from app.content.parsers import parser_identity

    parser, version = parser_identity(file.name, file.mime)
    fingerprint = hashlib.sha256(f"{file.id}:{file.sha256}:{parser}:{version}".encode()).hexdigest()
    identity = uuid5(NAMESPACE_URL, "acti:extraction:" + fingerprint)
    session.execute(
        insert(ContentExtraction)
        .values(
            id=identity,
            document_id=file.document_id,
            file_id=file.id,
            version_id=file.version_id,
            sha256=file.sha256,
            fingerprint=fingerprint,
            parser=parser,
            parser_version=version,
            state="pending",
            attempts=0,
            warnings=[],
            full_text="",
        )
        .on_conflict_do_nothing(index_elements=["fingerprint"])
    )
    row = session.get(ContentExtraction, identity)
    if retry and row.state == "failed" and row.attempts < 3:
        row.state, row.error_code, row.next_attempt_at, row.lease_until = (
            "pending",
            None,
            datetime.now(UTC),
            None,
        )
    return row


def handle_event(session, event):
    if event.event_type in {
        "document.created",
        "document.metadata.updated",
        "document.version.created",
    }:
        index_metadata(session, event.object_id)
        for file in session.scalars(
            select(DocumentFile).where(DocumentFile.document_id == event.object_id)
        ):
            queue_file(session, file)
