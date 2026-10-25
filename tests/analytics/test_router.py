"""/api/v1/analytics routes: the mode and the web app's query names reach the
service, and the JSON keys are Kotlin's ("from", not "from_"). Service faked."""

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from src.analytics import service
from src.analytics.schemas import DataItemOut, SeriesOut, SummaryOut, TimeSeriesOut
from src.auth import tokens

A = "/api/v1/analytics"
SERIES = TimeSeriesOut(
    from_="2026-08",
    to="2026-10",
    title="t",
    metadata={},
    series=[SeriesOut(label="Grade A", values=[1.0], unit="kg")],
)
SUMMARY = SummaryOut(
    from_="2026-08",
    to="2026-10",
    title="t",
    metadata={},
    data=DataItemOut(label="x", value=1, unit=None),
)


def _auth() -> dict[str, str]:
    token = tokens.issue_access_token(
        uuid4(), "researcher", frozenset({"researcher"}), frozenset({"read:report:all"})
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {
        "harvest_series": AsyncMock(return_value=SERIES),
        "harvest_summary": AsyncMock(return_value=SUMMARY),
        "user_series": AsyncMock(return_value=SERIES),
        "user_summary": AsyncMock(return_value=SUMMARY),
    }
    for name, mock in fakes.items():
        monkeypatch.setattr(service, name, mock)
    return fakes


async def test_needs_a_bearer_token(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    assert (await client.get(f"{A}/harvest/time-series/sum")).status_code == 401


async def test_harvest_series_passes_mode_place_and_grade(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    province = uuid4()
    r = await client.get(
        f"{A}/harvest/time-series/average",
        params={"from": "2026-08", "to": "2026-10", "provinceId": str(province), "gradeCode": "A"},
        headers=_auth(),
    )

    assert r.status_code == 200
    assert r.json()["from"] == "2026-08" and "from_" not in r.json()
    args = fake["harvest_series"].await_args
    assert args.args[2] == "average"
    assert args.kwargs["place"] == service.Place(province_id=province)
    assert (args.kwargs["from_"], args.kwargs["grade_code"]) == ("2026-08", "A")


async def test_area_routes_take_the_drawn_area(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    area = '{"type":"Polygon","coordinates":[]}'
    r = await client.post(
        f"{A}/harvest/spatial/summary/count",
        params={"from": "2026-08", "to": "2026-10"},
        json={"geoJson": area},
        headers=_auth(),
    )

    assert r.status_code == 200
    kwargs: dict[str, Any] = fake["harvest_summary"].await_args.kwargs
    assert kwargs["area"] == area and "place" not in kwargs


async def test_area_routes_accept_an_empty_body(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    r = await client.post(
        f"{A}/harvest/spatial/time-series/delta",
        params={"from": "2026-08", "to": "2026-10"},
        json={},
        headers=_auth(),
    )
    assert r.status_code == 200
    assert fake["harvest_series"].await_args.kwargs["area"] is None


async def test_users_pass_the_role(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    await client.get(
        f"{A}/users/summary/count",
        params={"from": "2026-07", "to": "2026-10", "roleName": "farmer"},
        headers=_auth(),
    )
    assert fake["user_summary"].await_args.kwargs["role_name"] == "farmer"


@pytest.mark.parametrize(
    "path",
    [
        "/harvest/time-series/median",
        "/harvest/summary/average",
        "/users/time-series/frequency",
        "/users/summary/total",
    ],
)
async def test_unknown_mode_is_422(
    client: AsyncClient, fake: dict[str, AsyncMock], path: str
) -> None:
    r = await client.get(f"{A}{path}", params={"from": "2026-08", "to": "2026-10"}, headers=_auth())
    assert r.status_code == 422
