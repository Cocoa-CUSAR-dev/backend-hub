"""Researcher profiles.

Owns:
    research.researcher

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import _profiles

RESEARCHER_COLUMNS = frozenset({"first_name", "last_name", "organization"})


async def researcher_profile(session: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    return await _profiles.get(session, "research.researcher", RESEARCHER_COLUMNS, user_id)


async def create_researcher_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    return await _profiles.insert(
        session, "research.researcher", RESEARCHER_COLUMNS, user_id, values
    )


async def update_researcher_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    # research.researcher has no updated_at column.
    return await _profiles.update(
        session, "research.researcher", RESEARCHER_COLUMNS, user_id, values, stamped=False
    )


# ---- the admin's researcher list (ADMIN-02..04) -------------------------------------


@dataclass(frozen=True, slots=True)
class Researcher:
    user_id: UUID
    first_name: str | None
    last_name: str | None
    organization: str | None


_RESEARCHER = "SELECT user_id, first_name, last_name, organization FROM research.researcher"


async def researchers(
    session: AsyncSession,
    *,
    first_name: str | None,
    last_name: str | None,
    organization: str | None,
    limit: int,
    offset: int,
) -> list[Researcher]:
    """Researchers matching every filter given (exactly), by last name, first
    name, then id, so a page never repeats or skips one."""
    where: list[str] = []
    params: dict[str, object] = {"limit": limit, "offset": offset}
    for column, value in (
        ("first_name", first_name),
        ("last_name", last_name),
        ("organization", organization),
    ):
        if value is not None:
            where.append(f"{column} = :{column}")
            params[column] = value
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    result = await session.execute(
        text(
            f"{_RESEARCHER}{clause}"
            " ORDER BY last_name, first_name, user_id LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return [Researcher(**row._mapping) for row in result]


async def researcher(session: AsyncSession, user_id: UUID) -> Researcher | None:
    result = await session.execute(text(f"{_RESEARCHER} WHERE user_id = :u"), {"u": user_id})
    row = result.first()
    return Researcher(**row._mapping) if row else None


async def delete_researcher_profile(session: AsyncSession, user_id: UUID) -> bool:
    """False when this user has no researcher profile."""
    result = await session.execute(
        text("DELETE FROM research.researcher WHERE user_id = :u RETURNING user_id"),
        {"u": user_id},
    )
    return result.first() is not None
