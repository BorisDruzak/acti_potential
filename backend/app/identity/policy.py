from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Actor:
    id: UUID
    display_name: str
    roles: frozenset[str]
    must_change_password: bool = False
    scoped_registry_ids: frozenset[str] | None = None
    scoped_operations: frozenset[str] | None = None


class PermissionDenied(Exception):
    pass


def require_role(actor: Actor, role: str) -> None:
    if role not in actor.roles:
        raise PermissionDenied()
