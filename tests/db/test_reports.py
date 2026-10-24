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

    rows = await db.reports.farm_export_page(db_session, limit=500, offset=0)

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

    first = await db.reports.farm_export_page(db_session, limit=2, offset=0)
    rest = await db.reports.farm_export_page(db_session, limit=500, offset=2)

    mine = [r.farm_name for r in first + rest if r.farm_name and r.farm_name.startswith("page-")]
    assert sorted(mine) == ["page-0", "page-1", "page-2"]  # each once: no repeat, no skip


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

    rows = await db.reports.harvest_export_page(db_session, limit=3, offset=0)

    assert [(r.harvest_date, r.grade_code) for r in rows] == [
        (datetime(2031, 2, 1), "A"),
        (datetime(2031, 2, 1), "B"),
        (datetime(2031, 1, 1), "C"),
    ]
    assert rows[0].farm_name == "สวน" and rows[0].is_clean is None
