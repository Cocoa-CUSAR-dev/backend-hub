"""processing.service with the db layer faked: permissions, scope and the
station/batch grouping. Real-DB flow is in
tests/db/test_collection_processing_service.py."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src import db
from src.actor import Actor
from src.db.processing import BatchRow, StationRow
from src.exceptions import Forbidden
from src.processing import service
from src.processing.schemas import StationIn

PROCESSOR = frozenset({"create:station:own", "read:station:own"})
SESSION: Any = AsyncMock()
NOW = datetime(2026, 10, 1, 3)


def _actor(perms: frozenset[str] = PROCESSOR) -> Actor:
    return Actor(user_id=uuid4(), roles=frozenset({"processor"}), perms=perms, channel="api")


def _station_in(**overrides: Any) -> StationIn:
    return StationIn.model_validate(
        {
            "processing_station_name": "โรงหมัก",
            "province_id": str(uuid4()),
            "district_id": str(uuid4()),
            "subdistrict_id": str(uuid4()),
            **overrides,
        }
    )


def _station_row(station_id: UUID) -> StationRow:
    return StationRow(
        station_id, "S", None, None, None, Decimal("12.5"), None, None, None, None,
        None, None, None, None, None, NOW, NOW,
    )  # fmt: skip


def _batch_row(station_id: UUID) -> BatchRow:
    return BatchRow(uuid4(), station_id, "farm", "", Decimal("10"), NOW, NOW)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {
        name: AsyncMock(return_value=[])
        for name in (
            "insert_station",
            "link_processor_station",
            "stations",
            "batches_of_stations",
            "batches",
        )
    }
    for name, mock in fakes.items():
        monkeypatch.setattr(db.processing, name, mock)
    return fakes


async def test_create_station_links_it_to_me(fake: dict[str, AsyncMock]) -> None:
    me = _actor()
    hub_id = uuid4()

    out = await service.create_station(
        SESSION, me, _station_in(hub_id=str(hub_id), gis=[{"lat": 1, "lng": 2}])
    )

    kwargs = fake["insert_station"].await_args.kwargs
    assert (kwargs["processing_station_name"], kwargs["hub_id"]) == ("โรงหมัก", hub_id)
    assert kwargs["created_at"].tzinfo is None
    fake["link_processor_station"].assert_awaited_once_with(SESSION, me.user_id, out.station_id)
    assert out.message == "สร้างสถานีแปรรูปสำเร็จ"


async def test_create_station_forbidden_without_permission(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="create:station:own"):
        await service.create_station(SESSION, _actor(frozenset()), _station_in())
    fake["insert_station"].assert_not_awaited()


async def test_create_station_needs_a_person(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="user"):
        await service.create_station(SESSION, Actor.system(), _station_in())


async def test_list_stations_nests_their_batches(fake: dict[str, AsyncMock]) -> None:
    me = _actor()
    busy, idle = uuid4(), uuid4()
    fake["stations"].return_value = [_station_row(busy), _station_row(idle)]
    fake["batches_of_stations"].return_value = [_batch_row(busy), _batch_row(busy)]

    out = await service.list_stations(SESSION, me, page=1, page_size=2)

    assert fake["stations"].await_args.kwargs == {
        "processor_id": me.user_id,
        "limit": 2,
        "offset": 2,
    }
    assert [len(s.batches) for s in out] == [2, 0]
    assert out[0].processing_station_area == 12.5
    assert out[0].batches[0].quantity_kg == 10


async def test_list_batches_scope(fake: dict[str, AsyncMock]) -> None:
    me = _actor()
    fake["batches"].return_value = [_batch_row(uuid4())]

    [batch] = await service.list_batches(SESSION, me)
    await service.list_batches(SESSION, _actor(frozenset({"read:station:all"})))

    assert fake["batches"].await_args_list[0].kwargs["processor_id"] == me.user_id
    assert fake["batches"].await_args_list[1].kwargs["processor_id"] is None
    assert batch.origin == "farm"


async def test_lists_are_forbidden_without_a_read_permission(fake: dict[str, AsyncMock]) -> None:
    no_read = _actor(frozenset({"create:station:own"}))
    with pytest.raises(Forbidden, match="read:station:own"):
        await service.list_stations(SESSION, no_read)
    with pytest.raises(Forbidden, match="read:station:own"):
        await service.list_batches(SESSION, no_read)
