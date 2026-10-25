"""Read-only queries that join many schemas (analytics, Excel export).

Owns: no tables. Never writes. If a query only touches one schema, it belongs
in that schema's file instead. scripts/check_conventions.py lets this file read
any table and fails it on INSERT / UPDATE / DELETE.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
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


# ---- analytics (ANL-01..06) ---------------------------------------------------------

# Only these aggregates, so the metric never comes from a caller as SQL.
_METRICS = {
    "sum": "SUM(g.quantity_kg)",
    "avg": "AVG(g.quantity_kg)",
    # One per graded part of a harvest, as web-backend counted "harvests".
    "count": "COUNT(*)",
}


@dataclass(frozen=True, slots=True)
class HarvestFilter:
    """Which harvests count. Harvest times at or after `start`, before `end`.

    `spatial` only keeps farms that have a location (storage.geo), and with
    `area` (a GeoJSON geometry) only those inside it, as web-backend's map
    dashboard did; the place filters are not used with it.
    """

    start: datetime
    end: datetime
    province_id: UUID | None = None
    district_id: UUID | None = None
    subdistrict_id: UUID | None = None
    farm_id: UUID | None = None
    grade_code: str | None = None
    spatial: bool = False
    area: str | None = None


@dataclass(frozen=True, slots=True)
class MonthGradeValue:
    month: date
    grade_code: str
    value: float


def _harvest_where(f: HarvestFilter) -> tuple[str, str, dict[str, Any]]:
    """(extra JOIN, WHERE clause, params) for a filter."""
    params: dict[str, Any] = {"start": f.start, "end": f.end}
    where = ["h.harvest_date >= :start", "h.harvest_date < :end"]
    for column, value in (
        ("f.province_id", f.province_id),
        ("f.district_id", f.district_id),
        ("f.subdistrict_id", f.subdistrict_id),
        ("h.farm_id", f.farm_id),
        ("g.grade_code", f.grade_code),
    ):
        if value is not None:
            name = column.split(".")[1]
            where.append(f"{column} = :{name}")
            params[name] = value
    join = ""
    if f.spatial:
        join = "JOIN storage.geo geo ON geo.geo_id = f.geo_id"
        if f.area is not None:
            where.append("ST_Intersects(geo.geom, ST_SetSRID(ST_GeomFromGeoJSON(:area), 4326))")
            params["area"] = f.area
    return join, " AND ".join(where), params


async def harvest_by_month(
    session: AsyncSession, metric: str, f: HarvestFilter
) -> list[MonthGradeValue]:
    """The metric per month and grade, months with nothing left out."""
    join, where, params = _harvest_where(f)
    result = await session.execute(
        text(
            f"""
            SELECT date_trunc('month', h.harvest_date)::date AS month, g.grade_code,
                   {_METRICS[metric]} AS value
            FROM collection.harvest h
            JOIN collection.harvest_grade_detail g ON g.harvest_id = h.harvest_id
            JOIN agriculture.farm f ON f.farm_id = h.farm_id
            {join}
            WHERE {where}
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ),
        params,
    )
    return [
        MonthGradeValue(month=row.month, grade_code=row.grade_code, value=float(row.value or 0))
        for row in result
    ]


async def harvest_total(session: AsyncSession, metric: str, f: HarvestFilter) -> float:
    """The metric over the whole range; 0 when nothing matches."""
    join, where, params = _harvest_where(f)
    result = await session.execute(
        text(
            f"""
            SELECT {_METRICS[metric]}
            FROM collection.harvest h
            JOIN collection.harvest_grade_detail g ON g.harvest_id = h.harvest_id
            JOIN agriculture.farm f ON f.farm_id = h.farm_id
            {join}
            WHERE {where}
            """
        ),
        params,
    )
    return float(result.scalar() or 0)


@dataclass(frozen=True, slots=True)
class MonthCount:
    month: date
    count: int


_USERS = """
    FROM auth.user_account u
    JOIN auth.user_role ur ON ur.user_id = u.user_id
    JOIN auth.role r ON r.role_id = ur.role_id
    WHERE u.created_at >= :start AND u.created_at < :end
      AND (CAST(:role_name AS text) IS NULL OR r.role_name = :role_name)
"""


async def users_by_month(
    session: AsyncSession, *, start: datetime, end: datetime, role_name: str | None
) -> list[MonthCount]:
    """Accounts created per month. Like web-backend, an account counts once per
    role it holds, and an account with no role doesn't count."""
    result = await session.execute(
        text(
            f"SELECT date_trunc('month', u.created_at)::date AS month, COUNT(*) AS n {_USERS}"
            " GROUP BY 1 ORDER BY 1"
        ),
        {"start": start, "end": end, "role_name": role_name},
    )
    return [MonthCount(month=row.month, count=int(row.n)) for row in result]


async def users_total(
    session: AsyncSession, *, start: datetime, end: datetime, role_name: str | None
) -> int:
    result = await session.execute(
        text(f"SELECT COUNT(*) {_USERS}"),
        {"start": start, "end": end, "role_name": role_name},
    )
    return int(result.scalar() or 0)
