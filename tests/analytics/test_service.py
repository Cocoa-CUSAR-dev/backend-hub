"""analytics.service with the db layer faked: months, modes, place checks and
permissions. The SQL is in tests/db/test_reports.py, every route against
Kotlin's answers in tests/contract/ (K-27..K-41)."""

import json
from datetime import date
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src import db
from src.actor import Actor
from src.analytics import service
from src.db.agriculture import FarmPlace
from src.db.ref import PlaceIds
from src.db.reports import MonthCount, MonthGradeValue
from src.exceptions import Forbidden, Invalid

SESSION: Any = object()
RESEARCHER = Actor(
    user_id=uuid4(), roles=frozenset(), perms=frozenset({"read:report:all"}), channel="api"
)
FARMER = Actor(
    user_id=uuid4(), roles=frozenset(), perms=frozenset({"read:farm:own"}), channel="api"
)
P1, P2, D1, S1, F1 = (uuid4() for _ in range(5))


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {
        "harvest_by_month": AsyncMock(return_value=[]),
        "harvest_total": AsyncMock(return_value=0.0),
        "users_by_month": AsyncMock(return_value=[]),
        "users_total": AsyncMock(return_value=0),
        "farm_place": AsyncMock(return_value=None),
        "subdistrict_place": AsyncMock(return_value=None),
        "province_of_district": AsyncMock(return_value=None),
    }
    for name in ("harvest_by_month", "harvest_total", "users_by_month", "users_total"):
        monkeypatch.setattr(db.reports, name, fakes[name])
    monkeypatch.setattr(db.agriculture, "farm_place", fakes["farm_place"])
    for name in ("subdistrict_place", "province_of_district"):
        monkeypatch.setattr(db.ref, name, fakes[name])
    return fakes


def _rows() -> list[MonthGradeValue]:
    return [
        MonthGradeValue(date(2026, 8, 1), "A", 120.5),
        MonthGradeValue(date(2026, 10, 1), "A", 140.0),
        MonthGradeValue(date(2026, 9, 1), "B", 60.0),
    ]


# ---- months -----------------------------------------------------------------------------


def test_month_range_covers_every_month_and_crosses_a_year() -> None:
    r = service.month_range("2026-11", "2027-02")
    assert [r.label(m) for m in r.months] == ["2026-11", "2026-12", "2027-01", "2027-02"]
    assert (r.start.isoformat(), r.end.isoformat()) == (
        "2026-11-01T00:00:00",
        "2027-03-01T00:00:00",
    )


@pytest.mark.parametrize(
    ("from_", "to", "field"),
    [(None, "2026-10", "from"), ("2026-13", "2026-10", "from"), ("2026-08", "Oct", "to"),
     ("2026-10", "2026-08", "from"),
     # years with no month after them, or before them: 422, not 500
     ("0000-01", "2026-10", "from"), ("2026-01", "9999-12", "to"),
     ("1999-12", "2026-10", "from"), ("2026-01", "2101-01", "to"),
     # more than ten years at once
     ("2000-01", "2010-01", "to"), ("0001-01", "9999-11", "from")],
)  # fmt: skip
def test_bad_months_are_invalid(from_: str | None, to: str, field: str) -> None:
    with pytest.raises(Invalid) as e:
        service.month_range(from_, to)
    assert field in e.value.fields


def test_the_widest_range_is_ten_years_inside_2000_to_2100() -> None:
    assert len(service.month_range("2000-01", "2009-12").months) == 120
    assert service.month_range("2100-12", "2100-12").end.isoformat() == "2101-01-01T00:00:00"


# ---- harvest series and totals ------------------------------------------------------------


async def test_delta_fills_empty_months_per_grade(fake_db: dict[str, AsyncMock]) -> None:
    fake_db["harvest_by_month"].return_value = _rows()

    out = await service.harvest_series(
        SESSION, RESEARCHER, "delta", from_="2026-08", to="2026-10", place=service.Place()
    )

    assert out.title == "Harvest Weight per Month" and out.from_ == "2026-08"
    assert [(s.label, s.values) for s in out.series] == [
        ("Grade A", [120.5, 0.0, 140.0]),
        ("Grade B", [0.0, 60.0, 0.0]),
        ("Grade C", [0.0, 0.0, 0.0]),
    ]
    assert fake_db["harvest_by_month"].await_args.args[1] == "sum"


async def test_sum_is_the_running_total(fake_db: dict[str, AsyncMock]) -> None:
    fake_db["harvest_by_month"].return_value = _rows()

    out = await service.harvest_series(
        SESSION, RESEARCHER, "sum", from_="2026-08", to="2026-10", place=service.Place()
    )

    assert out.series[0].values == [120.5, 120.5, 260.5]


@pytest.mark.parametrize(
    ("mode", "metric", "unit"),
    [("average", "avg", "kg"), ("frequency", "count", "records")],
)
async def test_modes_pick_their_metric(
    fake_db: dict[str, AsyncMock], mode: str, metric: str, unit: str
) -> None:
    out = await service.harvest_series(
        SESSION, RESEARCHER, mode, from_="2026-08", to="2026-08", place=service.Place()
    )
    assert fake_db["harvest_by_month"].await_args.args[1] == metric
    assert out.series[0].unit == unit


async def test_one_grade_gives_one_series(fake_db: dict[str, AsyncMock]) -> None:
    out = await service.harvest_series(
        SESSION, RESEARCHER, "delta", from_="2026-08", to="2026-08", place=service.Place(),
        grade_code="A",
    )  # fmt: skip
    assert [s.label for s in out.series] == ["Grade A"]
    assert fake_db["harvest_by_month"].await_args.args[2].grade_code == "A"


async def test_total_labels_the_grade(fake_db: dict[str, AsyncMock]) -> None:
    fake_db["harvest_total"].return_value = 532.75

    out = await service.harvest_summary(
        SESSION, RESEARCHER, "total", from_="2026-08", to="2026-10", place=service.Place()
    )

    assert (out.title, out.data.label, out.data.value) == (
        "Total Harvest Weight",
        "All Grades",
        532.75,
    )


async def test_area_view_is_spatial_without_place_metadata(fake_db: dict[str, AsyncMock]) -> None:
    area = '{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}'

    out = await service.harvest_summary(
        SESSION, RESEARCHER, "count", from_="2026-08", to="2026-10", area=area
    )

    f = fake_db["harvest_total"].await_args.args[2]
    assert f.spatial and f.area == area and f.province_id is None
    assert out.metadata == {"type": "spatial_selection"}


SQUARE = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
HOLE = [[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.2]]


@pytest.mark.parametrize(
    "area",
    [
        {"type": "Polygon", "coordinates": [SQUARE]},
        {"type": "Polygon", "coordinates": [SQUARE, HOLE]},  # with a hole
        {"type": "Polygon", "coordinates": [[[100.5, 13.7, 2.0], [100.6, 13.7, 2.0],
                                             [100.6, 13.8, 2.0], [100.5, 13.7, 2.0]]]},
        {"type": "MultiPolygon", "coordinates": [[SQUARE], [SQUARE]]},
    ],
)  # fmt: skip
async def test_a_polygon_or_multipolygon_is_an_area(
    fake_db: dict[str, AsyncMock], area: dict[str, Any]
) -> None:
    geo_json = json.dumps(area)
    await service.harvest_series(
        SESSION, RESEARCHER, "delta", from_="2026-08", to="2026-08", area=geo_json
    )
    assert fake_db["harvest_by_month"].await_args.args[2].area == geo_json


@pytest.mark.parametrize(
    "area",
    [
        "not json",
        '"a string"',
        '{"no": "type"}',
        '{"type": "Foo"}',  # passed the old check, then PostGIS answered 500
        '{"type": "Point", "coordinates": [0, 0]}',
        '{"type": "Polygon"}',
        '{"type": "Polygon", "coordinates": []}',
        '{"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}',  # open ring
        '{"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 0]]]}',  # too few points
        '{"type": "Polygon", "coordinates": [[[0, 0], [1, "x"], [1, 1], [0, 0]]]}',
        '{"type": "Polygon", "coordinates": [[[0, 0], [true, 0], [1, 1], [0, 0]]]}',
        '{"type": "Polygon", "coordinates": [[[0, 0], [181, 0], [1, 1], [0, 0]]]}',
        '{"type": "Polygon", "coordinates": [[[0, 0], [0, 91], [1, 1], [0, 0]]]}',
        '{"type": "Polygon", "coordinates": [[[0], [1, 0], [1, 1], [0]]]}',
        '{"type": "MultiPolygon", "coordinates": []}',
        '{"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 1]]]]}',
        '{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}}',
    ],
)
async def test_a_bad_area_is_invalid(fake_db: dict[str, AsyncMock], area: str) -> None:
    with pytest.raises(Invalid) as e:
        await service.harvest_series(
            SESSION, RESEARCHER, "delta", from_="2026-08", to="2026-08", area=area
        )
    assert "geoJson" in e.value.fields


# ---- place checks ----------------------------------------------------------------------------


async def test_a_farm_fills_in_its_place(fake_db: dict[str, AsyncMock]) -> None:
    fake_db["farm_place"].return_value = FarmPlace(
        province_id=P1, district_id=D1, subdistrict_id=S1
    )

    out = await service.harvest_series(
        SESSION, RESEARCHER, "delta", from_="2026-08", to="2026-08",
        place=service.Place(farm_id=F1),
    )  # fmt: skip

    assert out.metadata == {
        "provinceId": str(P1), "districtId": str(D1), "subdistrictId": str(S1), "farmId": str(F1),
    }  # fmt: skip


@pytest.mark.parametrize(
    "place",
    [
        service.Place(farm_id=F1, province_id=P2),  # farm is in P1
        service.Place(subdistrict_id=S1, province_id=P2),  # subdistrict is in P1
        service.Place(district_id=D1, province_id=P2),  # district is in P1
    ],
)
async def test_places_that_dont_belong_together_are_invalid(
    fake_db: dict[str, AsyncMock], place: service.Place
) -> None:
    fake_db["farm_place"].return_value = FarmPlace(
        province_id=P1, district_id=D1, subdistrict_id=S1
    )
    fake_db["subdistrict_place"].return_value = PlaceIds(province_id=P1, district_id=D1)
    fake_db["province_of_district"].return_value = P1

    with pytest.raises(Invalid):
        await service.harvest_summary(
            SESSION, RESEARCHER, "total", from_="2026-08", to="2026-08", place=place
        )
    fake_db["harvest_total"].assert_not_awaited()


async def test_unknown_farm_is_invalid(fake_db: dict[str, AsyncMock]) -> None:
    with pytest.raises(Invalid):
        await service.harvest_series(
            SESSION, RESEARCHER, "delta", from_="2026-08", to="2026-08",
            place=service.Place(farm_id=F1),
        )  # fmt: skip


# ---- users -------------------------------------------------------------------------------------


async def test_user_series_running_total(fake_db: dict[str, AsyncMock]) -> None:
    fake_db["users_by_month"].return_value = [
        MonthCount(date(2026, 7, 1), 2),
        MonthCount(date(2026, 9, 1), 3),
    ]

    out = await service.user_series(SESSION, RESEARCHER, "sum", from_="2026-07", to="2026-10")

    assert out.series[0].label == "All Users"
    assert out.series[0].values == [2.0, 2.0, 5.0, 5.0]


async def test_user_summary_names_the_role(fake_db: dict[str, AsyncMock]) -> None:
    fake_db["users_total"].return_value = 2

    out = await service.user_summary(
        SESSION, RESEARCHER, "count", from_="2026-07", to="2026-10", role_name="farmer"
    )

    assert (out.data.label, out.data.value, out.data.unit) == ("New farmer Users", 2, "users")
    assert fake_db["users_total"].await_args.kwargs["role_name"] == "farmer"


# ---- permissions ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: service.harvest_series(SESSION, FARMER, "delta", from_="2026-08", to="2026-08"),
        lambda: service.harvest_summary(SESSION, FARMER, "total", from_="2026-08", to="2026-08"),
        lambda: service.user_series(SESSION, FARMER, "sum", from_="2026-08", to="2026-08"),
        lambda: service.user_summary(SESSION, FARMER, "count", from_="2026-08", to="2026-08"),
    ],
)
async def test_dashboard_is_for_researchers(fake_db: dict[str, AsyncMock], call: Any) -> None:
    with pytest.raises(Forbidden):
        await call()
    for mock in fake_db.values():
        mock.assert_not_awaited()
