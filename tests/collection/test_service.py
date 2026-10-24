"""collection.service with the db layer faked: permissions, scope, the
hub/harvest grouping and the input rules. Real-DB flow is in
tests/db/test_collection_processing_service.py."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src import db
from src.actor import Actor
from src.collection import service
from src.collection.schemas import HubIn
from src.db.collection import HarvestGradeRow
from src.db.processing import HubRow
from src.exceptions import Forbidden, Invalid

COLLECTOR = frozenset({"create:hub:own", "read:hub:own"})
SESSION: Any = AsyncMock()
NOW = datetime(2026, 10, 1, 3)


def _actor(perms: frozenset[str] = COLLECTOR) -> Actor:
    return Actor(user_id=uuid4(), roles=frozenset({"hub_collector"}), perms=perms, channel="api")


def _hub_in(**overrides: Any) -> HubIn:
    return HubIn.model_validate(
        {
            "hub_name": "ศูนย์",
            "province_id": str(uuid4()),
            "district_id": str(uuid4()),
            "subdistrict_id": str(uuid4()),
            **overrides,
        }
    )


def _hub_row(hub_id: UUID) -> HubRow:
    return HubRow(
        hub_id, "H", None, date(2026, 1, 2), None, None, None, None,
        None, None, None, None, None, NOW, NOW,
    )  # fmt: skip


def _harvest(hub_id: UUID, farm_id: UUID, grade: str | None = None) -> HarvestGradeRow:
    return HarvestGradeRow(
        uuid4(), hub_id, farm_id, None, None, NOW, NOW, NOW, grade,
        Decimal("3.5") if grade else None, None, None, None, None, None, None, None, None,
        None, None,
    )  # fmt: skip


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {
        "insert_hub": AsyncMock(),
        "set_collector_hub": AsyncMock(return_value=True),
        "hub_ids_of_collector": AsyncMock(return_value=[]),
        "hubs": AsyncMock(return_value=[]),
        "harvests": AsyncMock(return_value=[]),
        "farm_names": AsyncMock(return_value={}),
        "insert_geo": AsyncMock(),
    }
    for name in ("insert_hub", "set_collector_hub", "hub_ids_of_collector", "hubs"):
        monkeypatch.setattr(db.processing, name, fakes[name])
    monkeypatch.setattr(db.collection, "harvests", fakes["harvests"])
    monkeypatch.setattr(db.agriculture, "farm_names", fakes["farm_names"])
    monkeypatch.setattr(db.storage, "insert_geo", fakes["insert_geo"])
    return fakes


async def test_create_hub_links_it_to_me_and_stores_the_first_point(
    fake: dict[str, AsyncMock],
) -> None:
    me = _actor()
    hub = _hub_in(found_date="2026-01-02", gis=[{"lat": 18.8, "lng": 99.0}, {"lat": 1, "lng": 2}])

    out = await service.create_hub(SESSION, me, hub)

    kwargs = fake["insert_hub"].await_args.kwargs
    assert (kwargs["hub_name"], kwargs["found_date"]) == ("ศูนย์", date(2026, 1, 2))
    fake["set_collector_hub"].assert_awaited_once_with(SESSION, me.user_id, out.hub_id)
    geo = fake["insert_geo"].await_args.kwargs
    assert (geo["wkt"], geo["source_type"], geo["code"]) == (
        "POINT(99.000000 18.800000)",
        "hub",
        str(out.hub_id),
    )
    assert out.message == "ลงทะเบียน Hub สำเร็จ"


async def test_create_hub_without_a_collector_profile_is_refused(
    fake: dict[str, AsyncMock],
) -> None:
    fake["set_collector_hub"].return_value = False
    with pytest.raises(Forbidden, match="hub collector"):
        await service.create_hub(SESSION, _actor(), _hub_in())
    fake["insert_geo"].assert_not_awaited()


async def test_create_hub_rejects_a_bad_date(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Invalid):
        await service.create_hub(SESSION, _actor(), _hub_in(found_date="tomorrow"))
    fake["insert_hub"].assert_not_awaited()


async def test_create_hub_forbidden_without_permission(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="create:hub:own"):
        await service.create_hub(SESSION, _actor(frozenset({"read:hub:own"})), _hub_in())


async def test_create_hub_needs_a_person(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="user"):
        await service.create_hub(SESSION, Actor.system(), _hub_in())


async def test_list_hubs_nests_harvests_with_farm_names(fake: dict[str, AsyncMock]) -> None:
    hub_id, quiet_hub, farm = uuid4(), uuid4(), uuid4()
    fake["hub_ids_of_collector"].return_value = [hub_id, quiet_hub]
    fake["hubs"].return_value = [_hub_row(hub_id), _hub_row(quiet_hub)]
    fake["harvests"].return_value = [_harvest(hub_id, farm, "A"), _harvest(hub_id, farm, "B")]
    fake["farm_names"].return_value = {farm: "สวน"}

    out = await service.list_hubs(SESSION, _actor(), page=1, page_size=10)

    assert fake["hubs"].await_args.kwargs == {
        "hub_ids": [hub_id, quiet_hub],
        "limit": 10,
        "offset": 10,
    }
    fake["harvests"].assert_awaited_once_with(SESSION, hub_ids=[hub_id, quiet_hub])
    assert [(h.grade_code, h.quantity_kg, h.farm_name) for h in out[0].harvests] == [
        ("A", 3.5, "สวน"),
        ("B", 3.5, "สวน"),
    ]
    assert out[1].harvests == []


async def test_a_collector_without_a_hub_sees_nothing(fake: dict[str, AsyncMock]) -> None:
    assert await service.list_hubs(SESSION, _actor()) == []
    assert await service.list_harvests(SESSION, _actor()) == []
    fake["hubs"].assert_not_awaited()
    fake["harvests"].assert_not_awaited()


async def test_researchers_see_every_hub(fake: dict[str, AsyncMock]) -> None:
    researcher = _actor(frozenset({"read:hub:all"}))

    await service.list_hubs(SESSION, researcher)
    await service.list_harvests(SESSION, researcher, page=-3, page_size=0)

    assert fake["hubs"].await_args.kwargs["hub_ids"] is None
    assert fake["harvests"].await_args.kwargs == {"hub_ids": None, "limit": 50, "offset": 0}
    fake["hub_ids_of_collector"].assert_not_awaited()


async def test_list_harvests_is_paged_and_has_no_farm_name(fake: dict[str, AsyncMock]) -> None:
    hub_id = uuid4()
    fake["hub_ids_of_collector"].return_value = [hub_id]
    fake["harvests"].return_value = [_harvest(hub_id, uuid4())]

    [harvest] = await service.list_harvests(SESSION, _actor(), page=2, page_size=5)

    assert fake["harvests"].await_args.kwargs == {"hub_ids": [hub_id], "limit": 5, "offset": 10}
    assert harvest.farm_name is None
    fake["farm_names"].assert_not_awaited()


async def test_lists_are_forbidden_without_a_read_permission(fake: dict[str, AsyncMock]) -> None:
    no_read = _actor(frozenset({"create:hub:own"}))
    with pytest.raises(Forbidden, match="read:hub:own"):
        await service.list_hubs(SESSION, no_read)
    with pytest.raises(Forbidden, match="read:hub:own"):
        await service.list_harvests(SESSION, no_read)
