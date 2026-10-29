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

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
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


# ---- hubs (registered by a hub collector) ------------------------------------


@dataclass(frozen=True, slots=True)
class HubRow:
    hub_id: UUID
    hub_name: str | None
    geo_id: UUID | None
    found_date: date | None
    address_detail: str | None
    subdistrict_id: UUID | None
    district_id: UUID | None
    province_id: UUID | None
    zip_code: str | None
    contact_name: str | None
    phone_number: str | None
    line: str | None
    facebook: str | None
    created_at: datetime
    updated_at: datetime


_HUB_COLUMNS = """
    h.hub_id, h.hub_name, h.geo_id, h.found_date, h.address_detail, h.subdistrict_id,
    h.district_id, h.province_id, h.zip_code, h.contact_name, h.phone_number, h.line,
    h.facebook, h.created_at, h.updated_at
"""


async def _insert(session: AsyncSession, table: str, values: dict[str, Any]) -> None:
    # `table` is one of this module's own literals, never caller input.
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    await session.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), values)


async def insert_hub(session: AsyncSession, hub_id: UUID, **columns: Any) -> None:
    await _insert(session, "processing.hub", {"hub_id": hub_id, **columns})


async def set_collector_hub(session: AsyncSession, user_id: UUID, hub_id: UUID) -> bool:
    """Make this the collector's hub (one hub per collector, as in Go). False
    if the user has no hub collector profile."""
    updated = await session.execute(
        text(
            "UPDATE processing.hub_collector SET hub_id = :hub_id, updated_at = now()"
            " WHERE user_id = :user_id RETURNING user_id"
        ),
        {"hub_id": hub_id, "user_id": user_id},
    )
    return updated.first() is not None


async def hubs(
    session: AsyncSession, *, hub_ids: list[UUID] | None, limit: int, offset: int
) -> list[HubRow]:
    """Hubs ordered by hub_id: only these ids, or every hub when None."""
    result = await session.execute(
        text(
            f"""
            SELECT {_HUB_COLUMNS}
            FROM processing.hub h
            WHERE CAST(:all_hubs AS boolean) OR h.hub_id = ANY(:hub_ids)
            ORDER BY h.hub_id
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
    return [HubRow(**row._mapping) for row in result]


# ---- processing stations and batches (registered by a processor) -------------


@dataclass(frozen=True, slots=True)
class StationRow:
    processing_station_id: UUID
    processing_station_name: str | None
    hub_id: UUID | None
    geo_id: UUID | None
    found_date: date | None
    processing_station_area: Decimal | None
    address_detail: str | None
    subdistrict_id: UUID | None
    district_id: UUID | None
    province_id: UUID | None
    zip_code: str | None
    contact_name: str | None
    phone_number: str | None
    line: str | None
    facebook: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BatchDetailRow:
    batch_id: UUID
    processing_station_id: UUID
    origin: str | None
    notes: str | None
    quantity_kg: Decimal | None
    created_at: datetime
    updated_at: datetime


_STATION_COLUMNS = """
    s.processing_station_id, s.processing_station_name, s.hub_id, s.geo_id, s.found_date,
    s.processing_station_area, s.address_detail, s.subdistrict_id, s.district_id,
    s.province_id, s.zip_code, s.contact_name, s.phone_number, s.line, s.facebook,
    s.created_at, s.updated_at
"""

_BATCH_COLUMNS = """
    b.batch_id, b.processing_station_id, b.origin, b.notes, b.quantity_kg,
    b.created_at, b.updated_at
"""

# A processor's stations: the ones linked to them, or all when :all_stations.
_MY_STATIONS = """
    CAST(:all_stations AS boolean) OR EXISTS (
        SELECT 1 FROM processing.processor_processing_station pps
        WHERE pps.processing_station_id = {station}.processing_station_id
          AND pps.processor_id = :processor_id
    )
"""


async def insert_station(session: AsyncSession, station_id: UUID, **columns: Any) -> None:
    await _insert(
        session,
        "processing.processing_station",
        {"processing_station_id": station_id, **columns},
    )


async def link_processor_station(
    session: AsyncSession, processor_id: UUID, station_id: UUID
) -> None:
    await _insert(
        session,
        "processing.processor_processing_station",
        {"processor_id": processor_id, "processing_station_id": station_id},
    )


async def stations(
    session: AsyncSession, *, processor_id: UUID | None, limit: int, offset: int
) -> list[StationRow]:
    """Stations ordered by id: a processor's own, or every station when None."""
    result = await session.execute(
        text(
            f"""
            SELECT {_STATION_COLUMNS}
            FROM processing.processing_station s
            WHERE {_MY_STATIONS.format(station="s")}
            ORDER BY s.processing_station_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "all_stations": processor_id is None,
            "processor_id": processor_id,
            "limit": limit,
            "offset": offset,
        },
    )
    return [StationRow(**row._mapping) for row in result]


async def batches_of_stations(
    session: AsyncSession, station_ids: list[UUID]
) -> list[BatchDetailRow]:
    """Every batch of these stations, ordered by batch_id."""
    if not station_ids:
        return []
    result = await session.execute(
        text(
            f"""
            SELECT {_BATCH_COLUMNS}
            FROM processing.batch b
            WHERE b.processing_station_id = ANY(:station_ids)
            ORDER BY b.batch_id
            """
        ),
        {"station_ids": station_ids},
    )
    return [BatchDetailRow(**row._mapping) for row in result]


async def batches(
    session: AsyncSession, *, processor_id: UUID | None, limit: int, offset: int
) -> list[BatchDetailRow]:
    """Batches ordered by batch_id: at a processor's stations, or all when None."""
    result = await session.execute(
        text(
            f"""
            SELECT {_BATCH_COLUMNS}
            FROM processing.batch b
            JOIN processing.processing_station s
              ON s.processing_station_id = b.processing_station_id
            WHERE {_MY_STATIONS.format(station="s")}
            ORDER BY b.batch_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "all_stations": processor_id is None,
            "processor_id": processor_id,
            "limit": limit,
            "offset": offset,
        },
    )
    return [BatchDetailRow(**row._mapping) for row in result]


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


async def processor_names(session: AsyncSession, user_ids: Sequence[UUID]) -> dict[UUID, str]:
    """user_id -> "first last" of their processor profile, for the people who have
    one with both names (a missing part means no name, as web-backend joined
    them)."""
    if not user_ids:
        return {}
    result = await session.execute(
        text(
            "SELECT user_id, first_name || ' ' || last_name AS name FROM processing.processor"
            " WHERE user_id = ANY(:ids) AND first_name IS NOT NULL AND last_name IS NOT NULL"
        ),
        {"ids": list(user_ids)},
    )
    return {row.user_id: row.name for row in result}


async def hub_collector_names(session: AsyncSession, user_ids: Sequence[UUID]) -> dict[UUID, str]:
    """user_id -> "first last" of their hub collector profile, for the people
    who have one with both names."""
    if not user_ids:
        return {}
    result = await session.execute(
        text(
            "SELECT user_id, first_name || ' ' || last_name AS name FROM processing.hub_collector"
            " WHERE user_id = ANY(:ids) AND first_name IS NOT NULL AND last_name IS NOT NULL"
        ),
        {"ids": list(user_ids)},
    )
    return {row.user_id: row.name for row in result}
