"""Admin-only account management: creating accounts, granting and removing roles."""

from uuid import UUID

from src import db
from src.actor import Actor, require
from src.auth import passwords
from src.database import Session, transaction
from src.exceptions import Conflict, Forbidden, NotFound

# Roles that carry a full profile (farmer, hub_collector, processor) are added
# by the person themselves, because only they can fill the profile in.
ADMIN_GRANTED_ROLES = frozenset({"researcher", "admin"})


async def create_user(
    session: Session,
    actor: Actor,
    username: str,
    temporary_password: str,
    role: str,
    profile: dict[str, str | None],
) -> tuple[UUID, list[str]]:
    """Create an account for someone else (a researcher or another admin).
    They must pick their own password at first sign-in (must_reset_password),
    the same rule web-backend's POST /admin/users had.
    """
    require(actor, "create:user:any")
    if role not in ADMIN_GRANTED_ROLES:
        raise Forbidden(f"'{role}' is added by the user with their own profile")
    passwords.check_strength(temporary_password)
    async with transaction(session):
        user_id = await db.auth.create_user(
            session,
            username.strip(),
            passwords.hash_password(temporary_password),
            requires_reset=True,
        )
        if user_id is None:
            raise Conflict("ชื่อผู้ใช้นี้ถูกใช้งานแล้ว")
        try:
            await db.auth.add_role(session, user_id, role)
        except LookupError as exc:
            raise NotFound(f"Role '{role}' is not set up in this database") from exc
        if role == "researcher":
            await db.research.create_researcher_profile(session, user_id, profile)
        roles, _ = await db.auth.roles_and_permissions(session, user_id)
    return user_id, sorted(roles)


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
