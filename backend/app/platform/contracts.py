SCHEMA_EVENTS = frozenset(
    {
        "form.schema.published",
        "form.schema.created",
        "form.schema.updated",
        "registry.created",
        "registry.updated",
        "document_type.created",
        "document_type.updated",
        "reference.created",
        "reference.updated",
        "reference.item.created",
        "reference.item.updated",
        "reference.imported",
        "reference.exported",
        "document.exported",
        "document.created",
        "document.metadata.updated",
        "document.version.created",
        "document.access.changed",
        "document.file.downloaded",
    }
)

EVENT_FIELDS = {
    name: frozenset({"revision", "version", "registry_id", "schema_version_id"})
    for name in SCHEMA_EVENTS
}
EVENT_FIELDS["platform.admin_created"] = frozenset({"user_id"})
for _event in ("reference.item.created", "reference.item.updated"):
    EVENT_FIELDS[_event] = EVENT_FIELDS[_event] | {"reference_set_id"}
for _event in ("series.created", "series.updated", "grants.updated", "reserved", "assigned",
               "cancelled", "expired", "void", "exported"):
    EVENT_FIELDS["number." + _event] = frozenset({"series_id"})
for _event in ("requested", "completed", "status"):
    EVENT_FIELDS["content.extraction." + _event] = frozenset({"file_id"})
for _event in ("proposed", "confirmed", "rejected"):
    EVENT_FIELDS["relation." + _event] = frozenset({"source_document_id", "target_document_id"})
for _event in ("created", "updated", "version.created", "version.updated", "version.published"):
    EVENT_FIELDS["process." + _event] = frozenset({"registry_id", "process_version_id"})
for _event in ("template.created", "template.version.created", "template.version.updated", "template.version.published", "case.created", "case.updated", "case.published", "case.curated", "access.changed"):
    EVENT_FIELDS["knowledge." + _event] = frozenset({"registry_id"})


def validate_payload(event_type, payload):
    allowed = EVENT_FIELDS.get(event_type)
    if allowed is None or set(payload) - allowed:
        raise ValueError("Outbox payload contains unsupported fields or event type")
    for key, value in payload.items():
        if key in {"revision", "version"}:
            if type(value) is not int or value < 1:
                raise ValueError("Invalid event version")
        else:
            from uuid import UUID

            UUID(str(value))
