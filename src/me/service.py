"""My account: who I am, my roles, my profile for each role (multi-role).

A person can hold several roles at once (auth.user_role is many-to-many), and
each role has its own profile table keyed by user_id. Profiles are always
shown and edited per role, never merged.
"""

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src import db
from src.actor import Actor, require
from src.auth.service import issue_session
from src.config import settings
from src.database import Session, transaction
from src.exceptions import Conflict, Forbidden, NotFound, Unauthorized
from src.me.schemas import MeOut, Profiles, RoleAddedOut

Getter = Callable[[Session, UUID], Awaitable[dict[str, Any] | None]]
Writer = Callable[[Session, UUID, dict[str, Any]], Awaitable[bool]]


@dataclass(frozen=True)
class _ProfileStore:
    get: Getter
    create: Writer
    update: Writer


PROFILES: dict[str, _ProfileStore] = {
    "farmer": _ProfileStore(
        db.agriculture.farmer_profile,
        db.agriculture.create_farmer_profile,
        db.agriculture.update_farmer_profile,
    ),
    "hub_collector": _ProfileStore(
        db.processing.hub_collector_profile,
        db.processing.create_hub_collector_profile,
        db.processing.update_hub_collector_profile,
    ),
    "processor": _ProfileStore(
        db.processing.processor_profile,
        db.processing.create_processor_profile,
        db.processing.update_processor_profile,
    ),
    "researcher": _ProfileStore(
        db.research.researcher_profile,
        db.research.create_researcher_profile,
        db.research.update_researcher_profile,
    ),
}

# Roles anyone may add to themselves by filling in that role's profile.
# researcher additionally needs RESEARCHER_SIGNUP_CODE; admin is never
# self-service (granted in src/admin/).
SELF_SERVICE_ROLES = frozenset({"farmer", "hub_collector", "processor"})


def _user_id(actor: Actor) -> UUID:
    if actor.user_id is None:
        raise Unauthorized("Not signed in")
    return actor.user_id


async def me(session: Session, actor: Actor) -> MeOut:
    """My account, every role I hold, and each role's profile.
    No permission key: it is always your own account, and a brand-new
    account has no roles (so no permissions) yet.
    """
    user_id = _user_id(actor)
    user = await db.auth.user_by_id(session, user_id)
    if user is None:
        raise Unauthorized("Account no longer exists")
    roles, _ = await db.auth.roles_and_permissions(session, user_id)
    profiles = {role: await store.get(session, user_id) for role, store in PROFILES.items()}
    return MeOut(
        user_id=user.user_id,
        username=user.username,
        roles=sorted(roles),
        must_reset_password=user.must_reset_password,
        profiles=Profiles(**{r: p for r, p in profiles.items() if r in roles}),
    )


async def add_role(
    session: Session,
    actor: Actor,
    role: str,
    profile: dict[str, Any],
    *,
    signup_code: str | None = None,
) -> RoleAddedOut:
    """Give myself another role and its profile, in one transaction.
    Returns a new token pair so the role works on the very next request.
    No permission key, for the same reason as `me`; which roles are allowed
    is the rule instead.
    """
    user_id = _user_id(actor)
    if role == "researcher":
        expected = settings.RESEARCHER_SIGNUP_CODE
        if not expected or not hmac.compare_digest((signup_code or "").encode(), expected.encode()):
            raise Forbidden("รหัสลงทะเบียนนักวิจัยไม่ถูกต้อง")
    elif role not in SELF_SERVICE_ROLES:
        raise Forbidden(f"Role '{role}' is granted by an admin")

    store = PROFILES[role]
    async with transaction(session):
        try:
            added = await db.auth.add_role(session, user_id, role)
        except LookupError as exc:
            raise NotFound(f"Role '{role}' is not set up in this database") from exc
        if not added:
            raise Conflict("คุณมีบทบาทนี้อยู่แล้ว")
        # A profile row can already exist without the role (accounts from the
        # old apps); keep it and apply what was sent.
        if not await store.create(session, user_id, profile):
            await store.update(session, user_id, profile)
        user = await db.auth.user_by_id(session, user_id)
        if user is None:
            raise Unauthorized("Account no longer exists")
        pair = await issue_session(session, user)
    return RoleAddedOut(**pair.model_dump())


async def update_profile(
    session: Session, actor: Actor, role: str, values: dict[str, Any]
) -> dict[str, Any]:
    """Edit my profile for one role I hold. Returns the profile after the edit."""
    require(actor, "update:profile:own")
    user_id = _user_id(actor)
    if not actor.has_role(role):
        raise Forbidden(f"You don't have the role '{role}'")
    store = PROFILES[role]
    async with transaction(session):
        if not await store.update(session, user_id, values):
            raise NotFound("Profile not found")
        profile = await store.get(session, user_id)
    assert profile is not None
    return profile
