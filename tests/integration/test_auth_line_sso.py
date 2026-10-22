"""LINE login/link, SSO exchange and the LIFF door (RUN_DB_TESTS=1).
LINE's verify endpoint is mocked; everything else is real.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.auth import service
from src.config import settings
from src.exceptions import InvalidLiffToken
from src.line import liff_tasks
from tests.integration.auth_helpers import seed_user

AUTH = "/api/v1/auth"


@pytest.fixture(autouse=True)
def _line_channel() -> Iterator[None]:
    with patch.object(settings, "LINE_LOGIN_CHANNEL_IDS", ["1650000000"]):
        yield


def _line_says(sub: str, name: str = "สมชาย") -> Any:
    return patch(
        "src.line.liff.verify_id_token", AsyncMock(return_value={"sub": sub, "name": name})
    )


async def test_link_then_line_login(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await seed_user(db_session, "0812000001", "secret1")

    with _line_says("Ufarmer1"):
        not_yet = await client.post(f"{AUTH}/line/login", json={"id_token": "liff-tok"})
        assert not_yet.status_code == 403

        linked = await client.post(
            f"{AUTH}/line/link",
            json={"id_token": "liff-tok", "username": "0812000001", "password": "secret1"},
        )
        assert linked.status_code == 200
        assert linked.json()["user"]["user_id"] == str(user_id)

        again = await client.post(f"{AUTH}/line/login", json={"id_token": "liff-tok"})
        assert again.status_code == 200
        assert again.json()["user"]["user_id"] == str(user_id)

    assert await db.auth.line_user_ids_for(db_session, [user_id]) == {user_id: "Ufarmer1"}


async def test_link_needs_the_right_password_and_a_free_line_account(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_user(db_session, "0812000002", "secret1")
    await seed_user(db_session, "0812000003", "secret1")
    body = {"id_token": "liff-tok", "username": "0812000002", "password": "wrong1"}

    with _line_says("Ushared"):
        assert (await client.post(f"{AUTH}/line/link", json=body)).status_code == 401
        body["password"] = "secret1"
        assert (await client.post(f"{AUTH}/line/link", json=body)).status_code == 200
        body["username"] = "0812000003"
        taken = await client.post(f"{AUTH}/line/link", json=body)
        assert taken.status_code == 409


async def test_line_login_with_a_token_line_rejects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    with patch("src.line.liff.verify_id_token", AsyncMock(side_effect=InvalidLiffToken)):
        response = await client.post(f"{AUTH}/line/login", json={"id_token": "forged"})
    assert response.status_code == 400


async def test_sso_link_works_once(client: AsyncClient, db_session: AsyncSession) -> None:
    user_id = await seed_user(db_session, "0812000004", "secret1")
    token = service.sso_mint(user_id)

    first = await client.post(f"{AUTH}/sso/exchange", json={"token": token})
    assert first.status_code == 200
    assert first.json()["user"]["user_id"] == str(user_id)

    replay = await client.post(f"{AUTH}/sso/exchange", json={"token": token})
    assert replay.status_code == 401

    # An SSO token is not an API credential.
    as_bearer = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert as_bearer.status_code == 401


async def test_liff_door_accepts_our_own_token_without_calling_line(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await seed_user(db_session, "0812000005", "secret1")
    with _line_says("Uliff5"):
        pair = (
            await client.post(
                f"{AUTH}/line/link",
                json={"id_token": "liff-tok", "username": "0812000005", "password": "secret1"},
            )
        ).json()

    with patch("src.line.liff.verify_id_token", AsyncMock()) as line_verify:
        resolved = await liff_tasks.resolve_liff_user(
            session=db_session, authorization=f"Bearer {pair['access_token']}"
        )
    line_verify.assert_not_awaited()
    assert (resolved.user_id, resolved.line_user_id) == (user_id, "Uliff5")
