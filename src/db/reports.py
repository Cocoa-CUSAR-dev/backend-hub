"""Read-only queries that join many schemas (analytics, Excel export).

Owns: no tables. Never writes. If a query only touches one schema, it belongs
in that schema's file instead. scripts/check_conventions.py lets this file read
any table and fails it on INSERT / UPDATE / DELETE.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class FarmExportRow:
    """One farm for the raw-data export's farm sheet (RPT-01). farm_id is not
    written to the sheet: it is where the next page starts."""

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
    farm_id: UUID | None = None


# Keyset paging: each page starts after the previous page's last row instead of
# skipping OFFSET rows, so a farm or harvest added or removed mid-download can't
# shift a page (no row twice, none missed) and a late page costs what the first
# does. The conditions follow the ORDER BY, NULLs included: farm_name sorts
# NULLs last (ASC), harvest_date NULLs first (DESC).
_FARMS_AFTER_NAME = (
    "WHERE (f.farm_name > :after_name"
    " OR (f.farm_name = :after_name AND f.farm_id > :after_id)"
    " OR f.farm_name IS NULL)"
)
_FARMS_AFTER_NULL_NAME = "WHERE f.farm_name IS NULL AND f.farm_id > :after_id"


async def farm_export_page(
    session: AsyncSession, *, limit: int, after: FarmExportRow | None = None
) -> list[FarmExportRow]:
    """Farms by name (farm_id breaks ties), with their place names in English;
    the page after `after`, the first page when None."""
    where = ""
    params: dict[str, object] = {}
    if after is not None and after.farm_name is None:
        where, params = _FARMS_AFTER_NULL_NAME, {"after_id": after.farm_id}
    elif after is not None:
        where = _FARMS_AFTER_NAME
        params = {"after_name": after.farm_name, "after_id": after.farm_id}
    result = await session.execute(
        text(
            """
            SELECT f.farm_name, f.found_date, f.total_area,
                   p.province_name_en, d.district_name_en, s.subdistrict_name_en,
                   f.contact_name, f.phone_number, f.line, f.facebook, f.farm_id
            FROM agriculture.farm f
            LEFT JOIN ref.province_constant p ON p.province_id = f.province_id
            LEFT JOIN ref.district_constant d ON d.district_id = f.district_id
            LEFT JOIN ref.subdistrict_constant s ON s.subdistrict_id = f.subdistrict_id
            """
            + where
            + """
            ORDER BY f.farm_name, f.farm_id
            LIMIT :limit
            """
        ),
        {"limit": limit, **params},
    )
    return [FarmExportRow(**row._mapping) for row in result]


@dataclass(frozen=True, slots=True)
class HarvestExportRow:
    """One graded part of a harvest for the export's harvest sheet (RPT-01).
    harvest_id is not written to the sheet: it is where the next page starts."""

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
    harvest_id: UUID | None = None


_HARVESTS_AFTER_DATE = (
    "WHERE (h.harvest_date < :after_date"
    " OR (h.harvest_date = :after_date"
    " AND (h.harvest_id, g.grade_code) > (:after_id, :after_grade)))"
)
_HARVESTS_AFTER_NULL_DATE = (
    "WHERE (h.harvest_date IS NOT NULL OR (h.harvest_id, g.grade_code) > (:after_id, :after_grade))"
)


async def harvest_export_page(
    session: AsyncSession, *, limit: int, after: HarvestExportRow | None = None
) -> list[HarvestExportRow]:
    """One row per harvest and grade, newest harvest first. harvest_id and then
    the grade break ties, so a page never repeats or skips a row; the page
    after `after`, the first page when None."""
    where = ""
    params: dict[str, object] = {}
    if after is not None:
        where = _HARVESTS_AFTER_NULL_DATE if after.harvest_date is None else _HARVESTS_AFTER_DATE
        params = {"after_id": after.harvest_id, "after_grade": after.grade_code}
        if after.harvest_date is not None:
            params["after_date"] = after.harvest_date
    result = await session.execute(
        text(
            """
            SELECT h.harvest_date, f.farm_name, g.grade_code, g.quantity_kg,
                   g.weight_gram_per_pod, g.is_clean, g.is_appropriate_size,
                   g.is_sprout, g.is_dry, g.is_shriveled, g.cut_test_result, h.harvest_id
            FROM collection.harvest h
            JOIN collection.harvest_grade_detail g ON g.harvest_id = h.harvest_id
            JOIN agriculture.farm f ON f.farm_id = h.farm_id
            """
            + where
            + """
            ORDER BY h.harvest_date DESC, h.harvest_id, g.grade_code
            LIMIT :limit
            """
        ),
        {"limit": limit, **params},
    )
    return [HarvestExportRow(**row._mapping) for row in result]
