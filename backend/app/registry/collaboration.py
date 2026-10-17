"""Advisory tab presence; document revision checks remain the write authority."""

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.api.dependencies import COOKIE_NAME
from app.api.errors import fail
from app.identity.models import AuthSession
from app.identity.service import AuthenticationError
from app.identity.sessions import resolve_actor_by_hash, token_digest
from app.registry.documents import get_document, latest_editors
from app.registry.policy import document_permissions

PRESENCE_SECONDS = 45


def _session_actor(session, identity, now):
    auth = session.get(AuthSession, identity)
    if auth is None:
        return None
    try:
        actor = resolve_actor_by_hash(session, auth.token_hash, now)
    except AuthenticationError:
        return None
    return None if actor.must_change_password else actor


def collaboration_state(session, request, actor, document, mode, heartbeat=None):
    # Presence is bound to an authenticated interactive browser session, never a
    # service principal or a caller supplied user identifier.
    token = request.cookies.get(COOKIE_NAME)
    auth = session.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(token))) if token else None
    now = datetime.now(UTC)
    if auth is None or auth.user_id != actor.id or _session_actor(session, auth.id, now) is None:
        fail(403, "interactive_session_required", "Нужна действующая пользовательская сессия")
    if actor.scoped_registry_ids is not None or actor.scoped_operations is not None:
        fail(403, "interactive_session_required", "Нужна пользовательская сессия")
    effective_mode = mode or ("editor" if actor.roles & {"admin", "editor"} else "viewer")
    redis = request.app.state.redis
    prefix = f"acti:collaboration:{document.id}:"
    if heartbeat is not None:
        if heartbeat.editing and (effective_mode != "editor" or "update" not in document_permissions(session, actor, document)):
            fail(403, "forbidden", "Редактирование документа не разрешено")
        redis.setex(prefix + str(auth.id) + ":" + str(heartbeat.client_id), PRESENCE_SECONDS, json.dumps({
            "session_id": str(auth.id), "mode": effective_mode, "editing": heartbeat.editing,
        }))
    keys = list(redis.scan_iter(match=prefix + "*"))
    participants = {}
    for key, raw in zip(keys, redis.mget(keys) if keys else []):
        if raw is None:
            continue
        try:
            value = json.loads(raw)
            participant = _session_actor(session, UUID(value["session_id"]), now)
            if participant is None or value["mode"] not in {"viewer", "editor"}:
                continue
            current = get_document(session, participant, document.id, mode=value["mode"])
            editing = bool(value["editing"] and value["mode"] == "editor" and "update" in document_permissions(session, participant, current))
        except (KeyError, ValueError, TypeError, HTTPException):
            continue
        previous = participants.get(participant.id)
        participants[participant.id] = {
            "id": participant.id, "display_name": participant.display_name,
            "editing": editing or bool(previous and previous["editing"]),
        }
    return {
        "revision": document.revision, "updated_at": document.updated_at,
        "updated_by": latest_editors(session, [document]).get(document.id),
        "participants": sorted(participants.values(), key=lambda item: (item["display_name"], str(item["id"]))),
    }
