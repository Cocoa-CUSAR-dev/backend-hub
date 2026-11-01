"""/api/v1/ref/constants/{key}: reaches the service with the caller and the
query filters, and answers a bare JSON list. The service is faked."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from src.auth import tokens
from src.exceptions import NotFound
from src.ref import service

USER_ID = uuid4()


def _auth() -> dict[str, str]:
    token = tokens.issue_access_token(USER_ID, "0812345678", frozenset(), frozenset())
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=[{"province_id": "p", "province_name_th": "เชียงใหม่"}])
    monkeypatch.setattr(service, "constants", mock)
    return mock


async def test_needs_a_bearer_token(client: AsyncClient, fake: AsyncMock) -> None:
    response = await client.get("/api/v1/ref/constants/province")

    assert response.status_code == 401
    fake.assert_not_awaited()


async def test_passes_the_key_and_filters_and_returns_the_list(
    client: AsyncClient, fake: AsyncMock
) -> None:
    response = await client.get(
        "/api/v1/ref/constants/district?province_id=p&zip_code=50300", headers=_auth()
    )

    assert (response.status_code, response.json()) == (
        200,
        [{"province_id": "p", "province_name_th": "เชียงใหม่"}],
    )
    _, actor, key, filters = fake.await_args.args
    assert (actor.user_id, key, filters.province_id, filters.zip_code, filters.farm_id) == (
        USER_ID,
        "district",
        "p",
        "50300",
        None,
    )


async def test_an_unknown_key_is_404(client: AsyncClient, fake: AsyncMock) -> None:
    fake.side_effect = NotFound("Constant key not found")

    response = await client.get("/api/v1/ref/constants/nope", headers=_auth())

    assert (response.status_code, response.json()) == (404, {"detail": "Constant key not found"})
