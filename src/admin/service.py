"""Admin-only account management: granting and removing roles."""

from uuid import UUID

from src import db
from src.actor import Actor, require
from src.database import Session, transaction
from src.exceptions import Conflict, Forbidden, NotFound

# Roles that carry a full profile (farmer, hub_collector, processor) are added
# by the person themselves, because only they can fill the profile in.
ADMIN_GRANTED_ROLES = frozenset({"researcher", "admin"})


async def grant_role(session: Session, actor: Actor, user_id: UUID, role: str) -> list[str]:
    """Give a user the researcher or admin role. Returns their roles after.
    Takes effect on their next token refresh (at most one access-token life).
    """
    require(actor, "update:role:any")
    if role not in ADMIN_GRANTED_ROLES:
        raise Forbidden(f"'{role}' is added by the user with their own profile")
    async with transaction(session):
        if await db.auth.user_by_id(session, user_id) is None:
            raise NotFound("User not found")
        try:
            added = await db.auth.add_role(session, user_id, role)
        except LookupError as exc:
            raise NotFound(f"Role '{role}' is not set up in this database") from exc
        if not added:
            raise Conflict(f"User already has the role '{role}'")
        if role == "researcher":
            # research.researcher has no required columns; the researcher fills
            # it in later through PATCH /me/profiles/researcher.
            await db.research.create_researcher_profile(session, user_id, {})
        roles, _ = await db.auth.roles_and_permissions(session, user_id)
    return sorted(roles)


async def remove_role(session: Session, actor: Actor, user_id: UUID, role: str) -> list[str]:
    """Take a role away and sign the user out everywhere, so the role stops
    working within one access-token life. The role's profile row is kept --
    it holds history other records point at.
    """
    require(actor, "update:role:any")
    if user_id == actor.user_id and role == "admin":
        raise Forbidden("You can't remove your own admin role")
    async with transaction(session):
        if not await db.auth.remove_role(session, user_id, role):
            raise NotFound(f"User doesn't have the role '{role}'")
        await db.auth.revoke_all_refresh_tokens(session, user_id)
        roles, _ = await db.auth.roles_and_permissions(session, user_id)
    return sorted(roles)
