"""/api/v1/reports/raw-data: the file comes back as a download, and the
sheets reach the service as asked. The service is faked."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from src.auth import tokens
from src.exceptions import Invalid
from src.reports import service


def _auth() -> dict[str, str]:
    token = tokens.issue_access_token(
        uuid4(), "researcher", frozenset({"researcher"}), frozenset({"read:report:all"})
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=service.Export(content=b"PK-xlsx", filename="raw_data_x.xlsx"))
    monkeypatch.setattr(service, "raw_data_xlsx", mock)
    return mock


async def test_needs_a_bearer_token(client: AsyncClient, fake: AsyncMock) -> None:
    assert (await client.get("/api/v1/reports/raw-data")).status_code == 401
    fake.assert_not_awaited()


async def test_answers_the_file_as_a_download(client: AsyncClient, fake: AsyncMock) -> None:
    r = await client.get("/api/v1/reports/raw-data", headers=_auth())

    assert r.status_code == 200
    assert r.content == b"PK-xlsx"
    assert r.headers["content-type"] == service.MEDIA_TYPE
    assert r.headers["content-disposition"] == "attachment; filename=raw_data_x.xlsx"
    assert fake.await_args.args[2] is None


async def test_passes_the_sheets(client: AsyncClient, fake: AsyncMock) -> None:
    await client.get(
        "/api/v1/reports/raw-data", params=[("sheets", "FARM,HARVEST")], headers=_auth()
    )
    assert fake.await_args.args[2] == ["FARM,HARVEST"]


async def test_bad_sheet_is_422(client: AsyncClient, fake: AsyncMock) -> None:
    fake.side_effect = Invalid(fields={"sheets": "Use one or more of FARM, HARVEST"})

    r = await client.get("/api/v1/reports/raw-data", params={"sheets": "NOPE"}, headers=_auth())

    assert r.status_code == 422
