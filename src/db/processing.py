"""Hubs, hub collectors, processors, stations and batches.

Owns:
    processing.hub
    processing.hub_collector
    processing.processor
    processing.processing_station
    processing.processor_processing_station
    processing.processing_record
    processing.batch
    processing.fermentation_batch
    processing.drying_batch

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import _profiles

PROCESSOR_COLUMNS = frozenset(
    {
        "first_name", "last_name", "nickname", "birth_date", "id_card_number",
        "address_detail", "subdistrict_id", "district_id", "province_id", "zip_code",
        "phone_number", "line", "facebook",
    }
)  # fmt: skip

# hub_id is set when the collector registers a hub, not in the profile.
HUB_COLLECTOR_COLUMNS = frozenset(
    {
        "first_name", "last_name", "nickname", "birthdate", "id_card_number",
        "subdistrict_id", "district_id", "province_id", "zip_code", "phone_number",
        "line", "facebook",
    }
)  # fmt: skip


async def processor_profile(session: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    return await _profiles.get(session, "processing.processor", PROCESSOR_COLUMNS, user_id)


async def create_processor_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    return await _profiles.insert(
        session, "processing.processor", PROCESSOR_COLUMNS, user_id, values
    )


async def update_processor_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    return await _profiles.update(
        session, "processing.processor", PROCESSOR_COLUMNS, user_id, values
    )


async def hub_collector_profile(session: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    return await _profiles.get(session, "processing.hub_collector", HUB_COLLECTOR_COLUMNS, user_id)


async def create_hub_collector_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    return await _profiles.insert(
        session, "processing.hub_collector", HUB_COLLECTOR_COLUMNS, user_id, values
    )


async def update_hub_collector_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    return await _profiles.update(
        session, "processing.hub_collector", HUB_COLLECTOR_COLUMNS, user_id, values
    )


@dataclass(frozen=True, slots=True)
class BatchRow:
    batch_id: UUID
    created_at: datetime
    station_name: str | None


async def hub_ids_of_collector(session: AsyncSession, user_id: UUID) -> list[UUID]:
    """The hubs this user collects for."""
    result = await session.execute(
        text("SELECT hub_id FROM processing.hub_collector WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    return [hub_id for hub_id in result.scalars() if hub_id is not None]


async def hub_names(session: AsyncSession, hub_ids: list[UUID]) -> dict[UUID, str | None]:
    """hub_id -> hub name, for the hubs that exist."""
    if not hub_ids:
        return {}
    result = await session.execute(
        text("SELECT hub_id, hub_name FROM processing.hub WHERE hub_id = ANY(:ids)"),
        {"ids": hub_ids},
    )
    return {row.hub_id: row.hub_name for row in result}


async def batches_of_processor(
    session: AsyncSession, processor_id: UUID, limit: int
) -> list[BatchRow]:
    """The newest batches at the processing stations this processor works at."""
    result = await session.execute(
        text(
            """
            SELECT b.batch_id, b.created_at, ps.processing_station_name AS station_name
            FROM processing.batch b
            JOIN processing.processor_processing_station pps
                ON pps.processing_station_id = b.processing_station_id
            JOIN processing.processing_station ps
                ON ps.processing_station_id = b.processing_station_id
            WHERE pps.processor_id = :processor_id
            ORDER BY b.created_at DESC
            LIMIT :limit
            """
        ),
        {"processor_id": processor_id, "limit": limit},
    )
    return [BatchRow(**row._mapping) for row in result]
