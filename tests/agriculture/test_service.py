"""agriculture.service with the db layer and R2 faked: permissions,
not-found, invalid input and the rules around shapes and photos. The same
flows against real Postgres are in tests/db/test_agriculture_service.py."""

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src import db
from src.actor import Actor
from src.agriculture import service
from src.agriculture.schemas import FarmIn, GisPoint, PlotIn
from src.db.agriculture import FarmRow
from src.exceptions import Forbidden, Invalid, NotFound, Unavailable

FARMER = frozenset({"create:farm:own", "read:farm:own", "update:farm:own"})
SESSION: Any = AsyncMock()  # transaction() commits it; nothing else touches it
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 20


def _actor(perms: frozenset[str] = FARMER, user_id: UUID | None = None) -> Actor:
    return Actor(
        user_id=user_id or uuid4(), roles=frozenset({"farmer"}), perms=perms, channel="api"
    )


def _farm_in(**overrides: Any) -> FarmIn:
    values: dict[str, Any] = {
        "farm_name": "สวน",
        "province_id": uuid4(),
        "district_id": uuid4(),
        "subdistrict_id": uuid4(),
        "zip_code": "50000",
        "contact_name": "สมชาย",
        "phone_number": "0800000000",
        **overrides,
    }
    return FarmIn.model_validate(values)


def _plot_in(**overrides: Any) -> PlotIn:
    values: dict[str, Any] = {
        "farm_id": uuid4(),
        "plot_name": "แปลง 1",
        "land_ownership": "own",
        "cocoa_planted_area": 1.5,
        **overrides,
    }
    return PlotIn.model_validate(values)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes: dict[str, AsyncMock] = {
        name: AsyncMock()
        for name in (
            "insert_farm",
            "link_farmer",
            "insert_plot",
            "farmer_owns_farm",
            "farm_exists",
            "farms",
            "plots",
            "plots_of_farms",
        )
    }
    fakes["farmer_owns_farm"].return_value = True
    fakes["farm_exists"].return_value = True
    fakes["farms"].return_value = []
    fakes["plots"].return_value = []
    fakes["plots_of_farms"].return_value = []
    for name, mock in fakes.items():
        monkeypatch.setattr(db.agriculture, name, mock)
    for name in ("insert_geo", "insert_file", "latest_file_paths"):
        fakes[name] = AsyncMock(return_value={})
        monkeypatch.setattr(db.storage, name, fakes[name])
    fakes["r2_put"] = AsyncMock()
    fakes["r2_sign"] = AsyncMock(return_value="https://signed")
    monkeypatch.setattr(service.r2, "put", fakes["r2_put"])
    monkeypatch.setattr(service.r2, "presigned_get", fakes["r2_sign"])
    monkeypatch.setattr(service.r2, "configured", lambda: True)
    return fakes


# --- shapes -------------------------------------------------------------------


def test_wkt_point_and_closed_polygon_use_lon_lat_with_six_decimals() -> None:
    a, b, c = (
        GisPoint(lat=13.7, lng=100.5),
        GisPoint(lat=13.8, lng=100.6),
        GisPoint(lat=13.9, lng=100.4),
    )

    assert service._wkt_point(a) == "POINT(100.500000 13.700000)"
    assert service._wkt_polygon([a, b, c]) == (
        "POLYGON((100.500000 13.700000,100.600000 13.800000,"
        "100.400000 13.900000,100.500000 13.700000))"
    )


# --- create_farm ----------------------------------------------------------------


async def test_create_farm_writes_farm_owner_and_point(fake: dict[str, AsyncMock]) -> None:
    me = _actor()
    farm = _farm_in(found_date="2026-01-02", gis=[{"lat": 13.7, "lng": 100.5}])

    out = await service.create_farm(SESSION, me, farm)

    farm_kwargs = fake["insert_farm"].await_args.kwargs
    assert fake["insert_farm"].await_args.args[1] == out.farm_id
    assert (farm_kwargs["farm_name"], farm_kwargs["found_date"]) == ("สวน", date(2026, 1, 2))
    assert farm_kwargs["created_at"].tzinfo is None  # naive UTC column
    fake["link_farmer"].assert_awaited_once_with(SESSION, me.user_id, out.farm_id)
    geo = fake["insert_geo"].await_args.kwargs
    assert (geo["wkt"], geo["code"], geo["source_type"]) == (
        "POINT(100.500000 13.700000)",
        str(out.farm_id),
        "farm",
    )
    assert out.message == "ลงทะเบียนฟาร์มและเจ้าของสำเร็จ"


async def test_create_farm_with_an_outline_and_without_gis(fake: dict[str, AsyncMock]) -> None:
    outline = [{"lat": 1, "lng": 2}, {"lat": 3, "lng": 4}, {"lat": 5, "lng": 6}]
    await service.create_farm(SESSION, _actor(), _farm_in(gis=outline))
    assert fake["insert_geo"].await_args.kwargs["wkt"].startswith("POLYGON((")

    fake["insert_geo"].reset_mock()
    await service.create_farm(SESSION, _actor(), _farm_in())
    fake["insert_geo"].assert_not_awaited()


async def test_blank_found_date_means_today_in_bangkok(
    fake: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service.clock, "bangkok_today", lambda: date(2026, 10, 23))

    await service.create_farm(SESSION, _actor(), _farm_in(found_date=" "))

    assert fake["insert_farm"].await_args.kwargs["found_date"] == date(2026, 10, 23)


async def test_malformed_found_date_is_invalid(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Invalid) as caught:
        await service.create_farm(SESSION, _actor(), _farm_in(found_date="02/01/2026"))
    assert caught.value.fields == {"found_date": "Use YYYY-MM-DD"}
    fake["insert_farm"].assert_not_awaited()


async def test_create_farm_forbidden_without_permission(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="create:farm:own"):
        await service.create_farm(SESSION, _actor(frozenset({"read:farm:own"})), _farm_in())
    fake["insert_farm"].assert_not_awaited()


async def test_create_farm_needs_a_person(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="user"):
        await service.create_farm(SESSION, Actor.system(), _farm_in())


# --- create_plot ------------------------------------------------------------------


async def test_create_plot_writes_area_in_rai_and_outline(fake: dict[str, AsyncMock]) -> None:
    me = _actor()
    plot = _plot_in(
        gis=[{"lat": 1, "lng": 2}, {"lat": 3, "lng": 4}, {"lat": 5, "lng": 6}], gis_area_m2=3200
    )

    out = await service.create_plot(SESSION, me, plot)

    kwargs = fake["insert_plot"].await_args.kwargs
    assert (kwargs["farm_id"], kwargs["total_area"], kwargs["cocoa_planted_area"]) == (
        plot.farm_id,
        2.0,
        1.5,
    )
    geo = fake["insert_geo"].await_args.kwargs
    assert (geo["code"], geo["source_type"], geo["area_sq_m"]) == (str(out.plot_id), "plot", 3200)
    assert out.message == "ลงทะเบียนแปลงสำเร็จ"


async def test_create_plot_stores_no_shape_for_a_single_point(fake: dict[str, AsyncMock]) -> None:
    await service.create_plot(SESSION, _actor(), _plot_in(gis=[{"lat": 1, "lng": 2}]))
    fake["insert_geo"].assert_not_awaited()


TWO_POINTS = [{"lat": 1, "lng": 2}, {"lat": 3, "lng": 4}]


async def test_two_points_are_no_outline_and_nothing_is_written(
    fake: dict[str, AsyncMock],
) -> None:
    # PostGIS needs a ring of 4 (3 distinct + closing); 2 points used to
    # reach ST_GeomFromText and roll the whole registration back as a 500.
    with pytest.raises(Invalid) as farm_error:
        await service.create_farm(SESSION, _actor(), _farm_in(gis=TWO_POINTS))
    with pytest.raises(Invalid) as plot_error:
        await service.create_plot(SESSION, _actor(), _plot_in(gis=TWO_POINTS))

    expected = {"gis": "An outline needs at least 3 points"}
    assert farm_error.value.fields == plot_error.value.fields == expected
    fake["insert_farm"].assert_not_awaited()
    fake["insert_plot"].assert_not_awaited()
    fake["insert_geo"].assert_not_awaited()


@pytest.mark.parametrize(
    "point",
    [
        {"lat": 90.1, "lng": 0},
        {"lat": -91, "lng": 0},
        {"lat": 0, "lng": 180.5},
        {"lat": 0, "lng": -181},
    ],
)
def test_coordinates_outside_the_globe_are_refused(point: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        _farm_in(gis=[point])


async def test_create_plot_on_an_unknown_farm_is_not_found(fake: dict[str, AsyncMock]) -> None:
    fake["farm_exists"].return_value = False
    with pytest.raises(NotFound):
        await service.create_plot(SESSION, _actor(), _plot_in())
    fake["insert_plot"].assert_not_awaited()


async def test_create_plot_on_someone_elses_farm_is_forbidden(fake: dict[str, AsyncMock]) -> None:
    fake["farmer_owns_farm"].return_value = False
    with pytest.raises(Forbidden, match="Not your farm"):
        await service.create_plot(SESSION, _actor(), _plot_in())
    fake["insert_plot"].assert_not_awaited()


async def test_create_plot_forbidden_without_permission(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden):
        await service.create_plot(SESSION, _actor(frozenset()), _plot_in())


async def test_create_plot_rejects_a_malformed_date(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Invalid):
        await service.create_plot(SESSION, _actor(), _plot_in(found_date="tomorrow"))


# --- list_farms / list_plots -----------------------------------------------------


def _farm_row(farm_id: UUID) -> FarmRow:
    now = datetime(2026, 10, 1, 1)
    return FarmRow(
        farm_id, "F", None, None, date(2026, 1, 2), None, None, None, None, None,
        None, None, None, None, None, now, now,
    )  # fmt: skip


async def test_list_farms_scopes_to_the_caller_and_attaches_photo_links(
    fake: dict[str, AsyncMock],
) -> None:
    me = _actor()
    with_photo, without_photo = uuid4(), uuid4()
    fake["farms"].return_value = [_farm_row(with_photo), _farm_row(without_photo)]
    fake["latest_file_paths"].return_value = {with_photo: "farms/x/a.jpg"}

    out = await service.list_farms(SESSION, me, page=1, page_size=10)

    fake["farms"].assert_awaited_once_with(SESSION, farmer_id=me.user_id, limit=10, offset=10)
    assert [(f.farm_id, f.image_url, f.plots) for f in out] == [
        (with_photo, "https://signed", []),
        (without_photo, None, []),
    ]
    fake["r2_sign"].assert_awaited_once_with("farms/x/a.jpg", 3600)


async def test_list_farms_with_read_farm_all_sees_everyone(fake: dict[str, AsyncMock]) -> None:
    await service.list_farms(SESSION, _actor(frozenset({"read:farm:all"})))
    assert fake["farms"].await_args.kwargs["farmer_id"] is None


async def test_list_farms_skips_a_link_that_fails_to_sign(fake: dict[str, AsyncMock]) -> None:
    farm_id = uuid4()
    fake["farms"].return_value = [_farm_row(farm_id)]
    fake["latest_file_paths"].return_value = {farm_id: "farms/x/a.jpg"}
    fake["r2_sign"].side_effect = Unavailable("down")

    [farm] = await service.list_farms(SESSION, _actor())

    assert farm.image_url is None


async def test_list_farms_without_r2_has_no_links(
    fake: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service.r2, "configured", lambda: False)
    fake["farms"].return_value = [_farm_row(uuid4())]

    [farm] = await service.list_farms(SESSION, _actor())

    assert farm.image_url is None
    fake["latest_file_paths"].assert_not_awaited()


@pytest.mark.parametrize(
    ("page", "page_size", "limit", "offset"),
    [(-1, 20, 20, 0), (0, 0, 50, 0), (2, 10_000, 500, 1000)],
)
async def test_list_plots_paging_falls_back_like_go(
    fake: dict[str, AsyncMock], page: int, page_size: int, limit: int, offset: int
) -> None:
    await service.list_plots(SESSION, _actor(), page=page, page_size=page_size)
    kwargs = fake["plots"].await_args.kwargs
    assert (kwargs["limit"], kwargs["offset"]) == (limit, offset)


async def test_lists_are_forbidden_without_a_read_permission(fake: dict[str, AsyncMock]) -> None:
    no_read = _actor(frozenset({"create:farm:own"}))
    with pytest.raises(Forbidden, match="read:farm:own"):
        await service.list_farms(SESSION, no_read)
    with pytest.raises(Forbidden, match="read:farm:own"):
        await service.list_plots(SESSION, no_read)


# --- upload_farm_image -----------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "mime", "ext"),
    [(JPEG, "image/jpeg", "jpg"), (PNG, "image/png", "png"), (WEBP, "image/webp", "webp")],
)
async def test_upload_stores_the_object_and_its_record(
    fake: dict[str, AsyncMock], data: bytes, mime: str, ext: str
) -> None:
    me = _actor()
    farm_id = uuid4()

    out = await service.upload_farm_image(
        SESSION, me, farm_id, filename="C:/photos/my farm.jpg", data=data
    )

    key, body, content_type = fake["r2_put"].await_args.args
    assert key.startswith(f"farms/{farm_id}/") and key.endswith(f".{ext}")
    assert (body, content_type) == (data, mime)
    record = fake["insert_file"].await_args.kwargs
    assert (record["storage_path"], record["mime_type"], record["file_size"]) == (
        key,
        mime,
        len(data),
    )
    assert record["original_name"] == "my farm.jpg"
    assert record["uploaded_by"] == me.user_id
    assert len(record["checksum"]) == 64
    assert (out.farm_id, out.image_url) == (farm_id, "https://signed")


async def test_upload_rejects_other_formats_by_their_bytes(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Invalid) as caught:
        await service.upload_farm_image(
            SESSION, _actor(), uuid4(), filename="fake.jpg", data=b"GIF89a..."
        )
    assert caught.value.fields == {"image": "รองรับเฉพาะไฟล์ JPG, PNG หรือ WEBP"}
    fake["r2_put"].assert_not_awaited()


async def test_upload_rejects_more_than_5_mb(fake: dict[str, AsyncMock]) -> None:
    too_big = JPEG + b"0" * (5 * 1024 * 1024)
    with pytest.raises(Invalid) as caught:
        await service.upload_farm_image(SESSION, _actor(), uuid4(), filename="a.jpg", data=too_big)
    assert caught.value.fields == {"image": "รูปต้องมีขนาดไม่เกิน 5 MB"}


async def test_upload_to_someone_elses_farm_is_forbidden(fake: dict[str, AsyncMock]) -> None:
    fake["farmer_owns_farm"].return_value = False
    with pytest.raises(Forbidden, match="Not your farm"):
        await service.upload_farm_image(SESSION, _actor(), uuid4(), filename="a.jpg", data=JPEG)
    fake["r2_put"].assert_not_awaited()


async def test_upload_forbidden_without_permission(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="update:farm:own"):
        await service.upload_farm_image(
            SESSION, _actor(frozenset({"read:farm:own"})), uuid4(), filename="a.jpg", data=JPEG
        )


async def test_upload_is_unavailable_without_r2(
    fake: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service.r2, "configured", lambda: False)
    with pytest.raises(Unavailable):
        await service.upload_farm_image(SESSION, _actor(), uuid4(), filename="a.jpg", data=JPEG)


async def test_upload_failure_writes_no_record(fake: dict[str, AsyncMock]) -> None:
    fake["r2_put"].side_effect = Unavailable("Photo upload failed, please try again")
    with pytest.raises(Unavailable):
        await service.upload_farm_image(SESSION, _actor(), uuid4(), filename="a.jpg", data=JPEG)
    fake["insert_file"].assert_not_awaited()


async def test_upload_still_succeeds_when_the_link_cannot_be_signed(
    fake: dict[str, AsyncMock],
) -> None:
    fake["r2_sign"].side_effect = Unavailable("down")

    out = await service.upload_farm_image(SESSION, _actor(), uuid4(), filename="a.jpg", data=JPEG)

    assert out.image_url is None
    fake["insert_file"].assert_awaited_once()
