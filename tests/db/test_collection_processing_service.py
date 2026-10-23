"""collection.service and processing.service end to end against real
Postgres: what a registration leaves in the tables and what the lists return."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.actor import Actor
from src.collection import service as collection
from src.collection.schemas import HubIn
from src.exceptions import Forbidden
from src.processing import service as processing
from src.processing.schemas import StationIn
from tests.db.conftest import insert


async def _person(session: AsyncSession, role: str, perms: set[str]) -> Actor:
    user_id = await insert(session, "auth.user_account", username=f"{role}-{uuid4()}")
    assert isinstance(user_id, UUID)
    if role == "hub_collector":
        await insert(session, "processing.hub_collector", user_id=user_id, first_name="เก็บ")
    return Actor(user_id=user_id, roles=frozenset({role}), perms=frozenset(perms), channel="api")


def _place() -> dict[str, str]:
    return {
        "province_id": str(uuid4()),
        "district_id": str(uuid4()),
        "subdistrict_id": str(uuid4()),
    }


async def test_collector_registers_a_hub_and_sees_its_harvests(db_session: AsyncSession) -> None:
    me = await _person(db_session, "hub_collector", {"create:hub:own", "read:hub:own"})
    other = await _person(db_session, "hub_collector", {"create:hub:own", "read:hub:own"})
    await collection.create_hub(db_session, other, HubIn.model_validate({**_place()}))

    created = await collection.create_hub(
        db_session,
        me,
        HubIn.model_validate(
            {"hub_name": "ศูนย์แม่แตง", "gis": [{"lat": 19.1, "lng": 98.9}], **_place()}
        ),
    )
    farm_id = uuid4()
    await insert(
        db_session,
        "agriculture.farm",
        farm_id=farm_id,
        farm_name="สวนลุงมา",
        created_at=datetime(2026, 10, 1),
        updated_at=datetime(2026, 10, 1),
    )
    harvest_id = await insert(
        db_session,
        "collection.harvest",
        hub_id=created.hub_id,
        farm_id=farm_id,
        harvest_date=datetime(2026, 10, 5, 7),
    )
    await db_session.execute(
        text(
            "INSERT INTO collection.harvest_grade_detail (harvest_id, grade_code, quantity_kg)"
            " VALUES (:h, 'A', 12)"
        ),
        {"h": harvest_id},
    )

    [hub] = await collection.list_hubs(db_session, me)
    [harvest] = await collection.list_harvests(db_session, me)

    assert (hub.hub_id, hub.hub_name) == (created.hub_id, "ศูนย์แม่แตง")
    assert [(h.harvest_id, h.grade_code, h.farm_name) for h in hub.harvests] == [
        (harvest_id, "A", "สวนลุงมา")
    ]
    assert (harvest.harvest_id, harvest.quantity_kg) == (harvest_id, 12)
    geo = (
        await db_session.execute(
            text("SELECT geom FROM storage.geo WHERE source_type = 'hub' AND code = :c"),
            {"c": str(created.hub_id)},
        )
    ).scalar_one()
    assert geo == "SRID=4326;POINT(98.900000 19.100000)"


async def test_hub_without_a_collector_profile_leaves_nothing(db_session: AsyncSession) -> None:
    no_profile = await _person(db_session, "farmer", {"create:hub:own"})

    with pytest.raises(Forbidden):
        await collection.create_hub(db_session, no_profile, HubIn.model_validate(_place()))

    count = (await db_session.execute(text("SELECT count(*) FROM processing.hub"))).scalar_one()
    assert count == 0


async def test_processor_registers_stations_and_sees_their_batches(
    db_session: AsyncSession,
) -> None:
    me = await _person(db_session, "processor", {"create:station:own", "read:station:own"})
    other = await _person(db_session, "processor", {"create:station:own", "read:station:own"})
    await processing.create_station(db_session, other, StationIn.model_validate(_place()))
    created = await processing.create_station(
        db_session,
        me,
        StationIn.model_validate({"processing_station_name": "โรงหมัก", **_place()}),
    )
    batch_id = await insert(
        db_session,
        "processing.batch",
        processing_station_id=created.station_id,
        quantity_kg=40,
        created_at=datetime(2026, 10, 1),
        updated_at=datetime(2026, 10, 1),
    )

    [station] = await processing.list_stations(db_session, me)
    [batch] = await processing.list_batches(db_session, me)

    assert (station.processing_station_id, station.processing_station_name) == (
        created.station_id,
        "โรงหมัก",
    )
    assert [b.batch_id for b in station.batches] == [batch_id]
    assert (batch.batch_id, batch.quantity_kg) == (batch_id, 40)
