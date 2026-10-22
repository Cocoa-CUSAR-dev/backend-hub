"""agriculture.service end to end against real Postgres: what a registration
actually leaves in the tables, and what the lists then return. R2 is faked."""

from collections.abc import Iterator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.actor import Actor
from src.agriculture import service
from src.agriculture.schemas import FarmIn, PlotIn
from src.exceptions import Forbidden
from tests.db.conftest import insert

FARMER = frozenset({"create:farm:own", "read:farm:own", "update:farm:own"})


@pytest.fixture(autouse=True)
def fake_r2(monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncMock]:
    put = AsyncMock()
    monkeypatch.setattr(service.r2, "configured", lambda: True)
    monkeypatch.setattr(service.r2, "put", put)
    monkeypatch.setattr(
        service.r2, "presigned_get", AsyncMock(side_effect=lambda key, _: f"https://r2/{key}")
    )
    yield put


async def _farmer(session: AsyncSession) -> Actor:
    user_id = await insert(session, "auth.user_account", username="farmer")
    assert isinstance(user_id, UUID)
    return Actor(user_id=user_id, roles=frozenset({"farmer"}), perms=FARMER, channel="mobile")


def _farm(name: str, **extra: object) -> FarmIn:
    return FarmIn.model_validate(
        {
            "farm_name": name,
            "province_id": str(uuid4()),
            "district_id": str(uuid4()),
            "subdistrict_id": str(uuid4()),
            "zip_code": "50000",
            "contact_name": "สมชาย",
            "phone_number": "0800000000",
            "found_date": "2026-01-02",
            **extra,
        }
    )


async def test_register_farm_and_plots_then_list_them(db_session: AsyncSession) -> None:
    me = await _farmer(db_session)
    farm = await service.create_farm(
        db_session, me, _farm("สวนเหนือ", gis=[{"lat": 13.7, "lng": 100.5}])
    )
    plot = await service.create_plot(
        db_session,
        me,
        PlotIn.model_validate(
            {
                "farm_id": str(farm.farm_id),
                "plot_name": "แปลง 1",
                "land_ownership": "own",
                "cocoa_planted_area": 1.5,
                "gis": [{"lat": 1, "lng": 2}, {"lat": 3, "lng": 4}, {"lat": 5, "lng": 6}],
                "gis_area_m2": 1600,
            }
        ),
    )

    [listed] = await service.list_farms(db_session, me)
    [listed_plot] = await service.list_plots(db_session, me)

    assert (listed.farm_id, listed.farm_name, listed.image_url) == (farm.farm_id, "สวนเหนือ", None)
    assert [p.plot_id for p in listed.plots] == [plot.plot_id]
    assert (listed_plot.plot_id, listed_plot.total_area) == (plot.plot_id, 1.0)
    shapes = (
        await db_session.execute(
            text("SELECT source_type, code, geom FROM storage.geo ORDER BY source_type")
        )
    ).all()
    assert [tuple(s) for s in shapes] == [
        ("farm", str(farm.farm_id), "SRID=4326;POINT(100.500000 13.700000)"),
        (
            "plot",
            str(plot.plot_id),
            "SRID=4326;POLYGON((2.000000 1.000000,4.000000 3.000000,"
            "6.000000 5.000000,2.000000 1.000000))",
        ),
    ]


async def test_lists_only_show_my_farms_and_plots(db_session: AsyncSession) -> None:
    me = await _farmer(db_session)
    other = await _farmer(db_session)
    await service.create_farm(db_session, other, _farm("theirs"))
    mine = await service.create_farm(db_session, me, _farm("mine"))

    assert [f.farm_id for f in await service.list_farms(db_session, me)] == [mine.farm_id]
    assert await service.list_plots(db_session, me) == []


async def test_plot_on_another_farmers_farm_is_refused(db_session: AsyncSession) -> None:
    me = await _farmer(db_session)
    other = await _farmer(db_session)
    theirs = await service.create_farm(db_session, other, _farm("theirs"))

    with pytest.raises(Forbidden):
        await service.create_plot(
            db_session,
            me,
            PlotIn.model_validate(
                {
                    "farm_id": str(theirs.farm_id),
                    "plot_name": "x",
                    "land_ownership": "own",
                    "cocoa_planted_area": 1,
                }
            ),
        )


async def test_uploaded_photo_shows_up_on_the_farm_list(
    db_session: AsyncSession, fake_r2: AsyncMock
) -> None:
    me = await _farmer(db_session)
    farm = await service.create_farm(db_session, me, _farm("สวน"))

    uploaded = await service.upload_farm_image(
        db_session, me, farm.farm_id, filename="farm.png", data=b"\x89PNG\r\n\x1a\n1234"
    )
    [listed] = await service.list_farms(db_session, me)

    key = fake_r2.await_args.args[0]
    assert uploaded.image_url == listed.image_url == f"https://r2/{key}"
