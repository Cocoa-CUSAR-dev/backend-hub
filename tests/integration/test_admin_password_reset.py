"""POST /api/v1/admin/password-resets: an admin hands a temporary password to
someone with neither email nor LINE (RUN_DB_TESTS=1)."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth import passwords
from tests.integration.auth_helpers import seed_role, seed_user

URL = "/api/v1/admin/password-resets"


async def _login(client: AsyncClient, username: str, password: str):  # type: ignore[no-untyped-def]
    return await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


async def _setup(session: AsyncSession) -> None:
    await seed_role(session, "farmer", ["read:response:own"])
    await seed_user(session, "admin-r", "admin-pass", roles={"admin": ["update:password:any"]})
    await seed_user(session, "0811111111", "old-pass-1", roles={"farmer": []})
    await session.commit()


async def _admin(client: AsyncClient) -> dict[str, str]:
    token = (await _login(client, "admin-r", "admin-pass")).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_the_old_password_and_sessions_stop_and_the_temporary_one_must_be_changed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _setup(db_session)
    admin = await _admin(client)
    old = (await _login(client, "0811111111", "old-pass-1")).json()

    response = await client.post(URL, json={"login": "0811111111"}, headers=admin)

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "0811111111"
    temporary = body["temporary_password"]
    assert len(temporary) == passwords.TEMPORARY_LENGTH
    assert (await _login(client, "0811111111", "old-pass-1")).status_code == 401
    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]}
    )
    assert refreshed.status_code == 401
    signed_in = await _login(client, "0811111111", temporary)
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["must_reset_password"] is True


async def test_each_reset_gives_a_different_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _setup(db_session)
    admin = await _admin(client)

    first = (await client.post(URL, json={"login": "0811111111"}, headers=admin)).json()
    second = (await client.post(URL, json={"login": "0811111111"}, headers=admin)).json()

    assert first["temporary_password"] != second["temporary_password"]
    assert (await _login(client, "0811111111", first["temporary_password"])).status_code == 401


async def test_an_unknown_login_is_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    await _setup(db_session)

    response = await client.post(URL, json={"login": "0899999999"}, headers=await _admin(client))

    assert response.status_code == 404


async def test_only_an_admin_may_do_it(client: AsyncClient, db_session: AsyncSession) -> None:
    await _setup(db_session)
    token = (await _login(client, "0811111111", "old-pass-1")).json()["access_token"]

    as_farmer = await client.post(
        URL, json={"login": "0811111111"}, headers={"Authorization": f"Bearer {token}"}
    )
    anonymous = await client.post(URL, json={"login": "0811111111"})

    assert as_farmer.status_code == 403
    assert anonymous.status_code == 401
    assert (await _login(client, "0811111111", "old-pass-1")).status_code == 200
