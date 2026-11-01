"""ref.service.constants against real Postgres: Go's GET /constants/:key
shapes, its filters, and that the own-row keys never list someone else's."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.actor import Actor
from src.exceptions import Forbidden, Invalid, NotFound
from src.ref import service
from src.ref.service import Filters
from tests.db.conftest import insert

OWN = frozenset({"read:farm:own", "read:hub:own", "read:station:own"})


async def _user(session: AsyncSession, perms: frozenset[str] = OWN) -> Actor:
    user_id = await insert(session, "auth.user_account", username=f"u-{uuid4()}")
    assert isinstance(user_id, UUID)
    return Actor(user_id=user_id, roles=frozenset(), perms=perms, channel="api")


async def test_a_reference_table_comes_back_in_gos_shape(db_session: AsyncSession) -> None:
    me = await _user(db_session, frozenset())  # no role yet: still allowed
    organic = await insert(
        db_session,
        "ref.fertilizer_constant",
        fertilizer_name="ปุ๋ยคอก",
        description=None,
        is_organic=True,
    )
    plain = await insert(db_session, "ref.fertilizer_constant", fertilizer_name="ยูเรีย")

    rows = await service.constants(db_session, me, "fertilizer")

    assert sorted(rows, key=lambda r: r["fertilizer_name"]) == sorted(
        [
            {
                "fertilizer_id": str(organic),
                "fertilizer_name": "ปุ๋ยคอก",
                "description": "",  # NULL -> "" as Go wrote it
                "is_organic": True,
            },
            {
                "fertilizer_id": str(plain),
                "fertilizer_name": "ยูเรีย",
                "description": "",
                "is_organic": False,
            },
        ],
        key=lambda r: r["fertilizer_name"],
    )
    assert [r["fertilizer_id"] for r in rows] == sorted(r["fertilizer_id"] for r in rows)


@pytest.mark.parametrize(
    "key",
    [
        "air_exposure_type",
        "breed",
        "chem_bio",
        "drying_facility",
        "farm_activity_type",
        "fertilizer_stage",
        "fertilizer",
        "grade",
        "hole_filler",
        "land_type",
        "location_type",
        "pest_disease",
        "processing_activity_type",
        "processing_defect",
        "soil_type",
        "tank_material",
        "water_source",
        "watering_system",
        "weather_condition",
        "province",
        "district",
        "subdistrict",
        "farm",
        "plot",
        "hub",
        "harvest",
        "processing_station",
        "batch",
    ],
)
async def test_every_go_key_answers_a_list(db_session: AsyncSession, key: str) -> None:
    assert await service.constants(db_session, await _user(db_session), key) == []


async def test_pest_disease_name_is_filled(db_session: AsyncSession) -> None:
    pest = await insert(
        db_session, "ref.pest_disease_constant", pest_disease_name="หนอนเจาะฝัก", type="pest"
    )

    rows = await service.constants(db_session, await _user(db_session), "pest_disease")

    assert rows == [
        {"pest_disease_id": str(pest), "name": "หนอนเจาะฝัก", "type": "pest", "description": ""}
    ]


async def _places(session: AsyncSession) -> tuple[UUID, UUID, UUID, UUID]:
    province = await insert(session, "ref.province_constant", province_name_th="เชียงใหม่")
    other = await insert(session, "ref.province_constant", province_name_th="ลำปาง")
    district = await insert(
        session, "ref.district_constant", district_name_th="เมือง", province_id=province
    )
    await insert(session, "ref.district_constant", district_name_th="เกาะคา", province_id=other)
    sub = await insert(
        session,
        "ref.subdistrict_constant",
        subdistrict_name_th="ช้างเผือก",
        district_id=district,
        zipcode="50300",
    )
    for value in (province, district, sub, other):
        assert isinstance(value, UUID)
    return province, district, sub, other  # type: ignore[return-value]


async def test_districts_and_subdistricts_filter_by_their_parent(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    province, district, sub, _ = await _places(db_session)

    districts = await service.constants(
        db_session, me, "district", Filters(province_id=str(province))
    )
    every = await service.constants(db_session, me, "district", Filters(province_id=""))
    subs = await service.constants(
        db_session, me, "subdistrict", Filters(district_id=str(district))
    )

    assert [d["district_name_th"] for d in districts] == ["เมือง"]
    assert districts[0]["province_id"] == str(province)
    assert len(every) == 2  # an empty filter is no filter, as in Go
    assert [(s["subdistrict_id"], s["district_id"]) for s in subs] == [(str(sub), str(district))]


async def test_location_by_zip_or_subdistrict(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    _, _, sub, _ = await _places(db_session)
    expected = {
        "subdistrict_id": str(sub),
        "subdistrict_name": "ช้างเผือก",
        "district_name": "เมือง",
        "province_name": "เชียงใหม่",
        "zip_code": "50300",
        "full_name": "ต.ช้างเผือก อ.เมือง จ.เชียงใหม่ 50300",
    }

    assert await service.constants(db_session, me, "location", Filters(zip_code="50300")) == [
        expected
    ]
    assert await service.constants(
        db_session, me, "location", Filters(subdistrict_id=str(sub))
    ) == [expected]
    assert await service.constants(db_session, me, "location", Filters(zip_code="10100")) == []
    with pytest.raises(Invalid):
        await service.constants(db_session, me, "location")


async def test_farms_and_plots_are_only_mine(db_session: AsyncSession) -> None:
    me, neighbour = await _user(db_session), await _user(db_session)
    mine = await insert(db_session, "agriculture.farm", farm_name="สวนบน")
    other_mine = await insert(db_session, "agriculture.farm", farm_name="สวนล่าง")
    theirs = await insert(db_session, "agriculture.farm", farm_name="สวนเขา")
    for farmer, farm in ((me, mine), (me, other_mine), (neighbour, theirs)):
        await insert(db_session, "agriculture.farmer_farm", farmer_id=farmer.user_id, farm_id=farm)
    plot = await insert(db_session, "agriculture.plot", farm_id=mine, plot_name="แปลง 1")
    await insert(db_session, "agriculture.plot", farm_id=other_mine, plot_name="แปลง 2")
    await insert(db_session, "agriculture.plot", farm_id=theirs, plot_name="แปลงเขา")

    farms = await service.constants(db_session, me, "farm")
    plots = await service.constants(db_session, me, "plot", Filters(farm_id=str(mine)))
    all_plots = await service.constants(db_session, me, "plot")

    assert {f["farm_name"] for f in farms} == {"สวนบน", "สวนล่าง"}
    assert plots == [{"plot_id": str(plot), "plot_name": "แปลง 1"}]
    assert {p["plot_name"] for p in all_plots} == {"แปลง 1", "แปลง 2"}


async def test_hubs_and_harvests_are_only_my_hubs(db_session: AsyncSession) -> None:
    me, other = await _user(db_session), await _user(db_session)
    hub = await insert(db_session, "processing.hub", hub_name="ศูนย์ A")
    other_hub = await insert(db_session, "processing.hub", hub_name="ศูนย์ B")
    await insert(db_session, "processing.hub_collector", user_id=me.user_id, hub_id=hub)
    await insert(db_session, "processing.hub_collector", user_id=other.user_id, hub_id=other_hub)
    farm = await insert(db_session, "agriculture.farm", farm_name="ฟาร์มเฮาส์")
    harvest = await insert(
        db_session,
        "collection.harvest",
        hub_id=hub,
        farm_id=farm,
        harvest_date=datetime(2026, 5, 1, 9),
    )
    await insert(db_session, "collection.harvest", hub_id=hub, farm_id=None)  # no farm: left out
    await insert(db_session, "collection.harvest", hub_id=other_hub, farm_id=farm)

    assert await service.constants(db_session, me, "hub") == [
        {"hub_id": str(hub), "hub_name": "ศูนย์ A"}
    ]
    assert await service.constants(db_session, me, "harvest") == [
        {"harvest_id": str(harvest), "name": "ฟาร์มเฮาส์ (01 May 2569)"}
    ]
    assert await service.constants(db_session, me, "harvest", Filters(hub_id=str(other_hub))) == []


async def test_stations_and_batches_are_only_mine(db_session: AsyncSession) -> None:
    me, other = await _user(db_session), await _user(db_session)
    station = await insert(
        db_session, "processing.processing_station", processing_station_name="โรงหมัก"
    )
    other_station = await insert(
        db_session, "processing.processing_station", processing_station_name="โรงอื่น"
    )
    await insert(
        db_session,
        "processing.processor_processing_station",
        processor_id=me.user_id,
        processing_station_id=station,
    )
    await insert(
        db_session,
        "processing.processor_processing_station",
        processor_id=other.user_id,
        processing_station_id=other_station,
    )
    batch = await insert(
        db_session,
        "processing.batch",
        processing_station_id=station,
        origin="ริมรั้ว",
        created_at=datetime(2026, 4, 30, 10),
    )
    await insert(db_session, "processing.batch", processing_station_id=other_station, origin="X")

    assert await service.constants(db_session, me, "processing_station") == [
        {"processing_station_id": str(station), "processing_station_name": "โรงหมัก"}
    ]
    assert await service.constants(db_session, me, "batch", Filters(station_id=str(station))) == [
        {"batch_id": str(batch), "name": "ริมรั้ว (30 Apr 2569)"}
    ]


async def test_an_unknown_key_is_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFound, match="Constant key not found"):
        await service.constants(db_session, await _user(db_session), "no_such_key")


@pytest.mark.parametrize(
    ("key", "perm"),
    [
        ("farm", "read:farm:own"),
        ("plot", "read:farm:own"),
        ("hub", "read:hub:own"),
        ("harvest", "read:hub:own"),
        ("processing_station", "read:station:own"),
        ("batch", "read:station:own"),
    ],
)
async def test_own_rows_need_their_permission(
    db_session: AsyncSession, key: str, perm: str
) -> None:
    me = await _user(db_session, OWN - {perm})

    with pytest.raises(Forbidden, match=perm):
        await service.constants(db_session, me, key)


async def test_a_bad_id_in_a_filter_is_just_no_match(db_session: AsyncSession) -> None:
    me = await _user(db_session)

    assert await service.constants(db_session, me, "district", Filters(province_id="nope")) == []
    assert await service.constants(db_session, me, "plot", Filters(farm_id="nope")) == []
