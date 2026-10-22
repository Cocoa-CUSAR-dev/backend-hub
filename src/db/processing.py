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

from typing import Any
from uuid import UUID

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
