"""Dropdown choices for the mobile app's forms, by key.

Ported from mobile-backend's RefHandler.GetConstants (GET /constants/:key).
The app asks for the form field's name minus "_id" (plot_id -> "plot"), so
every key Go answered is answered here, with Go's JSON field names. A NULL
comes back as "" (false for a flag), as Go's structs wrote it.

- Reference tables (ref.*_constant) and the address lists are open to any
  signed-in user: a brand-new account picks its province while registering
  a role (CONVENTIONS.md §5).
- The caller's own farms/plots, hubs/harvests and stations/batches need
  read:farm:own, read:hub:own and read:station:own. Go listed them for any
  signed-in user, filtered to the caller.

Two Go bugs are not carried over: pest_disease's "name" was always empty
(Go read a column that doesn't exist, the real one is pest_disease_name),
and "location" always failed (Go read sd.zip_code; the column is zipcode).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from src import db
from src.actor import Actor, require
from src.database import Session
from src.exceptions import Forbidden, Invalid, NotFound

UNKNOWN_KEY = "Constant key not found"
_NO_PLACE = "กรุณาระบุ zip_code หรือ subdistrict_id"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True, slots=True)
class _Table:
    table: str
    # (column, JSON field) pairs, in Go's field order.
    fields: tuple[tuple[str, str], ...]
    flags: frozenset[str] = frozenset()
    # The query parameter that filters this table by the column of the same name.
    filter_by: str | None = None


def _plain(table: str, *columns: str, flags: frozenset[str] = frozenset()) -> _Table:
    return _Table(table, tuple((c, c) for c in columns), flags)


_TABLES: dict[str, _Table] = {
    "air_exposure_type": _plain(
        "air_exposure_type_constant", "air_exposure_type_id", "air_exposure_type_name"
    ),
    "breed": _plain("breed_constant", "breed_id", "breed_name"),
    "chem_bio": _plain("chem_bio_constant", "chem_bio_id", "chem_bio_name", "description"),
    "drying_facility": _plain(
        "drying_facility_constant", "drying_facility_type_id", "drying_facility_type_name"
    ),
    "farm_activity_type": _plain(
        "farm_activity_type_constant", "farm_activity_type_id", "farm_activity_type_name"
    ),
    "fertilizer_stage": _plain(
        "fertilizer_application_stage_constant",
        "fertilizer_application_stage_id",
        "fertilizer_application_stage_name_th",
        "fertilizer_application_stage_name_en",
        "fertilizer_application_stage_description",
    ),
    "fertilizer": _plain(
        "fertilizer_constant",
        "fertilizer_id",
        "fertilizer_name",
        "description",
        "is_organic",
        flags=frozenset({"is_organic"}),
    ),
    "grade": _plain(
        "grade_constant", "grade_id", "grade_name", "is_fail", flags=frozenset({"is_fail"})
    ),
    "hole_filler": _plain(
        "hole_filler_material_constant", "hole_filler_material_id", "hole_filler_material_name"
    ),
    "land_type": _plain("land_type_constant", "land_type_id", "land_type_name"),
    "location_type": _plain("location_type_constant", "location_type_id", "location_type_name"),
    "pest_disease": _Table(
        "pest_disease_constant",
        (
            ("pest_disease_id", "pest_disease_id"),
            ("pest_disease_name", "name"),
            ("type", "type"),
            ("description", "description"),
        ),
    ),
    "processing_activity_type": _plain(
        "processing_activity_type_constant",
        "processing_activity_type_id",
        "processing_activity_type_name",
    ),
    "processing_defect": _plain(
        "processing_defect_constant", "processing_defect_id", "processing_defect_name"
    ),
    "soil_type": _plain("soil_type_constant", "soil_type_id", "soil_type_name"),
    "tank_material": _plain("tank_material_constant", "tank_material_id", "tank_material_name"),
    "water_source": _plain("water_source_constant", "water_source_id", "water_source_name"),
    "watering_system": _plain(
        "watering_system_constant", "watering_system_id", "watering_system_name"
    ),
    "weather_condition": _plain(
        "weather_condition_constant", "weather_condition_id", "weather_condition_name"
    ),
    "province": _plain(
        "province_constant", "province_id", "province_name_th", "province_name_en", "description"
    ),
    "district": _Table(
        "district_constant",
        tuple(
            (c, c)
            for c in (
                "district_id",
                "district_name_th",
                "district_name_en",
                "province_id",
                "description",
            )
        ),
        filter_by="province_id",
    ),
    "subdistrict": _Table(
        "subdistrict_constant",
        tuple(
            (c, c)
            for c in (
                "subdistrict_id",
                "subdistrict_name_th",
                "subdistrict_name_en",
                "district_id",
                "description",
            )
        ),
        filter_by="district_id",
    ),
}

_LOCATION_FIELDS = (
    "subdistrict_id",
    "subdistrict_name",
    "district_name",
    "province_name",
    "zip_code",
    "full_name",
)


@dataclass(frozen=True, slots=True)
class Filters:
    """The optional query parameters; an empty string counts as not given, as in Go."""

    province_id: str | None = None
    district_id: str | None = None
    farm_id: str | None = None
    hub_id: str | None = None
    station_id: str | None = None
    zip_code: str | None = None
    subdistrict_id: str | None = None

    def get(self, name: str) -> str | None:
        value: str | None = getattr(self, name)
        return value or None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _row(
    row: dict[str, Any], fields: tuple[tuple[str, str], ...], flags: frozenset[str]
) -> dict[str, Any]:
    return {
        out: bool(row[column]) if column in flags else _text(row[column]) for column, out in fields
    }


def buddhist_label(moment: datetime | None) -> str | None:
    """Go's TO_CHAR(t + 543 years, 'DD Mon ') || year + 543: "05 Apr 2569"."""
    if moment is None:
        return None
    year = moment.year + 543
    try:
        shifted = moment.replace(year=year)
    except ValueError:  # 29 Feb in a year that has none: Postgres gives the 28th
        shifted = moment.replace(year=year, day=28)
    return f"{shifted.day:02d} {_MONTHS[shifted.month - 1]} {year}"


def _named(first: str | None, moment: datetime | None) -> str:
    """'<first> (<label>)', or "" when either part is missing (Go's NULL)."""
    label = buddhist_label(moment)
    return "" if first is None or label is None else f"{first} ({label})"


def _caller(actor: Actor) -> UUID:
    if actor.user_id is None:
        raise Forbidden("A user is required")
    return actor.user_id


async def _own_rows(
    session: Session, actor: Actor, key: str, filters: Filters
) -> list[dict[str, Any]] | None:
    """The caller's own farms/plots/hubs/harvests/stations/batches, or None
    when `key` is not one of those."""
    if key in ("farm", "plot"):
        require(actor, "read:farm:own")
        if key == "farm":
            farms = await db.agriculture.farm_choices(session, _caller(actor))
            return [{"farm_id": str(i), "farm_name": _text(n)} for i, n in farms]
        plots = await db.agriculture.plot_choices(session, _caller(actor), filters.get("farm_id"))
        return [{"plot_id": str(i), "plot_name": _text(n)} for i, n in plots]
    if key in ("hub", "harvest"):
        require(actor, "read:hub:own")
        if key == "hub":
            hubs = await db.processing.hub_choices(session, _caller(actor))
            return [{"hub_id": str(i), "hub_name": _text(n)} for i, n in hubs]
        hub_ids = await db.processing.hub_ids_of_collector(session, _caller(actor))
        harvests = await db.collection.harvest_choices(session, hub_ids, filters.get("hub_id"))
        names = await db.agriculture.farm_names(
            session, list({farm for _, farm, _ in harvests if farm is not None})
        )
        return [
            {"harvest_id": str(i), "name": _named(names.get(farm) if farm else None, when)}
            for i, farm, when in harvests
            if farm in names  # Go joined the farm: a harvest with no farm row is left out
        ]
    if key in ("processing_station", "batch"):
        require(actor, "read:station:own")
        if key == "processing_station":
            stations = await db.processing.station_choices(session, _caller(actor))
            return [
                {"processing_station_id": str(i), "processing_station_name": _text(n)}
                for i, n in stations
            ]
        batches = await db.processing.batch_choices(
            session, _caller(actor), filters.get("station_id")
        )
        return [{"batch_id": str(i), "name": _named(o, when)} for i, o, when in batches]
    return None


async def constants(
    session: Session, actor: Actor, key: str, filters: Filters | None = None
) -> list[dict[str, Any]]:
    """The choices for one dropdown key, in Go's shape. NotFound for an unknown key."""
    _caller(actor)  # any signed-in user; the own-row keys check their permission below
    filters = filters or Filters()
    table = _TABLES.get(key)
    if table is not None:
        value = filters.get(table.filter_by) if table.filter_by else None
        rows = await db.ref.constant_rows(
            session,
            table.table,
            [column for column, _ in table.fields],
            where=(table.filter_by, value) if table.filter_by and value else None,
        )
        return [_row(row, table.fields, table.flags) for row in rows]
    if key == "location":
        zip_code, subdistrict_id = filters.get("zip_code"), filters.get("subdistrict_id")
        if zip_code is None and subdistrict_id is None:
            raise Invalid(_NO_PLACE, fields={"zip_code": _NO_PLACE})
        rows = await db.ref.locations(session, zip_code=zip_code, subdistrict_id=subdistrict_id)
        return [{field: _text(row[field]) for field in _LOCATION_FIELDS} for row in rows]
    own = await _own_rows(session, actor, key, filters)
    if own is None:
        raise NotFound(UNKNOWN_KEY)
    return own
