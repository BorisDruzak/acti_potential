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
