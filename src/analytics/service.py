"""The researchers' dashboard: harvests and new users per month, and totals.

Ported from web-backend's HarvestAnalyticsService, SpatialHarvestAnalyticsService
and UserAnalyticsService. Its 15 routes become four functions with the mode as
a parameter (ANL-01..06, CONVENTIONS.md §4); the drawn-area (spatial) views are
the same harvest functions with an area.

Kept from Kotlin: titles, units and labels; one series per grade A, B, C (or
the one grade asked for), every month in the range, 0 for a month with
nothing; "sum" is the running total; harvests are counted per graded part and
users per role held. Changed: a missing or malformed month, a year outside 2000..2100,
more than 120 months and an inconsistent place answer 422
(Kotlin: 500 and 400).
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from itertools import accumulate
from typing import Any
from uuid import UUID

from src import db
from src.actor import Actor, require
from src.analytics.schemas import DataItemOut, SeriesOut, SummaryOut, TimeSeriesOut
from src.database import Session
from src.db.reports import HarvestFilter
from src.exceptions import Invalid

GRADES = ("A", "B", "C")

# mode -> (title, unit, metric, running total)
HARVEST_SERIES = {
    "sum": ("Running Total Harvest Weight per Month", "kg", "sum", True),
    "delta": ("Harvest Weight per Month", "kg", "sum", False),
    "average": ("Average Harvest Weight per Month", "kg", "avg", False),
    "frequency": ("Number of Harvests per Month", "records", "count", False),
}
HARVEST_SUMMARY = {
    "total": ("Total Harvest Weight", "kg", "sum"),
    "count": ("Total Number of Harvests", "records", "count"),
}
USER_SERIES = {
    "sum": ("Running Total User per Month", True),
    "delta": ("New User per Month", False),
}
USER_SUMMARY = {"count": "Total User"}

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
# The months a dashboard may ask about, and how many at once (ten years). Kotlin
# had no limit: year 0 or 10000 was a 500, and 0001-01..9999-11 built ~120,000
# months.
FIRST_YEAR, LAST_YEAR = 2000, 2100
MAX_MONTHS = 120
_HIERARCHY = (
    "The geographic hierarchy provided (Province/District/Subdistrict/Farm) "
    "is inconsistent or invalid."
)


@dataclass(frozen=True, slots=True)
class Range:
    first: date
    last: date
    months: list[date]

    @property
    def start(self) -> datetime:
        return datetime(self.first.year, self.first.month, 1)

    @property
    def end(self) -> datetime:
        """The first moment after the last month."""
        nxt = _next(self.last)
        return datetime(nxt.year, nxt.month, 1)

    def label(self, month: date) -> str:
        return f"{month.year:04d}-{month.month:02d}"


@dataclass(frozen=True, slots=True)
class Place:
    """The place filters as asked (web-app's query names)."""

    province_id: UUID | None = None
    district_id: UUID | None = None
    subdistrict_id: UUID | None = None
    farm_id: UUID | None = None


def _next(month: date) -> date:
    return date(month.year + month.month // 12, month.month % 12 + 1, 1)


def _month(name: str, value: str | None) -> date:
    m = _MONTH.match(value or "")
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise Invalid(fields={name: "Use a month like 2026-08"})
    year = int(m.group(1))
    if not FIRST_YEAR <= year <= LAST_YEAR:
        # Year 0 has no date, and the month after 9999-12 is year 10000.
        raise Invalid(fields={name: f"Use a year from {FIRST_YEAR} to {LAST_YEAR}"})
    return date(year, int(m.group(2)), 1)


def month_range(from_: str | None, to: str | None) -> Range:
    first, last = _month("from", from_), _month("to", to)
    if first > last:
        raise Invalid(fields={"from": "'from' must not be after 'to'"})
    if (last.year - first.year) * 12 + last.month - first.month + 1 > MAX_MONTHS:
        raise Invalid(fields={"to": f"Ask for at most {MAX_MONTHS} months at a time"})
    months, current = [], first
    while current <= last:
        months.append(current)
        current = _next(current)
    return Range(first=first, last=last, months=months)


async def _check_place(session: Session, place: Place) -> dict[str, Any]:
    """Refuse a place whose parts don't belong together, and fill in the
    levels above the most precise one given (Kotlin's LocationRepository)."""
    got = {
        "provinceId": place.province_id,
        "districtId": place.district_id,
        "subdistrictId": place.subdistrict_id,
        "farmId": place.farm_id,
    }
    if place.farm_id is not None:
        farm = await db.agriculture.farm_place(session, place.farm_id)
        if farm is None or any(
            asked is not None and asked != actual
            for asked, actual in (
                (place.subdistrict_id, farm.subdistrict_id),
                (place.district_id, farm.district_id),
                (place.province_id, farm.province_id),
            )
        ):
            raise Invalid(_HIERARCHY, fields={"farmId": _HIERARCHY})
        got |= {
            "provinceId": farm.province_id,
            "districtId": farm.district_id,
            "subdistrictId": farm.subdistrict_id,
        }
    elif place.subdistrict_id is not None:
        sub = await db.ref.subdistrict_place(session, place.subdistrict_id)
        if sub is None or any(
            asked is not None and asked != actual
            for asked, actual in (
                (place.district_id, sub.district_id),
                (place.province_id, sub.province_id),
            )
        ):
            raise Invalid(_HIERARCHY, fields={"subdistrictId": _HIERARCHY})
        got |= {"provinceId": sub.province_id, "districtId": sub.district_id}
    elif place.district_id is not None:
        province = await db.ref.province_of_district(session, place.district_id)
        if place.province_id is not None and province != place.province_id:
            raise Invalid(_HIERARCHY, fields={"districtId": _HIERARCHY})
        got["provinceId"] = province
    return {k: (str(v) if v is not None else None) for k, v in got.items()}


def _area(geo_json: str | None) -> str | None:
    if geo_json is None:
        return None
    try:
        parsed = json.loads(geo_json)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict) or "type" not in parsed:
        raise Invalid(fields={"geoJson": "Send a GeoJSON geometry, e.g. a Polygon"})
    return geo_json


async def _filter(
    session: Session,
    period: Range,
    place: Place | None,
    grade_code: str | None,
    area: str | None,
) -> tuple[HarvestFilter, dict[str, Any]]:
    if place is None:  # the map dashboard: an area instead of a place
        f = HarvestFilter(
            start=period.start,
            end=period.end,
            grade_code=grade_code,
            spatial=True,
            area=_area(area),
        )
        return f, {"type": "spatial_selection"}
    metadata = await _check_place(session, place)
    f = HarvestFilter(
        start=period.start,
        end=period.end,
        province_id=place.province_id,
        district_id=place.district_id,
        subdistrict_id=place.subdistrict_id,
        farm_id=place.farm_id,
        grade_code=grade_code,
    )
    return f, metadata


async def harvest_series(
    session: Session,
    actor: Actor,
    mode: str,
    *,
    from_: str | None,
    to: str | None,
    place: Place | None = None,
    grade_code: str | None = None,
    area: str | None = None,
) -> TimeSeriesOut:
    """One series per grade over every month in the range. `place=None` is the
    map dashboard: farms with a location, inside `area` if one is drawn."""
    require(actor, "read:report:all")
    title, unit, metric, running = HARVEST_SERIES[mode]
    period = month_range(from_, to)
    f, metadata = await _filter(session, period, place, grade_code, area)
    rows = await db.reports.harvest_by_month(session, metric, f)
    by_grade: dict[str, dict[date, float]] = {}
    for row in rows:
        by_grade.setdefault(row.grade_code, {})[row.month] = row.value
    series = []
    for grade in (grade_code,) if grade_code else GRADES:
        values = [by_grade.get(grade, {}).get(m, 0.0) for m in period.months]
        if running:
            values = list(accumulate(values))
        series.append(SeriesOut(label=f"Grade {grade}", values=values, unit=unit))
    return TimeSeriesOut(
        from_=period.label(period.first),
        to=period.label(period.last),
        title=title,
        metadata=metadata,
        series=series,
    )


async def harvest_summary(
    session: Session,
    actor: Actor,
    mode: str,
    *,
    from_: str | None,
    to: str | None,
    place: Place | None = None,
    grade_code: str | None = None,
    area: str | None = None,
) -> SummaryOut:
    require(actor, "read:report:all")
    title, unit, metric = HARVEST_SUMMARY[mode]
    period = month_range(from_, to)
    f, metadata = await _filter(session, period, place, grade_code, area)
    value = await db.reports.harvest_total(session, metric, f)
    label = f"Grade {grade_code}" if grade_code else "All Grades"
    return SummaryOut(
        from_=period.label(period.first),
        to=period.label(period.last),
        title=title,
        metadata=metadata,
        data=DataItemOut(label=label, value=value, unit=unit),
    )


async def user_series(
    session: Session,
    actor: Actor,
    mode: str,
    *,
    from_: str | None,
    to: str | None,
    role_name: str | None = None,
) -> TimeSeriesOut:
    require(actor, "read:report:all")
    title, running = USER_SERIES[mode]
    period = month_range(from_, to)
    rows = await db.reports.users_by_month(
        session, start=period.start, end=period.end, role_name=role_name
    )
    per_month = {row.month: float(row.count) for row in rows}
    values = [per_month.get(m, 0.0) for m in period.months]
    if running:
        values = list(accumulate(values))
    return TimeSeriesOut(
        from_=period.label(period.first),
        to=period.label(period.last),
        title=title,
        metadata={},
        series=[SeriesOut(label=role_name or "All Users", values=values, unit="users")],
    )


async def user_summary(
    session: Session,
    actor: Actor,
    mode: str,
    *,
    from_: str | None,
    to: str | None,
    role_name: str | None = None,
) -> SummaryOut:
    require(actor, "read:report:all")
    title = USER_SUMMARY[mode]
    period = month_range(from_, to)
    total = await db.reports.users_total(
        session, start=period.start, end=period.end, role_name=role_name
    )
    label = f"New {role_name} Users" if role_name else "Total New Users"
    return SummaryOut(
        from_=period.label(period.first),
        to=period.label(period.last),
        title=title,
        metadata={},
        data=DataItemOut(label=label, value=total, unit="users"),
    )
