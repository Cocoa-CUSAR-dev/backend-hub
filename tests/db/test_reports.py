"""src/db/reports.py: the raw-data export's read-only joins (RPT-01)."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from tests.db.conftest import insert


async def _place(session: AsyncSession) -> dict[str, UUID]:
    province = await insert(session, "ref.province_constant", province_name_en="Chumphon")
    district = await insert(
        session, "ref.district_constant", province_id=province, district_name_en="Mueang"
    )
    subdistrict = await insert(
        session, "ref.subdistrict_constant", district_id=district, subdistrict_name_en="Tha Taphao"
    )
    assert province and district and subdistrict
    return {"province_id": province, "district_id": district, "subdistrict_id": subdistrict}


async def _farm(session: AsyncSession, name: str, place: dict[str, UUID]) -> UUID:
    farm_id = await insert(session, "agriculture.farm", farm_name=name, **place)
    assert farm_id is not None
    return farm_id


async def test_farms_by_name_with_english_place_names(db_session: AsyncSession) -> None:
    place = await _place(db_session)
    tag = uuid4().hex[:6]
    await _farm(db_session, f"zz-{tag}-b", place)
    await _farm(db_session, f"zz-{tag}-a", place)

    rows = await db.reports.farm_export_page(db_session, limit=500)

    mine = [r for r in rows if r.farm_name and r.farm_name.startswith(f"zz-{tag}")]
    assert [r.farm_name for r in mine] == [f"zz-{tag}-a", f"zz-{tag}-b"]
    assert (mine[0].province_name_en, mine[0].district_name_en, mine[0].subdistrict_name_en) == (
        "Chumphon",
        "Mueang",
        "Tha Taphao",
    )


async def test_farm_pages_dont_overlap(db_session: AsyncSession) -> None:
    place = await _place(db_session)
    for i in range(3):
        await _farm(db_session, f"page-{i}", place)

    first = await db.reports.farm_export_page(db_session, limit=2)
    rest = await db.reports.farm_export_page(db_session, limit=500, after=first[-1])

    mine = [r.farm_name for r in first + rest if r.farm_name and r.farm_name.startswith("page-")]
    assert sorted(mine) == ["page-0", "page-1", "page-2"]  # each once: no repeat, no skip


async def _every_farm(
    session: AsyncSession, size: int, start: db.reports.FarmExportRow | None = None
) -> list[db.reports.FarmExportRow]:
    """Every farm after `start`, read `size` at a time as the export does."""
    rows: list[db.reports.FarmExportRow] = []
    while page := await db.reports.farm_export_page(
        session, limit=size, after=rows[-1] if rows else start
    ):
        rows += page
    return rows


async def test_farm_pages_walk_past_farms_with_no_name(db_session: AsyncSession) -> None:
    # NULL names sort last; a page that ends on one must not lose the others.
    place = await _place(db_session)
    named = [await _farm(db_session, f"nn-{i}", place) for i in range(2)]
    nameless = [await insert(db_session, "agriculture.farm", **place) for _ in range(3)]
    everyone = await db.reports.farm_export_page(db_session, limit=10_000)

    for size in (1, 2, 3):
        rows = await _every_farm(db_session, size)
        assert [r.farm_id for r in rows] == [r.farm_id for r in everyone]
    ids = [r.farm_id for r in everyone]
    assert set(named) | set(nameless) <= set(ids)
    assert [r.farm_name for r in everyone][-len(nameless) :] == [None] * len(nameless)
    # A farm added before the cursor mid-download isn't read; none repeats.
    first = await db.reports.farm_export_page(db_session, limit=1)
    await _farm(db_session, "", place)  # sorts before every other name
    rest = await _every_farm(db_session, 2, start=first[-1])
    assert len({r.farm_id for r in first + rest}) == len(first + rest)


async def test_harvests_one_row_per_grade_newest_first(db_session: AsyncSession) -> None:
    place = await _place(db_session)
    farm_id = await _farm(db_session, "สวน", place)
    hub_id = uuid4()
    old = await insert(
        db_session, "collection.harvest", hub_id=hub_id, farm_id=farm_id,
        harvest_date=datetime(2031, 1, 1),
    )  # fmt: skip
    new = await insert(
        db_session, "collection.harvest", hub_id=hub_id, farm_id=farm_id,
        harvest_date=datetime(2031, 2, 1),
    )  # fmt: skip
    for harvest_id, grade, kg in ((new, "B", 2), (new, "A", 1), (old, "C", 3)):
        await insert(
            db_session, "collection.harvest_grade_detail", harvest_id=harvest_id,
            grade_code=grade, quantity_kg=kg, is_clean=None,
        )  # fmt: skip

    rows = await db.reports.harvest_export_page(db_session, limit=3)

    assert [(r.harvest_date, r.grade_code) for r in rows] == [
        (datetime(2031, 2, 1), "A"),
        (datetime(2031, 2, 1), "B"),
        (datetime(2031, 1, 1), "C"),
    ]
    assert rows[0].farm_name == "สวน" and rows[0].is_clean is None


# ---- analytics (ANL-01..06) ------------------------------------------------------------


async def _harvests(session: AsyncSession) -> UUID:
    """Farm in its own province; 2031-01: A 10 + B 5 (one harvest), 2031-02: A 20."""
    place = await _place(session)
    farm_id = await _farm(session, "สวน analytics", place)
    hub_id = uuid4()
    jan = await insert(
        session, "collection.harvest", hub_id=hub_id, farm_id=farm_id,
        harvest_date=datetime(2031, 1, 10),
    )  # fmt: skip
    feb = await insert(
        session, "collection.harvest", hub_id=hub_id, farm_id=farm_id,
        harvest_date=datetime(2031, 2, 3),
    )  # fmt: skip
    for harvest_id, grade, kg in ((jan, "A", 10), (jan, "B", 5), (feb, "A", 20)):
        await insert(
            session, "collection.harvest_grade_detail", harvest_id=harvest_id,
            grade_code=grade, quantity_kg=kg,
        )  # fmt: skip
    return place["province_id"]


def _range(**more: object) -> db.reports.HarvestFilter:
    return db.reports.HarvestFilter(
        start=datetime(2031, 1, 1),
        end=datetime(2031, 3, 1),
        **more,  # type: ignore[arg-type]
    )


async def test_harvest_by_month_per_grade(db_session: AsyncSession) -> None:
    province = await _harvests(db_session)

    rows = await db.reports.harvest_by_month(db_session, "sum", _range(province_id=province))

    assert [(r.month.isoformat(), r.grade_code, r.value) for r in rows] == [
        ("2031-01-01", "A", 10.0),
        ("2031-01-01", "B", 5.0),
        ("2031-02-01", "A", 20.0),
    ]


async def test_count_counts_graded_parts_and_the_range_ends_before_end(
    db_session: AsyncSession,
) -> None:
    province = await _harvests(db_session)
    jan_only = db.reports.HarvestFilter(
        start=datetime(2031, 1, 1), end=datetime(2031, 2, 1), province_id=province
    )

    assert await db.reports.harvest_total(db_session, "count", _range(province_id=province)) == 3
    assert await db.reports.harvest_total(db_session, "count", jan_only) == 2
    avg = await db.reports.harvest_total(db_session, "avg", _range(province_id=province))
    assert avg == pytest.approx(35 / 3)
    assert (
        await db.reports.harvest_total(
            db_session, "sum", _range(province_id=province, grade_code="C")
        )
        == 0.0
    )


async def test_users_count_once_per_role(db_session: AsyncSession) -> None:
    role_name = f"analytics-{uuid4().hex[:6]}"
    role = await insert(db_session, "auth.role", role_name=role_name)
    other = await insert(db_session, "auth.role", role_name=f"analytics-{uuid4().hex[:6]}")
    two_roles = await insert(
        db_session, "auth.user_account", username=f"u-{uuid4()}", created_at=datetime(2031, 5, 2)
    )
    no_role = await insert(
        db_session, "auth.user_account", username=f"u-{uuid4()}", created_at=datetime(2031, 5, 3)
    )
    assert no_role is not None
    for r in (role, other):
        await insert(db_session, "auth.user_role", user_id=two_roles, role_id=r)

    window = {"start": datetime(2031, 5, 1), "end": datetime(2031, 6, 1)}
    rows = await db.reports.users_by_month(db_session, role_name=None, **window)

    # Two roles count twice; the account with no role doesn't count.
    assert [(r.month.isoformat(), r.count) for r in rows] == [("2031-05-01", 2)]
    assert await db.reports.users_total(db_session, role_name=None, **window) == 2
    assert await db.reports.users_total(db_session, role_name=role_name, **window) == 1


async def test_place_lookups(db_session: AsyncSession) -> None:
    place = await _place(db_session)
    farm_id = await _farm(db_session, "สวน place", place)

    farm = await db.agriculture.farm_place(db_session, farm_id)
    sub = await db.ref.subdistrict_place(db_session, place["subdistrict_id"])

    assert farm is not None and farm.subdistrict_id == place["subdistrict_id"]
    assert sub == db.ref.PlaceIds(
        province_id=place["province_id"], district_id=place["district_id"]
    )
    assert (
        await db.ref.province_of_district(db_session, place["district_id"]) == place["province_id"]
    )
    assert await db.agriculture.farm_place(db_session, uuid4()) is None
    assert await db.ref.subdistrict_place(db_session, uuid4()) is None


async def test_harvest_pages_walk_past_harvests_with_no_date(db_session: AsyncSession) -> None:
    # NULL dates sort first (DESC); every grade appears once at any page size.
    place = await _place(db_session)
    farm_id = await _farm(db_session, "สวน", place)
    hub_id = uuid4()
    for when in (None, None, datetime(2032, 1, 1), datetime(2032, 1, 1), datetime(2031, 1, 1)):
        harvest = await insert(
            db_session, "collection.harvest", hub_id=hub_id, farm_id=farm_id, harvest_date=when
        )
        for grade in ("A", "B"):
            await insert(
                db_session, "collection.harvest_grade_detail", harvest_id=harvest,
                grade_code=grade, quantity_kg=1,
            )  # fmt: skip
    everyone = await db.reports.harvest_export_page(db_session, limit=10_000)

    for size in (1, 2, 3, 4):
        rows: list[db.reports.HarvestExportRow] = []
        while page := await db.reports.harvest_export_page(
            db_session, limit=size, after=rows[-1] if rows else None
        ):
            rows += page
        assert [(r.harvest_id, r.grade_code) for r in rows] == [
            (r.harvest_id, r.grade_code) for r in everyone
        ]
    dates = [r.harvest_date for r in everyone]
    assert dates.index(datetime(2032, 1, 1)) > max(i for i, d in enumerate(dates) if d is None)
