"""Who is calling, whatever door the request came in through (CONVENTIONS.md §5).

Doors only *identify*: an HTTP route (cookie or Bearer token), the LINE
webhook (LINE userId) and the scheduler all end up with an `Actor`. Services
*authorise*: every service function takes the actor and calls
`require(actor, "<permission key>")` itself, so no door can skip a check.

Permissions are keys like "read:farm:all" (verb:resource:scope). `perms`
is the union over all of the user's roles, so one person can be a farmer
and a processor at the same time.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from src.exceptions import Forbidden

# "api" = a Bearer token over HTTP (web app via Next.js, or the mobile app);
# the backend can't and needn't tell those two apart.
Channel = Literal["api", "line", "liff", "system"]

# Grants every permission. Only Actor.system() carries it.
ALL_PERMISSIONS = "*"


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: UUID | None  # None only for system jobs
    roles: frozenset[str]
    perms: frozenset[str]
    channel: Channel

    def can(self, perm: str) -> bool:
        return ALL_PERMISSIONS in self.perms or perm in self.perms

    def has_role(self, role: str) -> bool:
        return role in self.roles

    @classmethod
    def system(cls) -> "Actor":
        """For scheduled jobs and other work no person triggered."""
        return cls(
            user_id=None,
            roles=frozenset(),
            perms=frozenset({ALL_PERMISSIONS}),
            channel="system",
        )


def require(actor: Actor, perm: str) -> None:
    """First line of every service function. Raises Forbidden(perm)."""
    if not actor.can(perm):
        raise Forbidden(f"Missing permission: {perm}")
