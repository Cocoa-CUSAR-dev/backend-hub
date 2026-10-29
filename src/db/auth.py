"""Logins, roles, permissions and LINE links.

Owns:
    auth.user_account
    auth.user_role
    auth.role
    auth.role_permission
    auth.permission
    auth.line_identity
    auth.line_link_code
    auth.refresh_token
    auth.sso_used_token

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class UserRow:
    user_id: UUID
    username: str
    password_hash: str | None
    is_requires_password_reset: bool
    is_password_reset: bool

    @property
    def must_reset_password(self) -> bool:
        """Admin-created accounts must pick their own password first."""
        return self.is_requires_password_reset and not self.is_password_reset


@dataclass(frozen=True, slots=True)
class RefreshTokenRow:
    refresh_token_id: UUID
    user_id: UUID
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


_USER_COLUMNS = """
    user_id, username, password_hash,
    COALESCE(is_requires_password_reset, false) AS is_requires_password_reset,
    COALESCE(is_password_reset, false) AS is_password_reset
"""


def _user(row: object) -> UserRow:
    return UserRow(**row._mapping)  # type: ignore[attr-defined]


# ---- users ----------------------------------------------------------------


async def user_by_username(session: AsyncSession, username: str) -> UserRow | None:
    row = (
        await session.execute(
            text(f"SELECT {_USER_COLUMNS} FROM auth.user_account WHERE username = :u"),
            {"u": username},
        )
    ).first()
    return _user(row) if row else None


async def user_by_id(session: AsyncSession, user_id: UUID) -> UserRow | None:
    row = (
        await session.execute(
            text(f"SELECT {_USER_COLUMNS} FROM auth.user_account WHERE user_id = :id"),
            {"id": user_id},
        )
    ).first()
    return _user(row) if row else None


async def create_user(
    session: AsyncSession, username: str, password_hash: str, *, requires_reset: bool = False
) -> UUID | None:
    """New account with no roles. None if the username is taken
    (uq_user_account_username, V8).
    """
    return (
        await session.execute(
            text(
                "INSERT INTO auth.user_account"
                " (username, password_hash, is_requires_password_reset)"
                " VALUES (:u, :p, :r)"
                " ON CONFLICT (username) DO NOTHING RETURNING user_id"
            ),
            {"u": username, "p": password_hash, "r": requires_reset},
        )
    ).scalar_one_or_none()


async def set_password(session: AsyncSession, user_id: UUID, password_hash: str) -> None:
    await session.execute(
        text(
            "UPDATE auth.user_account SET password_hash = :p, is_password_reset = true,"
            " updated_at = now() WHERE user_id = :id"
        ),
        {"p": password_hash, "id": user_id},
    )


# ---- roles and permissions -------------------------------------------------


async def roles_and_permissions(
    session: AsyncSession, user_id: UUID
) -> tuple[frozenset[str], frozenset[str]]:
    """Every role the user has, and the union of those roles' permissions."""
    rows = (
        await session.execute(
            text(
                "SELECT r.role_name, p.permission_key"
                " FROM auth.user_role ur"
                " JOIN auth.role r ON r.role_id = ur.role_id"
                " LEFT JOIN auth.role_permission rp ON rp.role_id = r.role_id"
                " LEFT JOIN auth.permission p ON p.permission_id = rp.permission_id"
                " WHERE ur.user_id = :id"
            ),
            {"id": user_id},
        )
    ).all()
    roles = frozenset(r.role_name for r in rows)
    perms = frozenset(r.permission_key for r in rows if r.permission_key)
    return roles, perms


async def add_role(session: AsyncSession, user_id: UUID, role_name: str) -> bool:
    """False if the user already had it. Raises LookupError for an unknown role."""
    role_id = (
        await session.execute(
            text("SELECT role_id FROM auth.role WHERE role_name = :n"), {"n": role_name}
        )
    ).scalar_one_or_none()
    if role_id is None:
        raise LookupError(role_name)
    inserted = (
        await session.execute(
            text(
                "INSERT INTO auth.user_role (user_id, role_id) VALUES (:u, :r)"
                " ON CONFLICT DO NOTHING RETURNING user_id"
            ),
            {"u": user_id, "r": role_id},
        )
    ).first()
    return inserted is not None


async def remove_role(session: AsyncSession, user_id: UUID, role_name: str) -> bool:
    """False if the user didn't have it."""
    removed = (
        await session.execute(
            text(
                "DELETE FROM auth.user_role ur USING auth.role r"
                " WHERE ur.role_id = r.role_id AND ur.user_id = :u AND r.role_name = :n"
                " RETURNING ur.user_id"
            ),
            {"u": user_id, "n": role_name},
        )
    ).first()
    return removed is not None


# ---- refresh tokens (hashed; same scheme mobile-backend uses) --------------


async def insert_refresh_token(
    session: AsyncSession, user_id: UUID, token_hash: str, expires_at: datetime
) -> None:
    await session.execute(
        text(
            "INSERT INTO auth.refresh_token (user_id, token_hash, expires_at) VALUES (:u, :h, :e)"
        ),
        {"u": user_id, "h": token_hash, "e": expires_at},
    )


async def refresh_token_by_hash(session: AsyncSession, token_hash: str) -> RefreshTokenRow | None:
    row = (
        await session.execute(
            text(
                "SELECT refresh_token_id, user_id, expires_at, used_at, revoked_at"
                " FROM auth.refresh_token WHERE token_hash = :h FOR UPDATE"
            ),
            {"h": token_hash},
        )
    ).first()
    return RefreshTokenRow(**row._mapping) if row else None


async def mark_refresh_token_used(
    session: AsyncSession, refresh_token_id: UUID, used_at: datetime
) -> None:
    """`used_at` comes from the app's clock, the same clock the reuse check
    compares against -- not the DB's now(), which can drift from it.
    """
    await session.execute(
        text("UPDATE auth.refresh_token SET used_at = :at WHERE refresh_token_id = :id"),
        {"id": refresh_token_id, "at": used_at},
    )


async def revoke_refresh_token(session: AsyncSession, token_hash: str) -> None:
    await session.execute(
        text(
            "UPDATE auth.refresh_token SET revoked_at = now()"
            " WHERE token_hash = :h AND revoked_at IS NULL"
        ),
        {"h": token_hash},
    )


async def revoke_all_refresh_tokens(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE auth.refresh_token SET revoked_at = now()"
            " WHERE user_id = :u AND revoked_at IS NULL"
        ),
        {"u": user_id},
    )


# ---- LINE links -----------------------------------------------------------


async def user_id_for_line(session: AsyncSession, line_user_id: str) -> UUID | None:
    return (
        await session.execute(
            text("SELECT user_id FROM auth.line_identity WHERE line_user_id = :l"),
            {"l": line_user_id},
        )
    ).scalar_one_or_none()


async def line_user_ids_for(session: AsyncSession, user_ids: Sequence[UUID]) -> dict[UUID, str]:
    """user_id -> LINE user_id, for pushing to people. Users with no LINE
    link are simply absent; callers decide what to do with them.
    """
    if not user_ids:
        return {}
    rows = await session.execute(
        text("SELECT user_id, line_user_id FROM auth.line_identity WHERE user_id = ANY(:ids)"),
        {"ids": list(user_ids)},
    )
    return {row.user_id: row.line_user_id for row in rows}


async def line_linked_user_ids(session: AsyncSession) -> list[UUID]:
    """Everyone who has linked a LINE account, i.e. everyone a LINE push can reach."""
    result = await session.execute(text("SELECT user_id FROM auth.line_identity"))
    return list(result.scalars())


async def existing_role_ids(session: AsyncSession, role_ids: Sequence[UUID]) -> set[UUID]:
    """Which of these role ids exist."""
    if not role_ids:
        return set()
    result = await session.execute(
        text("SELECT role_id FROM auth.role WHERE role_id = ANY(:ids)"), {"ids": list(role_ids)}
    )
    return set(result.scalars())


async def existing_user_ids(session: AsyncSession, user_ids: Sequence[UUID]) -> set[UUID]:
    """Which of these user ids exist."""
    if not user_ids:
        return set()
    result = await session.execute(
        text("SELECT user_id FROM auth.user_account WHERE user_id = ANY(:ids)"),
        {"ids": list(user_ids)},
    )
    return set(result.scalars())


async def usernames(session: AsyncSession, user_ids: Sequence[UUID]) -> dict[UUID, str]:
    """user_id -> username."""
    if not user_ids:
        return {}
    result = await session.execute(
        text("SELECT user_id, username FROM auth.user_account WHERE user_id = ANY(:ids)"),
        {"ids": list(user_ids)},
    )
    return {row.user_id: row.username for row in result}


async def user_ids_with_any_role(session: AsyncSession, role_ids: Sequence[UUID]) -> set[UUID]:
    """Who holds at least one of these roles."""
    if not role_ids:
        return set()
    result = await session.execute(
        text("SELECT DISTINCT user_id FROM auth.user_role WHERE role_id = ANY(:ids)"),
        {"ids": list(role_ids)},
    )
    return set(result.scalars())


async def link_line(
    session: AsyncSession, user_id: UUID, line_user_id: str, display_name: str | None
) -> bool:
    """False if this LINE account or this user is already linked."""
    inserted = (
        await session.execute(
            text(
                "INSERT INTO auth.line_identity (user_id, line_user_id, display_name)"
                " VALUES (:u, :l, :d) ON CONFLICT DO NOTHING RETURNING user_id"
            ),
            {"u": user_id, "l": line_user_id, "d": display_name},
        )
    ).first()
    return inserted is not None


# ---- SSO one-time tokens --------------------------------------------------


async def claim_sso_token(session: AsyncSession, jti: UUID, expires_at: datetime) -> bool:
    """True the first time a jti is redeemed, False on any replay."""
    inserted = (
        await session.execute(
            text(
                "INSERT INTO auth.sso_used_token (jti, expires_at) VALUES (:j, :e)"
                " ON CONFLICT (jti) DO NOTHING RETURNING jti"
            ),
            {"j": jti, "e": expires_at},
        )
    ).first()
    return inserted is not None
