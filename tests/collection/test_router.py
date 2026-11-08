"""/api/v1/collection and /api/v1/processing routes: each reaches the right
service function with the Bearer caller and answers with the right status
and JSON. The services are faked; their rules are in test_service.py."""

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from src.auth import tokens
from src.collection import service as collection
from src.collection.schemas import HarvestOut, HubCreatedOut, HubIn, HubOut
from src.exceptions import Forbidden
from src.processing import service as processing
from src.processing.schemas import BatchOut, StationCreatedOut, StationIn, StationOut

USER_ID = uuid4()
NOW = datetime(2026, 10, 1, 3)


def _auth() -> dict[str, str]:
    perms = frozenset({"create:hub:own", "read:hub:own", "create:station:own", "read:station:own"})
    token = tokens.issue_access_token(USER_ID, "0812345678", frozenset({"processor"}), perms)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes: dict[str, AsyncMock] = {}
    for module, names in (
        (collection, ("create_hub", "list_hubs", "list_harvests")),
        (processing, ("create_station", "list_stations", "list_batches")),
    ):
        for name in names:
            fakes[name] = AsyncMock()
            monkeypatch.setattr(module, name, fakes[name])
    return fakes


def _place(**extra: Any) -> dict[str, Any]:
    return {
        "province_id": str(uuid4()),
        "district_id": str(uuid4()),
        "subdistrict_id": str(uuid4()),
        **extra,
    }


def _harvest(hub_id: Any) -> HarvestOut:
    return HarvestOut(
        harvest_id=uuid4(), hub_id=hub_id, farm_id=uuid4(), plot_id=None, tree_id=None,
        harvest_date=datetime(2026, 10, 5, 7), created_at=NOW, updated_at=NOW,
        grade_code="A", quantity_kg=12.0, grade_description=None, is_clean=True,
        is_appropriate_size=None, weight_gram_per_pod=None, is_sprout=None, is_dry=None,
        is_shriveled=None, cut_test_result=None, collection_id=None, batch_id=None,
        farm_name="สวน",
    )  # fmt: skip


async def test_routes_need_a_bearer_token(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    for path in ("/api/v1/collection/hubs", "/api/v1/processing/stations"):
        assert (await client.get(path)).status_code == 401
    fake["list_hubs"].assert_not_awaited()


async def test_create_hub(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    hub_id = uuid4()
    fake["create_hub"].return_value = HubCreatedOut(hub_id=hub_id)
    body = _place(hub_name="ศูนย์", gis=[{"lat": 19.1, "lng": 98.9}])

    response = await client.post("/api/v1/collection/hubs", json=body, headers=_auth())

    assert response.status_code == 201
    assert response.json() == {"message": "ลงทะเบียน Hub สำเร็จ", "hub_id": str(hub_id)}
    _, actor, hub = fake["create_hub"].await_args.args
    assert (actor.user_id, hub) == (USER_ID, HubIn.model_validate(body))


async def test_create_hub_needs_the_place_ids(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    response = await client.post(
        "/api/v1/collection/hubs", json={"hub_name": "ศูนย์"}, headers=_auth()
    )

    assert response.status_code == 422
    fake["create_hub"].assert_not_awaited()


async def test_list_hubs_nests_harvests_and_writes_go_times(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    hub_id = uuid4()
    fake["list_hubs"].return_value = [
        HubOut(
            hub_id=hub_id,
            hub_name="ศูนย์",
            geo_id=None,
            found_date=date(2026, 1, 2),
            address_detail=None,
            subdistrict_id=None,
            district_id=None,
            province_id=None,
            zip_code=None,
            contact_name=None,
            phone_number=None,
            line=None,
            facebook=None,
            created_at=NOW,
            updated_at=NOW,
            harvests=[_harvest(hub_id)],
        )  # fmt: skip
    ]

    response = await client.get("/api/v1/collection/hubs?page=1&page_size=5", headers=_auth())

    assert response.status_code == 200
    [hub] = response.json()
    assert (hub["found_date"], hub["created_at"]) == (
        "2026-01-02T00:00:00Z",
        "2026-10-01T03:00:00Z",
    )
    [harvest] = hub["harvests"]
    assert (harvest["grade_code"], harvest["farm_name"], harvest["harvest_date"]) == (
        "A",
        "สวน",
        "2026-10-05T07:00:00Z",
    )
    assert fake["list_hubs"].await_args.kwargs == {"page": 1, "page_size": 5}


async def test_list_harvests(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    fake["list_harvests"].return_value = [_harvest(uuid4())]

    response = await client.get("/api/v1/collection/harvests", headers=_auth())

    assert (response.status_code, len(response.json())) == (200, 1)
    assert fake["list_harvests"].await_args.kwargs == {"page": 0, "page_size": 50}


async def test_create_station(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    station_id = uuid4()
    fake["create_station"].return_value = StationCreatedOut(station_id=station_id)
    body = _place(processing_station_name="โรงหมัก")

    response = await client.post("/api/v1/processing/stations", json=body, headers=_auth())

    assert response.status_code == 201
    assert response.json() == {"message": "สร้างสถานีแปรรูปสำเร็จ", "station_id": str(station_id)}
    assert fake["create_station"].await_args.args[2] == StationIn.model_validate(body)


async def test_list_stations_and_batches(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    station_id = uuid4()
    batch = BatchOut(
        batch_id=uuid4(), processing_station_id=station_id, origin=None, notes="",
        quantity_kg=40.0, created_at=NOW, updated_at=NOW,
    )  # fmt: skip
    fake["list_stations"].return_value = [
        StationOut(
            processing_station_id=station_id,
            processing_station_name="โรงหมัก",
            hub_id=None,
            geo_id=None,
            found_date=None,
            processing_station_area=None,
            address_detail=None,
            subdistrict_id=None,
            district_id=None,
            province_id=None,
            zip_code=None,
            contact_name=None,
            phone_number=None,
            line=None,
            facebook=None,
            created_at=NOW,
            updated_at=NOW,
            batches=[batch],
        )  # fmt: skip
    ]
    fake["list_batches"].return_value = [batch]

    stations = await client.get("/api/v1/processing/stations", headers=_auth())
    batches = await client.get("/api/v1/processing/batches?page=2", headers=_auth())

    assert stations.status_code == 200
    assert [b["batch_id"] for b in stations.json()[0]["batches"]] == [str(batch.batch_id)]
    assert (batches.status_code, batches.json()[0]["quantity_kg"]) == (200, 40.0)
    assert fake["list_batches"].await_args.kwargs == {"page": 2, "page_size": 50}


async def test_service_forbidden_becomes_403(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    fake["create_hub"].side_effect = Forbidden("Add the hub collector role first")

    response = await client.post("/api/v1/collection/hubs", json=_place(), headers=_auth())

    assert (response.status_code, response.json()) == (
        403,
        {"detail": "Add the hub collector role first"},
    )
