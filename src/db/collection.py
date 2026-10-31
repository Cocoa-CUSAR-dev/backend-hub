"""Harvests collected at hubs.

Owns:
    collection.harvest
    collection.harvest_collection
    collection.harvest_grade_detail

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class HarvestRow:
    harvest_id: UUID
    harvest_date: datetime | None  # a timestamp column in V1
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


@dataclass(frozen=True, slots=True)
class HarvestGradeRow:
    """One harvest with one of its grade rows (and the batch it went into).

    A harvest graded into several grades comes back once per grade, as in
    mobile-backend; one with no grade row comes back once with the grade
    fields None.
    """

    harvest_id: UUID
    hub_id: UUID
    farm_id: UUID
    plot_id: UUID | None
    tree_id: UUID | None
    harvest_date: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    grade_code: str | None
    quantity_kg: Decimal | None
    grade_description: str | None
    is_clean: bool | None
    is_appropriate_size: bool | None
    weight_gram_per_pod: Decimal | None
    is_sprout: bool | None
    is_dry: bool | None
    is_shriveled: bool | None
    cut_test_result: str | None
    collection_id: UUID | None
    batch_id: UUID | None


async def harvests(
    session: AsyncSession,
    *,
    hub_ids: list[UUID] | None,
    limit: int | None = None,
    offset: int = 0,
) -> list[HarvestGradeRow]:
    """Harvests at these hubs (or every hub when None), newest first.
    No limit means all of them."""
    result = await session.execute(
        text(
            """
            SELECT
                h.harvest_id, h.hub_id, h.farm_id, h.plot_id, h.tree_id, h.harvest_date,
                h.created_at, h.updated_at,
                gd.grade_code, gd.quantity_kg, gd.description AS grade_description,
                gd.is_clean, gd.is_appropriate_size, gd.weight_gram_per_pod,
                gd.is_sprout, gd.is_dry, gd.is_shriveled, gd.cut_test_result,
                hc.collection_id, hc.batch_id
            FROM collection.harvest h
            LEFT JOIN collection.harvest_grade_detail gd ON gd.harvest_id = h.harvest_id
            LEFT JOIN collection.harvest_collection hc ON hc.harvest_id = h.harvest_id
            WHERE CAST(:all_hubs AS boolean) OR h.hub_id = ANY(:hub_ids)
            ORDER BY h.harvest_date DESC NULLS LAST, h.harvest_id, gd.grade_code
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "all_hubs": hub_ids is None,
            "hub_ids": hub_ids or [],
            "limit": limit,
            "offset": offset,
        },
    )
    return [HarvestGradeRow(**row._mapping) for row in result]


async def harvest_choices(
    session: AsyncSession, hub_ids: list[UUID], hub_id: str | None = None
) -> list[tuple[UUID, UUID | None, datetime | None]]:
    """(harvest_id, farm_id, harvest_date) of the harvests at these hubs,
    newest first; only `hub_id`'s (compared as text) when given."""
    if not hub_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT harvest_id, farm_id, harvest_date
            FROM collection.harvest
            WHERE hub_id = ANY(:hub_ids)
              AND (CAST(:hub_id AS text) IS NULL OR hub_id::text = :hub_id)
            ORDER BY harvest_date DESC NULLS LAST, harvest_id
            """
        ),
        {"hub_ids": hub_ids, "hub_id": hub_id},
    )
    return [(row.harvest_id, row.farm_id, row.harvest_date) for row in result]
