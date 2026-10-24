"""The picker queries in src/db/processing.py against real Postgres."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from tests.db.conftest import insert


async def _user(session: AsyncSession) -> UUID:
    user_id = await insert(session, "auth.user_account", username=f"user-{uuid4()}")
    assert user_id is not None
    return user_id


async def test_hub_ids_of_a_collector_skip_a_collector_with_no_hub(
    db_session: AsyncSession,
) -> None:
    with_hub, without_hub, hub = await _user(db_session), await _user(db_session), uuid4()
    await insert(db_session, "processing.hub_collector", user_id=with_hub, hub_id=hub)
    await insert(db_session, "processing.hub_collector", user_id=without_hub)

    assert await db.processing.hub_ids_of_collector(db_session, with_hub) == [hub]
    assert await db.processing.hub_ids_of_collector(db_session, without_hub) == []
    assert await db.processing.hub_ids_of_collector(db_session, await _user(db_session)) == []


async def test_hub_names_cover_only_hubs_that_exist(db_session: AsyncSession) -> None:
    hub = await insert(db_session, "processing.hub", hub_name="ฮับกลาง")
    assert hub is not None

    assert await db.processing.hub_names(db_session, [hub, uuid4()]) == {hub: "ฮับกลาง"}
    assert await db.processing.hub_names(db_session, []) == {}


async def test_batches_are_those_at_the_processors_stations_newest_first(
    db_session: AsyncSession,
) -> None:
    me, other = await _user(db_session), await _user(db_session)
    mine = await insert(
        db_session, "processing.processing_station", processing_station_name="สถานี A"
    )
    theirs = await insert(
        db_session, "processing.processing_station", processing_station_name="สถานี B"
    )
    for processor, station in ((me, mine), (other, theirs)):
        await db_session.execute(
            text(
                "INSERT INTO processing.processor_processing_station "
                "(processor_id, processing_station_id) VALUES (:p, :s)"
            ),
            {"p": processor, "s": station},
        )
    older = await insert(
        db_session, "processing.batch", processing_station_id=mine, created_at=datetime(2026, 8, 1)
    )
    newer = await insert(
        db_session, "processing.batch", processing_station_id=mine, created_at=datetime(2026, 8, 2)
    )
    await insert(
        db_session,
        "processing.batch",
        processing_station_id=theirs,
        created_at=datetime(2026, 8, 3),
    )

    rows = await db.processing.batches_of_processor(db_session, me, limit=10)
    capped = await db.processing.batches_of_processor(db_session, me, limit=1)

    assert [(r.batch_id, r.station_name) for r in rows] == [(newer, "สถานี A"), (older, "สถานี A")]
    assert [r.batch_id for r in capped] == [newer]
