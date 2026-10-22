"""Login, sessions and passwords: one set of functions for web, mobile and LINE.

Every login-like action ends in `issue_session`, so web and mobile get the
same token pair in the response body (CONVENTIONS.md §5).
"""

from datetime import timedelta
from uuid import UUID

from src import db
from src.actor import Actor, Channel
from src.auth import passwords, tokens
from src.auth.schemas import SessionUser, TokenPairOut
from src.clock import utc_now_naive
from src.config import settings
from src.database import Session, transaction
from src.db.auth import UserRow
from src.exceptions import Conflict, InvalidLiffToken, LineAccountNotLinked, Unauthorized
from src.line import liff

_WRONG_LOGIN = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
# Two requests racing with the same refresh token (two browser tabs, a retry)
# are not theft; only a reuse after this window revokes every session.
_REUSE_GRACE = timedelta(seconds=60)


async def issue_session(session: Session, user: UserRow) -> TokenPairOut:
    """New access + refresh token for this user, with their roles as of now.
    Caller commits (it runs inside the caller's transaction).
    """
    roles, perms = await db.auth.roles_and_permissions(session, user.user_id)
    refresh = tokens.new_refresh_token()
    await db.auth.insert_refresh_token(
        session,
        user.user_id,
        tokens.hash_refresh_token(refresh),
        tokens.refresh_expires_at().replace(tzinfo=None),
    )
    return TokenPairOut(
        access_token=tokens.issue_access_token(user.user_id, user.username, roles, perms),
        refresh_token=refresh,
        expires_in=settings.ACCESS_TOKEN_TTL_SECONDS,
        user=SessionUser(
            user_id=user.user_id,
            username=user.username,
            roles=sorted(roles),
            must_reset_password=user.must_reset_password,
        ),
    )


async def register(session: Session, username: str, password: str) -> TokenPairOut:
    """New account with no roles yet; the user adds roles through /me/roles."""
    passwords.check_strength(password)
    async with transaction(session):
        user_id = await db.auth.create_user(
            session, username.strip(), passwords.hash_password(password)
        )
        if user_id is None:
            raise Conflict("ชื่อผู้ใช้นี้ถูกใช้งานแล้ว")
        user = await db.auth.user_by_id(session, user_id)
        assert user is not None
        return await issue_session(session, user)


async def login(session: Session, username: str, password: str) -> TokenPairOut:
    user = await db.auth.user_by_username(session, username.strip())
    if user is None or not passwords.verify_password(password, user.password_hash):
        raise Unauthorized(_WRONG_LOGIN)
    async with transaction(session):
        return await issue_session(session, user)


async def refresh(session: Session, raw_refresh_token: str) -> TokenPairOut:
    """Swap a refresh token for a new pair. The old one is used up; presenting
    a used one again (after a short grace) is treated as theft and logs the
    user out everywhere.
    """
    stolen_from: UUID | None = None
    async with transaction(session):
        row = await db.auth.refresh_token_by_hash(
            session, tokens.hash_refresh_token(raw_refresh_token)
        )
        now = utc_now_naive()
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            raise Unauthorized("Refresh token is not valid")
        if row.used_at is not None:
            if now - row.used_at > _REUSE_GRACE:
                await db.auth.revoke_all_refresh_tokens(session, row.user_id)
                stolen_from = row.user_id
            else:
                raise Unauthorized("Refresh token already used")
        else:
            await db.auth.mark_refresh_token_used(session, row.refresh_token_id, now)
            user = await db.auth.user_by_id(session, row.user_id)
            if user is None:
                raise Unauthorized("Refresh token is not valid")
            return await issue_session(session, user)
    # Committed the revocation above; only now refuse.
    assert stolen_from is not None
    raise Unauthorized("Refresh token already used -- signed out everywhere")


async def logout(session: Session, raw_refresh_token: str) -> None:
    """Revoke this device's refresh token. The access token simply expires."""
    async with transaction(session):
        await db.auth.revoke_refresh_token(session, tokens.hash_refresh_token(raw_refresh_token))


async def reset_password(session: Session, actor: Actor, new_password: str) -> TokenPairOut:
    """Change my password, sign out every other device, keep this one signed in.
    Any signed-in person may change their own password, whatever their roles
    (a brand-new account has none), so there is no permission key here.
    """
    if actor.user_id is None:
        raise Unauthorized("Not signed in")
    passwords.check_strength(new_password)
    async with transaction(session):
        await db.auth.set_password(session, actor.user_id, passwords.hash_password(new_password))
        await db.auth.revoke_all_refresh_tokens(session, actor.user_id)
        user = await db.auth.user_by_id(session, actor.user_id)
        if user is None:
            raise Unauthorized("Account no longer exists")
        return await issue_session(session, user)


# ---- LINE ------------------------------------------------------------------


async def line_login(session: Session, id_token: str) -> TokenPairOut:
    """LIFF: swap a LINE ID token for our own token pair, once. After this the
    LIFF page uses Bearer like every other client, instead of LINE verifying
    on every request. 403 if this LINE account isn't linked yet.
    """
    claims = await liff.verify_for_any_channel(id_token)
    line_user_id = claims.get("sub")
    if not line_user_id:
        raise InvalidLiffToken("LIFF token had no sub claim")
    user_id = await db.auth.user_id_for_line(session, line_user_id)
    user = await db.auth.user_by_id(session, user_id) if user_id else None
    if user is None:
        raise LineAccountNotLinked
    async with transaction(session):
        return await issue_session(session, user)


async def line_link(session: Session, id_token: str, username: str, password: str) -> TokenPairOut:
    """Link a LINE account to an existing account (username + password),
    then sign in. Replaces mobile-backend's POST /public/liff/link.
    """
    user = await db.auth.user_by_username(session, username.strip())
    if user is None or not passwords.verify_password(password, user.password_hash):
        raise Unauthorized(_WRONG_LOGIN)
    claims = await liff.verify_for_any_channel(id_token)
    line_user_id = claims.get("sub")
    if not line_user_id:
        raise InvalidLiffToken("LIFF token had no sub claim")
    async with transaction(session):
        if not await db.auth.link_line(session, user.user_id, line_user_id, claims.get("name")):
            raise Conflict("บัญชี LINE นี้ถูกผูกกับบัญชีผู้ใช้ไปแล้ว")
        return await issue_session(session, user)


# ---- SSO (LINE diary card -> web app) --------------------------------------


def sso_mint(user_id: UUID) -> str:
    """One-time, short-lived token for a link the chatbot pushes to LINE.
    In-process: no HTTP call to another server.
    """
    token, _, _ = tokens.issue_sso_token(user_id)
    return token


async def sso_exchange(session: Session, token: str) -> TokenPairOut:
    """Redeem an SSO token for a normal token pair. Works once per token: a
    forwarded or screenshotted link can't be used again.
    """
    user_id, jti, expires_at = tokens.read_sso_token(token)
    async with transaction(session):
        if not await db.auth.claim_sso_token(session, jti, expires_at.replace(tzinfo=None)):
            raise Unauthorized("This link has already been used")
        user = await db.auth.user_by_id(session, user_id)
        if user is None:
            raise Unauthorized("Account no longer exists")
        return await issue_session(session, user)


def actor_from_access_token(token: str, channel: Channel) -> Actor:
    """Bearer token -> Actor, without touching the DB."""
    claims = tokens.read_access_token(token)
    return Actor(user_id=claims.user_id, roles=claims.roles, perms=claims.perms, channel=channel)


async def load_actor(session: Session, user_id: UUID, channel: Channel) -> Actor:
    """Actor straight from the DB -- for doors without a token (LINE webhook)."""
    roles, perms = await db.auth.roles_and_permissions(session, user_id)
    return Actor(user_id=user_id, roles=roles, perms=perms, channel=channel)
