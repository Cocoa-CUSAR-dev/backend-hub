"""src/db/agriculture.py and src/db/storage.py against real Postgres."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from tests.db.conftest import insert

NOW = datetime(2026, 10, 22, 3, 0)


async def _farmer(session: AsyncSession) -> UUID:
    user_id = await insert(session, "auth.user_account", username=f"farmer-{uuid4()}")
    assert user_id is not None
    return user_id


async def _farm(session: AsyncSession, owner: UUID | None, name: str = "Farm") -> UUID:
    farm_id = uuid4()
    await db.agriculture.insert_farm(
        session, farm_id, farm_name=name, created_at=NOW, updated_at=NOW
    )
    if owner is not None:
        await db.agriculture.link_farmer(session, owner, farm_id)
    return farm_id


async def _plot(session: AsyncSession, farm_id: UUID, name: str = "Plot") -> UUID:
    plot_id = uuid4()
    await db.agriculture.insert_plot(
        session, plot_id, farm_id=farm_id, plot_name=name, created_at=NOW, updated_at=NOW
    )
    return plot_id


async def test_insert_farm_writes_the_given_columns(db_session: AsyncSession) -> None:
    farm_id = uuid4()
    province = uuid4()
    await db.agriculture.insert_farm(
        db_session,
        farm_id,
        farm_name="สวนเหนือ",
        province_id=province,
        found_date=date(2026, 1, 2),
        zip_code="50000",
        created_at=NOW,
        updated_at=NOW,
    )

    [row] = await db.agriculture.farms(db_session, farmer_id=None, limit=10, offset=0)

    assert (row.farm_id, row.farm_name, row.province_id, row.found_date, row.zip_code) == (
        farm_id,
        "สวนเหนือ",
        province,
        date(2026, 1, 2),
        "50000",
    )
    assert row.created_at == NOW


async def test_farms_of_a_farmer_are_only_theirs(db_session: AsyncSession) -> None:
    me = await _farmer(db_session)
    other = await _farmer(db_session)
    mine = sorted([await _farm(db_session, me), await _farm(db_session, me)])
    theirs = await _farm(db_session, other)

    my_farms = await db.agriculture.farms(db_session, farmer_id=me, limit=10, offset=0)
    every_farm = await db.agriculture.farms(db_session, farmer_id=None, limit=10, offset=0)

    assert [f.farm_id for f in my_farms] == mine
    assert {f.farm_id for f in every_farm} == {*mine, theirs}


async def test_farms_page_in_farm_id_order(db_session: AsyncSession) -> None:
    me = await _farmer(db_session)
    ids = sorted([await _farm(db_session, me) for _ in range(3)])

    first = await db.agriculture.farms(db_session, farmer_id=me, limit=2, offset=0)
    rest = await db.agriculture.farms(db_session, farmer_id=me, limit=2, offset=2)

    assert [f.farm_id for f in first + rest] == ids


async def test_plots_of_farms_and_of_a_farmer(db_session: AsyncSession) -> None:
    me = await _farmer(db_session)
    other = await _farmer(db_session)
    my_farm = await _farm(db_session, me)
    their_farm = await _farm(db_session, other)
    mine = sorted([await _plot(db_session, my_farm), await _plot(db_session, my_farm)])
    theirs = await _plot(db_session, their_farm)

    of_farm = await db.agriculture.plots_of_farms(db_session, [my_farm])
    of_me = await db.agriculture.plots(db_session, farmer_id=me, limit=10, offset=0)
    of_all = await db.agriculture.plots(db_session, farmer_id=None, limit=10, offset=0)

    assert [p.plot_id for p in of_farm] == mine
    assert [p.plot_id for p in of_me] == mine
    assert {p.plot_id for p in of_all} == {*mine, theirs}
    assert await db.agriculture.plots_of_farms(db_session, []) == []


async def test_plots_page_in_plot_id_order(db_session: AsyncSession) -> None:
    me = await _farmer(db_session)
    farm = await _farm(db_session, me)
    ids = sorted([await _plot(db_session, farm) for _ in range(3)])

    first = await db.agriculture.plots(db_session, farmer_id=me, limit=2, offset=0)
    rest = await db.agriculture.plots(db_session, farmer_id=me, limit=2, offset=2)

    assert [p.plot_id for p in first + rest] == ids


async def test_insert_plot_keeps_numbers_and_choices(db_session: AsyncSession) -> None:
    farm = await _farm(db_session, None)
    soil = uuid4()
    plot_id = uuid4()
    await db.agriculture.insert_plot(
        db_session,
        plot_id,
        farm_id=farm,
        plot_name="A",
        total_area=2.5,
        cocoa_planted_area=1.25,
        has_chemical_use=True,
        soil_type_id=soil,
        created_at=NOW,
        updated_at=NOW,
    )

    [row] = await db.agriculture.plots_of_farms(db_session, [farm])

    assert (row.plot_id, float(row.total_area or 0), float(row.cocoa_planted_area or 0)) == (
        plot_id,
        2.5,
        1.25,
    )
    assert (row.has_chemical_use, row.soil_type_id) == (True, soil)


async def test_ownership_and_existence(db_session: AsyncSession) -> None:
    me = await _farmer(db_session)
    other = await _farmer(db_session)
    farm = await _farm(db_session, me)

    assert await db.agriculture.farmer_owns_farm(db_session, me, farm) is True
    assert await db.agriculture.farmer_owns_farm(db_session, other, farm) is False
    assert await db.agriculture.farm_exists(db_session, farm) is True
    assert await db.agriculture.farm_exists(db_session, uuid4()) is False


# --- src/db/storage.py -------------------------------------------------------


async def test_insert_geo_passes_the_shape_through_st_geomfromtext(
    db_session: AsyncSession,
) -> None:
    me = await _farmer(db_session)
    await db.storage.insert_geo(
        db_session,
        uploaded_by=me,
        wkt="POINT(100.500000 13.700000)",
        code="farm-1",
        source_type="farm",
        area_sq_m=12.5,
        created_at=NOW.replace(tzinfo=UTC),
    )

    row = (
        await db_session.execute(
            text("SELECT geom, code, source_type, area_sq_m FROM storage.geo WHERE code = 'farm-1'")
        )
    ).one()

    # The test schema's ST_GeomFromText stand-in echoes SRID + WKT.
    assert tuple(row) == ("SRID=4326;POINT(100.500000 13.700000)", "farm-1", "farm", 12.5)


async def test_latest_file_path_per_row(db_session: AsyncSession) -> None:
    me = await _farmer(db_session)
    farm_a, farm_b, no_photo = uuid4(), uuid4(), uuid4()

    async def photo(ref_id: UUID, path: str, at: datetime, code: str = "farm_image") -> None:
        await db.storage.insert_file(
            db_session,
            file_name=path.rsplit("/", 1)[-1],
            table_name="agriculture.farm",
            ref_id=ref_id,
            code=code,
            original_name="x.jpg",
            file_extension="jpg",
            mime_type="image/jpeg",
            file_size=3,
            storage_path=path,
            checksum="abc",
            uploaded_by=me,
            created_at=at,
        )

    await photo(farm_a, "farms/a/old.jpg", datetime(2026, 10, 1))
    await photo(farm_a, "farms/a/new.jpg", datetime(2026, 10, 2))
    await photo(farm_b, "farms/b/only.jpg", datetime(2026, 10, 1))
    await photo(farm_b, "farms/b/other-kind.jpg", datetime(2026, 10, 3), code="something_else")

    paths = await db.storage.latest_file_paths(
        db_session,
        table_name="agriculture.farm",
        code="farm_image",
        ref_ids=[farm_a, farm_b, no_photo],
    )

    assert paths == {farm_a: "farms/a/new.jpg", farm_b: "farms/b/only.jpg"}
    assert (
        await db.storage.latest_file_paths(
            db_session, table_name="agriculture.farm", code="farm_image", ref_ids=[]
        )
        == {}
    )


async def test_farm_and_plot_ids_of_a_farmer_cover_only_their_farms(
    db_session: AsyncSession,
) -> None:
    me, other = await _farmer(db_session), await _farmer(db_session)
    my_farm, other_farm = await _farm(db_session, me), await _farm(db_session, other)
    my_plot, _ = await _plot(db_session, my_farm), await _plot(db_session, other_farm)

    assert await db.agriculture.farm_ids_of_farmer(db_session, me) == [my_farm]
    assert await db.agriculture.plot_ids_of_farmer(db_session, me) == [my_plot]
    assert await db.agriculture.farm_ids_of_farmer(db_session, await _farmer(db_session)) == []


async def test_plots_with_farm_name_can_narrow_to_a_farmer_and_a_farm(
    db_session: AsyncSession,
) -> None:
    me, other = await _farmer(db_session), await _farmer(db_session)
    north, south = await _farm(db_session, me, "north"), await _farm(db_session, me, "south")
    elsewhere = await _farm(db_session, other, "elsewhere")
    p_north, p_south = await _plot(db_session, north, "a"), await _plot(db_session, south, "b")
    p_else = await _plot(db_session, elsewhere, "c")

    def ids(rows: list[db.agriculture.PlotWithFarmRow]) -> list[UUID]:
        return [r.plot_id for r in rows]

    mine = await db.agriculture.plots_with_farm_name(db_session, farmer_id=me, farm_id=None)
    everyone = await db.agriculture.plots_with_farm_name(db_session, farmer_id=None, farm_id=None)
    one_farm = await db.agriculture.plots_with_farm_name(db_session, farmer_id=me, farm_id=south)
    wrong_farm = await db.agriculture.plots_with_farm_name(
        db_session, farmer_id=me, farm_id=elsewhere
    )

    assert ids(mine) == [p_north, p_south]  # ordered by farm name, then plot name
    assert [(r.farm_name, r.plot_name) for r in mine] == [("north", "a"), ("south", "b")]
    assert set(ids(everyone)) == {p_north, p_south, p_else}
    assert ids(one_farm) == [p_south]
    assert wrong_farm == []
