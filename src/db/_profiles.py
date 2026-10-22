"""Shared SQL shape for the per-role profile tables (one row per user_id).

Each owning module (agriculture, processing, research) calls these with its
own table name, so the INSERT/UPDATE/SELECT logic exists once. Column names
come from the pydantic schemas in src/me/schemas.py, never from user input
directly, and are checked against an allow-list before being put in SQL.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _check(columns: list[str], allowed: frozenset[str]) -> None:
    unknown = set(columns) - allowed
    if unknown:
        raise ValueError(f"unknown profile columns: {sorted(unknown)}")


async def get(
    session: AsyncSession, table: str, columns: frozenset[str], user_id: UUID
) -> dict[str, Any] | None:
    cols = ", ".join(sorted(columns))
    row = (
        await session.execute(
            text(f"SELECT {cols} FROM {table} WHERE user_id = :user_id"), {"user_id": user_id}
        )
    ).first()
    return dict(row._mapping) if row else None


async def insert(
    session: AsyncSession,
    table: str,
    allowed: frozenset[str],
    user_id: UUID,
    values: dict[str, Any],
) -> bool:
    """False if this user already has a row."""
    cols = list(values)
    _check(cols, allowed)
    names = ", ".join(["user_id", *cols])
    params = ", ".join([":user_id", *(f":{c}" for c in cols)])
    inserted = (
        await session.execute(
            text(
                f"INSERT INTO {table} ({names}) VALUES ({params})"
                " ON CONFLICT (user_id) DO NOTHING RETURNING user_id"
            ),
            {"user_id": user_id, **values},
        )
    ).first()
    return inserted is not None


async def update(
    session: AsyncSession,
    table: str,
    allowed: frozenset[str],
    user_id: UUID,
    values: dict[str, Any],
    *,
    stamped: bool = True,
) -> bool:
    """False if this user has no row. Only the given columns change."""
    cols = list(values)
    _check(cols, allowed)
    if not cols:
        return await get(session, table, frozenset({"user_id"}), user_id) is not None
    sets = ", ".join(f"{c} = :{c}" for c in cols)
    stamp = ", updated_at = now()" if stamped else ""
    updated = (
        await session.execute(
            text(f"UPDATE {table} SET {sets}{stamp} WHERE user_id = :user_id RETURNING user_id"),
            {"user_id": user_id, **values},
        )
    ).first()
    return updated is not None
