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
) -> list[HarvestRow]:
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
    return [HarvestRow(**row._mapping) for row in result]
