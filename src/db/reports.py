"""Read-only queries that join many schemas (analytics, Excel export).

Owns: no tables. Never writes. If a query only touches one schema, it belongs
in that schema's file instead. scripts/check_conventions.py lets this file read
any table and fails it on INSERT / UPDATE / DELETE.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class FarmExportRow:
    """One farm for the raw-data export's farm sheet (RPT-01)."""

    farm_name: str | None
    found_date: date | None
    total_area: Decimal | None
    province_name_en: str | None
    district_name_en: str | None
    subdistrict_name_en: str | None
    contact_name: str | None
    phone_number: str | None
    line: str | None
    facebook: str | None


async def farm_export_page(
    session: AsyncSession, *, limit: int, offset: int
) -> list[FarmExportRow]:
    """Farms by name (farm_id breaks ties), with their place names in English."""
    result = await session.execute(
        text(
            """
            SELECT f.farm_name, f.found_date, f.total_area,
                   p.province_name_en, d.district_name_en, s.subdistrict_name_en,
                   f.contact_name, f.phone_number, f.line, f.facebook
            FROM agriculture.farm f
            LEFT JOIN ref.province_constant p ON p.province_id = f.province_id
            LEFT JOIN ref.district_constant d ON d.district_id = f.district_id
            LEFT JOIN ref.subdistrict_constant s ON s.subdistrict_id = f.subdistrict_id
            ORDER BY f.farm_name, f.farm_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset},
    )
    return [FarmExportRow(**row._mapping) for row in result]


@dataclass(frozen=True, slots=True)
class HarvestExportRow:
    """One graded part of a harvest for the export's harvest sheet (RPT-01)."""

    harvest_date: datetime | None
    farm_name: str | None
    grade_code: str
    quantity_kg: Decimal | None
    weight_gram_per_pod: Decimal | None
    is_clean: bool | None
    is_appropriate_size: bool | None
    is_sprout: bool | None
    is_dry: bool | None
    is_shriveled: bool | None
    cut_test_result: str | None


async def harvest_export_page(
    session: AsyncSession, *, limit: int, offset: int
) -> list[HarvestExportRow]:
    """One row per harvest and grade, newest harvest first. harvest_id and then
    the grade break ties, so a page never repeats or skips a row."""
    result = await session.execute(
        text(
            """
            SELECT h.harvest_date, f.farm_name, g.grade_code, g.quantity_kg,
                   g.weight_gram_per_pod, g.is_clean, g.is_appropriate_size,
                   g.is_sprout, g.is_dry, g.is_shriveled, g.cut_test_result
            FROM collection.harvest h
            JOIN collection.harvest_grade_detail g ON g.harvest_id = h.harvest_id
            JOIN agriculture.farm f ON f.farm_id = h.farm_id
            ORDER BY h.harvest_date DESC, h.harvest_id, g.grade_code
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset},
    )
    return [HarvestExportRow(**row._mapping) for row in result]
