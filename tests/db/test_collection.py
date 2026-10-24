"""src/db/collection.py against real Postgres."""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from tests.db.conftest import insert


async def _harvest(
    session: AsyncSession, on: date, farm_id: UUID | None = None, hub_id: UUID | None = None
) -> UUID:
    harvest_id = await insert(
        session, "collection.harvest", farm_id=farm_id, hub_id=hub_id, harvest_date=on
    )
    assert harvest_id is not None
    return harvest_id


async def test_harvests_are_those_of_the_given_farms_or_hubs_newest_first(
    db_session: AsyncSession,
) -> None:
    my_farm, my_hub = uuid4(), uuid4()
    on_farm = await _harvest(db_session, date(2026, 8, 1), farm_id=my_farm)
    at_hub = await _harvest(db_session, date(2026, 8, 3), hub_id=my_hub)
    both = await _harvest(db_session, date(2026, 8, 2), farm_id=my_farm, hub_id=my_hub)
    await _harvest(db_session, date(2026, 8, 4), farm_id=uuid4(), hub_id=uuid4())

    rows = await db.collection.harvests_of(
        db_session, farm_ids=[my_farm], hub_ids=[my_hub], limit=10
    )

    assert [r.harvest_id for r in rows] == [at_hub, both, on_farm]
    assert (rows[1].farm_id, rows[1].hub_id) == (my_farm, my_hub)


async def test_harvests_can_be_found_by_farm_alone_or_by_hub_alone(
    db_session: AsyncSession,
) -> None:
    farm, hub = uuid4(), uuid4()
    on_farm = await _harvest(db_session, date(2026, 8, 1), farm_id=farm)
    at_hub = await _harvest(db_session, date(2026, 8, 2), hub_id=hub)

    by_farm = await db.collection.harvests_of(db_session, farm_ids=[farm], hub_ids=[], limit=10)
    by_hub = await db.collection.harvests_of(db_session, farm_ids=[], hub_ids=[hub], limit=10)
    neither = await db.collection.harvests_of(db_session, farm_ids=[], hub_ids=[], limit=10)

    assert [r.harvest_id for r in by_farm] == [on_farm]
    assert [r.harvest_id for r in by_hub] == [at_hub]
    assert neither == []


async def test_harvests_are_capped_at_the_limit(db_session: AsyncSession) -> None:
    farm = uuid4()
    for day in range(1, 6):
        await _harvest(db_session, date(2026, 8, day), farm_id=farm)

    rows = await db.collection.harvests_of(db_session, farm_ids=[farm], hub_ids=[], limit=3)

    assert [r.harvest_date for r in rows] == [datetime(2026, 8, day) for day in (5, 4, 3)]
