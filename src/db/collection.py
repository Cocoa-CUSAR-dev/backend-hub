"""Harvests collected at hubs.

Owns:
    collection.harvest
    collection.harvest_collection
    collection.harvest_grade_detail

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class HarvestRow:
    harvest_id: UUID
    harvest_date: date | None
    farm_id: UUID | None
    hub_id: UUID | None


async def harvests_of(
    session: AsyncSession, *, farm_ids: list[UUID], hub_ids: list[UUID], limit: int
) -> list[HarvestRow]:
    """The newest harvests made on any of these farms or collected at any of
    these hubs."""
    result = await session.execute(
        text(
            """
            SELECT harvest_id, harvest_date, farm_id, hub_id
            FROM collection.harvest
            WHERE farm_id = ANY(CAST(:farm_ids AS uuid[]))
               OR hub_id = ANY(CAST(:hub_ids AS uuid[]))
            ORDER BY harvest_date DESC
            LIMIT :limit
            """
        ),
        {"farm_ids": farm_ids, "hub_ids": hub_ids, "limit": limit},
    )
    return [HarvestRow(**row._mapping) for row in result]
