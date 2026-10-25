"""Admin-only account management: creating accounts, granting and removing roles,
and the researcher list (ADMIN-02..04)."""

from uuid import UUID

from src import db
from src.actor import Actor, require
from src.auth import passwords
from src.database import Session, transaction
from src.exceptions import Conflict, Forbidden, Invalid, NotFound

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


# ---- researchers (ADMIN-02..04), ported from web-backend's ResearcherService -----------

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
DELETED = "Successfully deleted researcher"


async def researchers(
    session: Session,
    actor: Actor,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    organization: str | None = None,
    page: int = 0,
    size: int = DEFAULT_PAGE_SIZE,
) -> list[db.research.Researcher]:
    """Every researcher, or those whose names / organization are exactly these."""
    require(actor, "read:researcher:all")
    errors = {}
    if page < 0:
        errors["page"] = "page must be 0 or more"
    if not 1 <= size <= MAX_PAGE_SIZE:
        errors["size"] = f"size must be between 1 and {MAX_PAGE_SIZE}"
    if errors:
        raise Invalid(fields=errors)
    return await db.research.researchers(
        session,
        first_name=first_name,
        last_name=last_name,
        organization=organization,
        limit=size,
        offset=page * size,
    )


async def researcher(session: Session, actor: Actor, user_id: UUID) -> db.research.Researcher:
    require(actor, "read:researcher:all")
    found = await db.research.researcher(session, user_id)
    if found is None:
        raise NotFound("Researcher not found")
    return found


async def delete_researcher(session: Session, actor: Actor, user_id: UUID) -> str:
    """Remove a researcher: their researcher profile and role, and sign them
    out everywhere. Only that role: Kotlin removed every role the account
    held, which with several roles per person would also take away their
    farmer or processor role."""
    require(actor, "delete:researcher:any")
    async with transaction(session):
        if not await db.research.delete_researcher_profile(session, user_id):
            raise NotFound("Researcher not found or do not have access")
        await db.auth.remove_role(session, user_id, "researcher")
        await db.auth.revoke_all_refresh_tokens(session, user_id)
    return DELETED
