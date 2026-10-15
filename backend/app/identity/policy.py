from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Actor:
    id: UUID
    display_name: str
    roles: frozenset[str]


class PermissionDenied(Exception):
    pass


def require_role(actor: Actor, role: str) -> None:
    if role not in actor.roles:
        raise PermissionDenied()
