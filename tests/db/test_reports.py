"""src/db/reports.py: the raw-data export's read-only joins (RPT-01)."""

from datetime import datetime
from uuid import UUID, uuid4

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
