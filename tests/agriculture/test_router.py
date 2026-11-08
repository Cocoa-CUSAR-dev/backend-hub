"""/api/v1/agriculture routes: each one reaches the right service function
with the Bearer caller as actor and answers with the right status and JSON.
The service is faked; its rules are in test_service.py, and the real-DB
flow in tests/db/test_agriculture_service.py."""

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from src.agriculture import service
from src.agriculture.schemas import (
    FarmCreatedOut,
    FarmImageOut,
    FarmIn,
    FarmOut,
    PlotCreatedOut,
    PlotIn,
    PlotOut,
)
from src.auth import tokens
from src.exceptions import Forbidden, Invalid

USER_ID = uuid4()
NOW = datetime(2026, 10, 1, 3, 0)


def _auth() -> dict[str, str]:
    perms = frozenset({"create:farm:own", "read:farm:own", "update:farm:own"})
    token = tokens.issue_access_token(USER_ID, "0812345678", frozenset({"farmer"}), perms)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    names = ("create_farm", "list_farms", "upload_farm_image", "create_plot", "list_plots")
    fakes = {name: AsyncMock() for name in names}
    for name, mock in fakes.items():
        monkeypatch.setattr(service, name, mock)
    return fakes


def _farm_body(**overrides: Any) -> dict[str, Any]:
    return {
        "farm_name": "สวน",
        "province_id": str(uuid4()),
        "district_id": str(uuid4()),
        "subdistrict_id": str(uuid4()),
        "zip_code": "50000",
        "contact_name": "สมชาย",
        "phone_number": "0800000000",
        "gis": [{"lat": 13.7, "lng": 100.5}],
        **overrides,
    }


def _plot(plot_id: Any, farm_id: Any) -> PlotOut:
    return PlotOut(
        plot_id=plot_id, farm_id=farm_id, plot_name="A", total_area=1.0, land_ownership="own",
        cocoa_planted_area=0.5, has_chemical_use=False, land_type_id=None, soil_type_id=None,
        water_source_id=None, watering_system_id=None, found_date=date(2026, 1, 2),
        created_at=NOW, updated_at=NOW,
    )  # fmt: skip


async def test_routes_need_a_bearer_token(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    assert (await client.get("/api/v1/agriculture/farms")).status_code == 401
    fake["list_farms"].assert_not_awaited()


async def test_create_farm(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    farm_id = uuid4()
    fake["create_farm"].return_value = FarmCreatedOut(farm_id=farm_id)
    body = _farm_body()

    response = await client.post("/api/v1/agriculture/farms", json=body, headers=_auth())

    assert response.status_code == 201
    assert response.json() == {"message": "ลงทะเบียนฟาร์มและเจ้าของสำเร็จ", "farm_id": str(farm_id)}
    _, actor, farm = fake["create_farm"].await_args.args
    assert actor.user_id == USER_ID
    assert farm == FarmIn.model_validate(body)


async def test_create_farm_reports_missing_fields(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    response = await client.post(
        "/api/v1/agriculture/farms", json=_farm_body(province_id=None), headers=_auth()
    )

    assert response.status_code == 422
    fake["create_farm"].assert_not_awaited()


async def test_list_farms_nests_plots_and_writes_go_dates(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    farm_id, plot_id = uuid4(), uuid4()
    fake["list_farms"].return_value = [
        FarmOut(
            farm_id=farm_id,
            farm_name="สวน",
            hub_id=None,
            geo_id=None,
            found_date=date(2026, 1, 2),
            total_area=None,
            address_detail=None,
            subdistrict_id=None,
            district_id=None,
            province_id=None,
            zip_code="50000",
            contact_name=None,
            phone_number=None,
            line=None,
            facebook=None,
            created_at=NOW,
            updated_at=NOW,
            plots=[_plot(plot_id, farm_id)],
            image_url="https://r2/x",
        )  # fmt: skip
    ]

    response = await client.get("/api/v1/agriculture/farms?page=1&page_size=5", headers=_auth())

    assert response.status_code == 200
    [farm] = response.json()
    assert (farm["farm_id"], farm["found_date"], farm["created_at"], farm["image_url"]) == (
        str(farm_id),
        "2026-01-02T00:00:00Z",
        "2026-10-01T03:00:00Z",
        "https://r2/x",
    )
    assert [p["plot_id"] for p in farm["plots"]] == [str(plot_id)]
    assert fake["list_farms"].await_args.kwargs == {"page": 1, "page_size": 5}


async def test_create_and_list_plots(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    farm_id, plot_id = uuid4(), uuid4()
    fake["create_plot"].return_value = PlotCreatedOut(plot_id=plot_id)
    fake["list_plots"].return_value = [_plot(plot_id, farm_id)]
    body = {
        "farm_id": str(farm_id),
        "plot_name": "A",
        "land_ownership": "own",
        "cocoa_planted_area": 0.5,
        "gis_area_m2": 1600,
    }

    created = await client.post("/api/v1/agriculture/plots", json=body, headers=_auth())
    listed = await client.get("/api/v1/agriculture/plots", headers=_auth())

    assert (created.status_code, created.json()["plot_id"]) == (201, str(plot_id))
    assert fake["create_plot"].await_args.args[2] == PlotIn.model_validate(body)
    assert listed.status_code == 200
    assert [p["plot_id"] for p in listed.json()] == [str(plot_id)]


async def test_upload_reads_the_multipart_image_field(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    farm_id = uuid4()
    fake["upload_farm_image"].return_value = FarmImageOut(farm_id=farm_id, image_url="https://r2/x")
    photo = b"\xff\xd8\xff" + b"0" * 10

    response = await client.post(
        f"/api/v1/agriculture/farms/{farm_id}/image",
        files={"image": ("farm.jpg", photo, "image/jpeg")},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "อัปโหลดรูปฟาร์มสำเร็จ",
        "farm_id": str(farm_id),
        "image_url": "https://r2/x",
    }
    args, kwargs = fake["upload_farm_image"].await_args
    assert (args[2], kwargs) == (farm_id, {"filename": "farm.jpg", "data": photo})


async def test_upload_reads_at_most_one_byte_past_the_limit(
    client: AsyncClient, fake: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "MAX_IMAGE_BYTES", 4)
    fake["upload_farm_image"].side_effect = Invalid(fields={"image": "รูปต้องมีขนาดไม่เกิน 5 MB"})

    response = await client.post(
        f"/api/v1/agriculture/farms/{uuid4()}/image",
        files={"image": ("big.jpg", b"0123456789", "image/jpeg")},
        headers=_auth(),
    )

    assert response.status_code == 422
    assert response.json()["fields"] == {"image": "รูปต้องมีขนาดไม่เกิน 5 MB"}
    assert fake["upload_farm_image"].await_args.kwargs["data"] == b"01234"


async def test_upload_without_the_image_field_is_422(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    response = await client.post(
        f"/api/v1/agriculture/farms/{uuid4()}/image",
        files={"photo": ("farm.jpg", b"x", "image/jpeg")},
        headers=_auth(),
    )

    assert response.status_code == 422
    fake["upload_farm_image"].assert_not_awaited()


async def test_service_forbidden_becomes_403(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    fake["create_plot"].side_effect = Forbidden("Not your farm")
    body = {"farm_id": str(uuid4()), "plot_name": "A", "land_ownership": "own",
            "cocoa_planted_area": 1}  # fmt: skip

    response = await client.post("/api/v1/agriculture/plots", json=body, headers=_auth())

    assert (response.status_code, response.json()) == (403, {"detail": "Not your farm"})
