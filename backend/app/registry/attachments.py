import base64
import binascii
import io
import re
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import event

from app.api.errors import fail
from app.registry.documents import file_visible, resolve_file_field
from app.registry.events import emit
from app.registry.models import DocumentFile, DocumentVersion
from app.registry.policy import field_writable
from app.storage.local import LocalFileStorage


def create_attachments(session, request, actor, document, schema, values, attachments):
    """Write originals inside the card's transaction, including required file fields."""
    proposed = deepcopy(values)
    storage = LocalFileStorage(request.app.state.settings.storage_root)
    trusted_ids, total = set(), 0
    for attachment in attachments:
        try:
            content = base64.b64decode(attachment.content_base64, validate=True)
        except (ValueError, binascii.Error):
            fail(422, "invalid_file", "Неверное содержимое файла")
        total += len(content)
        if total > 25 * 1024 * 1024:
            fail(413, "file_too_large", "Суммарный размер файлов превышает 25 МиБ")
        if not content:
            fail(422, "empty_file", "Выберите непустой файл")
        name = attachment.name.replace("\\", "/").rsplit("/", 1)[-1]
        if not name or any(ord(c) < 32 or ord(c) == 127 for c in name):
            fail(422, "invalid_filename", "Неверное имя файла")
        field, parent, index = None, None, None
        if attachment.field_key:
            field, parent, index = resolve_file_field(schema, attachment.field_key, proposed)
            if field is None or field["type"] != "file":
                fail(422, "invalid_file_field", "Поле загрузки не найдено")
            probe = type("FileProbe", (), {"field_key": attachment.field_key})()
            if not field_writable(field, actor) or not file_visible(schema, actor, probe, proposed):
                fail(403, "field_forbidden", "Загрузка в это поле не разрешена")
            if parent:
                parent_field = next(
                    f for s in schema["sections"] for f in s["fields"] if f["key"] == parent
                )
                if not field_writable(parent_field, actor):
                    fail(403, "field_forbidden", "Изменение группы не разрешено")
        stored = storage.put(uuid4().hex, io.BytesIO(content))

        def cleanup(_session, key=stored.storage_key):
            try:
                storage.delete_if_allowed(key, allowed=True)
            except OSError:
                pass

        event.listen(session, "after_rollback", cleanup, once=True)
        document.current_version += 1
        version = DocumentVersion(
            document_id=document.id, version=document.current_version, created_by=actor.id
        )
        session.add(version)
        session.flush()
        mime = attachment.mime
        if not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", mime):
            mime = "application/octet-stream"
        row = DocumentFile(
            document_id=document.id,
            version_id=version.id,
            field_key=attachment.field_key,
            name=name,
            size=stored.size,
            mime=mime,
            sha256=stored.sha256,
            storage_key=stored.storage_key,
            created_by=actor.id,
        )
        session.add(row)
        session.flush()
        trusted_ids.add(row.id)
        if field:
            target = proposed[parent][index] if parent else proposed
            if target.get(field["key"]) is not None and not isinstance(target[field["key"]], list):
                fail(422, "invalid_file_field", "Поле файла должно содержать список")
            target[field["key"]] = [*(target.get(field["key"]) or []), str(row.id)]
        emit(
            session,
            request,
            actor,
            "document.version.created",
            "document",
            document.id,
            {"version": document.current_version, "revision": document.revision},
        )
    return proposed, trusted_ids
