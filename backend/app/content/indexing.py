import hashlib
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.content.models import ContentExtraction, DocumentIndexState, MetadataFragment
from app.registry.documents import get_schema, raw_values
from app.registry.models import Document, DocumentFile, FormSchema, ReferenceItem, ReferenceSet


def text_fields(schema, values, session=None):
    """Flatten only declared text fields; callers redact the values before querying."""
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            key, value = field["key"], values.get(field["key"])
            if field.get("searchable") is False:
                continue
            if (
                field["type"] in {"text", "textarea", "date", "datetime"}
                and isinstance(value, str)
                and value.strip()
            ):
                yield key, field["label"], value
            elif (
                field["type"] in {"reference", "multi_reference", "department"}
                and field.get("searchable")
                and session is not None
            ):
                identities = value if isinstance(value, list) else [value]
                labels = []
                for identity in identities:
                    if identity is not None:
                        item = session.get(ReferenceItem, UUID(str(identity)))
                        if item is not None:
                            labels.append(item.label)
                if labels:
                    yield key, field["label"], "; ".join(labels)
            elif field["type"] == "group" and isinstance(value, list):
                for index, row in enumerate(value):
                    if not isinstance(row, dict):
                        continue
                    for child_key, label, entry in text_fields(
                        {"sections": [{"fields": field.get("children", [])}]}, row, session
                    ):
                        yield f"{key}.{index}.{child_key}", label, entry


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
    if document.deleted_at is not None:
        session.execute(delete(MetadataFragment).where(MetadataFragment.document_id == document.id))
        if state is None:
            state = DocumentIndexState(document_id=document.id, document_revision=document.revision)
            session.add(state)
        state.document_revision = document.revision
        state.index_version, state.indexed_at = 2, datetime.now(UTC)
        session.flush()
        return
    if state and state.document_revision == document.revision and state.index_version == 2:
        return
    schema = get_schema(session, document)
    session.execute(delete(MetadataFragment).where(MetadataFragment.document_id == document.id))
    for key, label, value in text_fields(schema, raw_values(document, schema), session):
        session.add(
            MetadataFragment(
                id=uuid5(NAMESPACE_URL, f"acti:metadata:{document.id}:{document.revision}:{key}"),
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
    state.index_version = 2
    session.flush()


def queue_file(session, file, *, retry=False):
    from app.content.parsers import parser_identity

    document = session.get(Document, file.document_id)
    if document is None or document.deleted_at is not None:
        return None

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
    if event.event_type == "document.deleted":
        index_metadata(session, event.object_id)
        return
    if event.event_type in {
        "reference.item.updated",
        "reference.imported",
        "reference.updated",
        "form.schema.published",
    }:
        schemas = []
        if event.event_type == "form.schema.published":
            schemas = [event.object_id]
        else:
            reference_id = event.payload.get("reference_set_id", str(event.object_id))
            reference = session.get(ReferenceSet, UUID(reference_id))
            for schema in session.scalars(
                select(FormSchema).where(FormSchema.state == "published")
            ):

                def uses(fields):
                    return any(
                        (
                            field.get("searchable")
                            and (
                                str(field.get("reference_set_id")) == reference_id
                                or reference is not None
                                and field.get("reference_code") == reference.code
                            )
                        )
                        or uses(field.get("children", []))
                        for field in fields
                    )

                if uses(
                    [f for s in schema.schema_data.get("sections", []) for f in s.get("fields", [])]
                ):
                    schemas.append(schema.id)
        if schemas:
            session.execute(
                update(DocumentIndexState)
                .where(
                    DocumentIndexState.document_id.in_(
                        select(Document.id).where(Document.schema_version_id.in_(schemas))
                    )
                )
                .values(index_version=0)
            )
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
