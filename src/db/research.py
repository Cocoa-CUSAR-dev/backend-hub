"""Researcher profiles.

Owns:
    research.researcher

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

from typing import Any
from uuid import UUID

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
