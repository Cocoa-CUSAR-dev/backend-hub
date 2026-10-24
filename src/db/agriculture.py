"""Farmers, farms, plots and every agriculture handler table.

Owns:
    agriculture.farmer
    agriculture.farm
    agriculture.farmer_farm
    agriculture.plot
    agriculture.plot_breed
    agriculture.farm_activity
    agriculture.farm_activity_chemical
    agriculture.farm_activity_fertilizer
    agriculture.farm_pest_disease_record
    agriculture.farm_initial_cost
    agriculture.farm_economic_eval

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import _profiles

FARMER_COLUMNS = frozenset(
    {
        "first_name", "last_name", "nickname", "birth_date", "nationality", "ethnicity",
        "religion", "id_card_number", "address_detail", "subdistrict_id", "district_id",
        "province_id", "zip_code", "phone_number", "line", "facebook", "salary_income",
        "family_member_count", "agri_worker_count", "agri_experience",
    }
)  # fmt: skip


async def farmer_profile(session: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    return await _profiles.get(session, "agriculture.farmer", FARMER_COLUMNS, user_id)


async def create_farmer_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    return await _profiles.insert(session, "agriculture.farmer", FARMER_COLUMNS, user_id, values)


async def update_farmer_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    return await _profiles.update(session, "agriculture.farmer", FARMER_COLUMNS, user_id, values)


@dataclass(frozen=True, slots=True)
class FarmRow:
    farm_id: UUID
    farm_name: str | None
    hub_id: UUID | None
    geo_id: UUID | None
    found_date: date | None
    total_area: Decimal | None
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
class PlotRow:
    plot_id: UUID
    farm_id: UUID
    plot_name: str | None
    total_area: Decimal | None
    land_ownership: str | None
    cocoa_planted_area: Decimal | None
    has_chemical_use: bool | None
    land_type_id: UUID | None
    soil_type_id: UUID | None
    water_source_id: UUID | None
    watering_system_id: UUID | None
    found_date: date | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PlotWithFarmRow:
    plot_id: UUID
    plot_name: str | None
    farm_name: str | None


@dataclass(frozen=True, slots=True)
class FarmActivityRow:
    farm_activity_id: UUID
    created_at: datetime
    farm_name: str | None
    farm_activity_type_id: UUID | None


_FARM_COLUMNS = """
    f.farm_id, f.farm_name, f.hub_id, f.geo_id, f.found_date, f.total_area,
    f.address_detail, f.subdistrict_id, f.district_id, f.province_id, f.zip_code,
    f.contact_name, f.phone_number, f.line, f.facebook, f.created_at, f.updated_at
"""

_PLOT_COLUMNS = """
    p.plot_id, p.farm_id, p.plot_name, p.total_area, p.land_ownership,
    p.cocoa_planted_area, p.has_chemical_use, p.land_type_id, p.soil_type_id,
    p.water_source_id, p.watering_system_id, p.found_date, p.created_at, p.updated_at
"""


async def _insert(session: AsyncSession, table: str, values: dict[str, Any]) -> None:
    # `table` is one of this module's own literals, never caller input.
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    await session.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), values)


async def insert_farm(session: AsyncSession, farm_id: UUID, **columns: Any) -> None:
    """Insert one farm row. Column names are the table's own (farm_name,
    province_id, found_date, created_at, ...)."""
    await _insert(session, "agriculture.farm", {"farm_id": farm_id, **columns})


async def link_farmer(session: AsyncSession, farmer_id: UUID, farm_id: UUID) -> None:
    """Record who owns a farm; every "my farms / my plots" read goes through it."""
    await _insert(session, "agriculture.farmer_farm", {"farmer_id": farmer_id, "farm_id": farm_id})


async def insert_plot(session: AsyncSession, plot_id: UUID, **columns: Any) -> None:
    await _insert(session, "agriculture.plot", {"plot_id": plot_id, **columns})


async def farmer_owns_farm(session: AsyncSession, farmer_id: UUID, farm_id: UUID) -> bool:
    found = await session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM agriculture.farmer_farm
                WHERE farmer_id = :farmer_id AND farm_id = :farm_id
            )
            """
        ),
        {"farmer_id": farmer_id, "farm_id": farm_id},
    )
    return bool(found.scalar())


@dataclass(frozen=True, slots=True)
class FarmPlace:
    """Where a farm is: its province, district and subdistrict."""

    province_id: UUID | None
    district_id: UUID | None
    subdistrict_id: UUID | None


async def farm_place(session: AsyncSession, farm_id: UUID) -> FarmPlace | None:
    row = (
        await session.execute(
            text(
                "SELECT province_id, district_id, subdistrict_id"
                " FROM agriculture.farm WHERE farm_id = :farm_id"
            ),
            {"farm_id": farm_id},
        )
    ).first()
    return FarmPlace(**row._mapping) if row else None


async def farm_exists(session: AsyncSession, farm_id: UUID) -> bool:
    found = await session.execute(
        text("SELECT EXISTS (SELECT 1 FROM agriculture.farm WHERE farm_id = :farm_id)"),
        {"farm_id": farm_id},
    )
    return bool(found.scalar())


async def farms(
    session: AsyncSession, *, farmer_id: UUID | None, limit: int, offset: int
) -> list[FarmRow]:
    """Farms ordered by farm_id: only `farmer_id`'s, or every farm when None."""
    result = await session.execute(
        text(
            f"""
            SELECT {_FARM_COLUMNS}
            FROM agriculture.farm f
            WHERE CAST(:farmer_id AS uuid) IS NULL OR EXISTS (
                SELECT 1 FROM agriculture.farmer_farm ff
                WHERE ff.farm_id = f.farm_id AND ff.farmer_id = :farmer_id
            )
            ORDER BY f.farm_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {"farmer_id": farmer_id, "limit": limit, "offset": offset},
    )
    return [FarmRow(**row._mapping) for row in result]


async def plots_of_farms(session: AsyncSession, farm_ids: list[UUID]) -> list[PlotRow]:
    """Every plot of these farms, ordered by plot_id."""
    if not farm_ids:
        return []
    result = await session.execute(
        text(
            f"""
            SELECT {_PLOT_COLUMNS}
            FROM agriculture.plot p
            WHERE p.farm_id = ANY(:farm_ids)
            ORDER BY p.plot_id
            """
        ),
        {"farm_ids": farm_ids},
    )
    return [PlotRow(**row._mapping) for row in result]


async def plots(
    session: AsyncSession, *, farmer_id: UUID | None, limit: int, offset: int
) -> list[PlotRow]:
    """Plots ordered by plot_id: only those on `farmer_id`'s farms, or every
    plot when None."""
    result = await session.execute(
        text(
            f"""
            SELECT {_PLOT_COLUMNS}
            FROM agriculture.plot p
            WHERE CAST(:farmer_id AS uuid) IS NULL OR EXISTS (
                SELECT 1 FROM agriculture.farmer_farm ff
                WHERE ff.farm_id = p.farm_id AND ff.farmer_id = :farmer_id
            )
            ORDER BY p.plot_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {"farmer_id": farmer_id, "limit": limit, "offset": offset},
    )
    return [PlotRow(**row._mapping) for row in result]


async def farm_ids_of_farmer(session: AsyncSession, farmer_id: UUID) -> list[UUID]:
    """The farms this farmer is linked to."""
    result = await session.execute(
        text("SELECT farm_id FROM agriculture.farmer_farm WHERE farmer_id = :farmer_id"),
        {"farmer_id": farmer_id},
    )
    return list(result.scalars())


async def plot_ids_of_farmer(session: AsyncSession, farmer_id: UUID) -> list[UUID]:
    """The plots on the farms this farmer is linked to."""
    result = await session.execute(
        text(
            """
            SELECT p.plot_id FROM agriculture.plot p
            JOIN agriculture.farmer_farm ff ON ff.farm_id = p.farm_id
            WHERE ff.farmer_id = :farmer_id
            """
        ),
        {"farmer_id": farmer_id},
    )
    return list(result.scalars())


async def plots_with_farm_name(
    session: AsyncSession, *, farmer_id: UUID | None, farm_id: UUID | None
) -> list[PlotWithFarmRow]:
    """Plots with the name of their farm, ordered by farm then plot name.
    `farmer_id` keeps only plots on that farmer's farms (None: every farm);
    `farm_id` keeps only that farm's plots (None: all)."""
    result = await session.execute(
        text(
            """
            SELECT p.plot_id, p.plot_name, f.farm_name
            FROM agriculture.plot p
            JOIN agriculture.farm f ON f.farm_id = p.farm_id
            WHERE (
                CAST(:farmer_id AS uuid) IS NULL
                OR EXISTS (
                    SELECT 1 FROM agriculture.farmer_farm ff
                    WHERE ff.farm_id = p.farm_id AND ff.farmer_id = :farmer_id
                )
            )
              AND (CAST(:farm_id AS uuid) IS NULL OR p.farm_id = :farm_id)
            ORDER BY f.farm_name, p.plot_name
            """
        ),
        {"farmer_id": farmer_id, "farm_id": farm_id},
    )
    return [PlotWithFarmRow(**row._mapping) for row in result]


async def farm_activities_of_farmer(
    session: AsyncSession, farmer_id: UUID, limit: int
) -> list[FarmActivityRow]:
    """The newest activities on this farmer's farms, with the farm's name."""
    result = await session.execute(
        text(
            """
            SELECT fa.farm_activity_id, fa.created_at, f.farm_name, fa.farm_activity_type_id
            FROM agriculture.farm_activity fa
            JOIN agriculture.farmer_farm ff ON ff.farm_id = fa.farm_id
            JOIN agriculture.farm f ON f.farm_id = fa.farm_id
            WHERE ff.farmer_id = :farmer_id
            ORDER BY fa.created_at DESC
            LIMIT :limit
            """
        ),
        {"farmer_id": farmer_id, "limit": limit},
    )
    return [FarmActivityRow(**row._mapping) for row in result]


async def farm_names(session: AsyncSession, farm_ids: list[UUID]) -> dict[UUID, str | None]:
    """farm_id -> farm name, for the farms that exist."""
    if not farm_ids:
        return {}
    result = await session.execute(
        text("SELECT farm_id, farm_name FROM agriculture.farm WHERE farm_id = ANY(:ids)"),
        {"ids": farm_ids},
    )
    return {row.farm_id: row.farm_name for row in result}
