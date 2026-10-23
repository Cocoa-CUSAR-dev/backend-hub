"""src/db/processing.py (hubs, stations, batches), src/db/collection.py
(harvests) and db.agriculture.farm_names against real Postgres."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from tests.db.conftest import insert

NOW = datetime(2026, 10, 22, 3, 0)


def _place() -> dict[str, object]:
    return {
        "province_id": uuid4(),
        "district_id": uuid4(),
        "subdistrict_id": uuid4(),
        "created_at": NOW,
        "updated_at": NOW,
    }


async def _user(session: AsyncSession) -> UUID:
    user_id = await insert(session, "auth.user_account", username=f"u-{uuid4()}")
    assert isinstance(user_id, UUID)
    return user_id


async def _collector(session: AsyncSession) -> UUID:
    user_id = await _user(session)
    await insert(session, "processing.hub_collector", user_id=user_id, first_name="เก็บ")
    return user_id


async def _hub(session: AsyncSession, name: str = "Hub") -> UUID:
    hub_id = uuid4()
    await db.processing.insert_hub(session, hub_id, hub_name=name, **_place())
    return hub_id


# --- hubs ---------------------------------------------------------------------


async def test_set_collector_hub_and_read_it_back(db_session: AsyncSession) -> None:
    collector = await _collector(db_session)
    hub_id = await _hub(db_session, "ศูนย์รวบรวม")

    assert await db.processing.collector_hub_ids(db_session, collector) == []
    assert await db.processing.set_collector_hub(db_session, collector, hub_id) is True
    assert await db.processing.collector_hub_ids(db_session, collector) == [hub_id]

    [hub] = await db.processing.hubs(db_session, hub_ids=[hub_id], limit=10, offset=0)
    assert (hub.hub_id, hub.hub_name, hub.created_at) == (hub_id, "ศูนย์รวบรวม", NOW)


async def test_set_collector_hub_without_a_profile_is_false(db_session: AsyncSession) -> None:
    no_profile = await _user(db_session)
    assert await db.processing.set_collector_hub(db_session, no_profile, uuid4()) is False


async def test_hubs_by_id_or_all_in_id_order(db_session: AsyncSession) -> None:
    ids = sorted([await _hub(db_session) for _ in range(3)])

    some = await db.processing.hubs(db_session, hub_ids=[ids[2], ids[0]], limit=10, offset=0)
    every = await db.processing.hubs(db_session, hub_ids=None, limit=10, offset=0)
    page = await db.processing.hubs(db_session, hub_ids=None, limit=2, offset=1)

    assert [h.hub_id for h in some] == [ids[0], ids[2]]
    assert [h.hub_id for h in every] == ids
    assert [h.hub_id for h in page] == ids[1:]
    assert await db.processing.hubs(db_session, hub_ids=[], limit=10, offset=0) == []


# --- stations and batches -------------------------------------------------------


async def _station(session: AsyncSession, processor: UUID | None, name: str = "S") -> UUID:
    station_id = uuid4()
    await db.processing.insert_station(
        session, station_id, processing_station_name=name, **_place()
    )
    if processor is not None:
        await db.processing.link_processor_station(session, processor, station_id)
    return station_id


async def _batch(session: AsyncSession, station_id: UUID, kg: float = 10) -> UUID:
    batch_id = await insert(
        session,
        "processing.batch",
        processing_station_id=station_id,
        quantity_kg=kg,
        created_at=NOW,
        updated_at=NOW,
    )
    assert isinstance(batch_id, UUID)
    return batch_id


async def test_stations_of_a_processor_or_all(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    other = await _user(db_session)
    mine = sorted([await _station(db_session, me), await _station(db_session, me)])
    theirs = await _station(db_session, other)

    my_stations = await db.processing.stations(db_session, processor_id=me, limit=10, offset=0)
    every = await db.processing.stations(db_session, processor_id=None, limit=10, offset=0)

    assert [s.processing_station_id for s in my_stations] == mine
    assert {s.processing_station_id for s in every} == {*mine, theirs}


async def test_batches_of_stations_and_of_a_processor(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    other = await _user(db_session)
    my_station = await _station(db_session, me)
    their_station = await _station(db_session, other)
    mine = sorted([await _batch(db_session, my_station), await _batch(db_session, my_station, 2.5)])
    theirs = await _batch(db_session, their_station)

    of_station = await db.processing.batches_of_stations(db_session, [my_station])
    of_me = await db.processing.batches(db_session, processor_id=me, limit=10, offset=0)
    of_all = await db.processing.batches(db_session, processor_id=None, limit=10, offset=0)
    paged = await db.processing.batches(db_session, processor_id=me, limit=1, offset=1)

    assert [b.batch_id for b in of_station] == mine
    assert [b.batch_id for b in of_me] == mine
    assert {b.batch_id for b in of_all} == {*mine, theirs}
    assert [b.batch_id for b in paged] == mine[1:]
    assert await db.processing.batches_of_stations(db_session, []) == []


# --- harvests -------------------------------------------------------------------


async def _harvest(session: AsyncSession, hub_id: UUID, farm_id: UUID, day: int) -> UUID:
    harvest_id = await insert(
        session,
        "collection.harvest",
        hub_id=hub_id,
        farm_id=farm_id,
        harvest_date=datetime(2026, 10, day, 8),
    )
    assert isinstance(harvest_id, UUID)
    return harvest_id


async def test_harvests_of_hubs_newest_first_one_row_per_grade(db_session: AsyncSession) -> None:
    hub, other_hub, farm = await _hub(db_session), await _hub(db_session), uuid4()
    older = await _harvest(db_session, hub, farm, 1)
    newer = await _harvest(db_session, hub, farm, 2)
    elsewhere = await _harvest(db_session, other_hub, farm, 3)
    for grade, kg in (("A", 3), ("B", 4)):
        await db_session.execute(
            text(
                "INSERT INTO collection.harvest_grade_detail (harvest_id, grade_code, quantity_kg)"
                " VALUES (:h, :g, :kg)"
            ),
            {"h": newer, "g": grade, "kg": kg},
        )
    batch = uuid4()
    await insert(db_session, "collection.harvest_collection", harvest_id=older, batch_id=batch)

    rows = await db.collection.harvests(db_session, hub_ids=[hub])

    assert [(r.harvest_id, r.grade_code) for r in rows] == [
        (newer, "A"),
        (newer, "B"),
        (older, None),
    ]
    assert float(rows[1].quantity_kg or 0) == 4
    assert rows[2].batch_id == batch
    every = await db.collection.harvests(db_session, hub_ids=None)
    assert {r.harvest_id for r in every} == {older, newer, elsewhere}
    paged = await db.collection.harvests(db_session, hub_ids=[hub], limit=1, offset=2)
    assert [r.harvest_id for r in paged] == [older]


async def test_farm_names(db_session: AsyncSession) -> None:
    farm_id = uuid4()
    await db.agriculture.insert_farm(
        db_session, farm_id, farm_name="สวน", created_at=NOW, updated_at=NOW
    )

    assert await db.agriculture.farm_names(db_session, [farm_id, uuid4()]) == {farm_id: "สวน"}
    assert await db.agriculture.farm_names(db_session, []) == {}
