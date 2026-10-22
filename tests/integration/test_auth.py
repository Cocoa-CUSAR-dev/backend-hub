"""/api/v1/auth/* end to end against Postgres (RUN_DB_TESTS=1)."""

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth import service, tokens
from tests.integration.auth_helpers import seed_user

AUTH = "/api/v1/auth"


def _creds(username: str, password: str) -> dict[str, str]:
    return {"username": username, "password": password}


async def test_login_returns_tokens_in_the_body_with_every_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await seed_user(
        db_session,
        "0811111111",
        "farmer-pass",
        roles={"farmer": ["read:farm:own"], "processor": ["read:station:own"]},
    )

    response = await client.post(f"{AUTH}/login", json=_creds("0811111111", "farmer-pass"))

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == {
        "user_id": str(user_id),
        "username": "0811111111",
        "roles": ["farmer", "processor"],
        "must_reset_password": False,
    }
    claims = tokens.read_access_token(body["access_token"])
    assert claims.perms == {"read:farm:own", "read:station:own"}


async def test_wrong_password_and_unknown_user_get_the_same_thai_message(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_user(db_session, "0822222222", "right-pass")
    wrong = await client.post(f"{AUTH}/login", json=_creds("0822222222", "nope00"))
    unknown = await client.post(f"{AUTH}/login", json=_creds("0899999999", "nope00"))
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"}


async def test_register_then_login(client: AsyncClient, db_session: AsyncSession) -> None:
    created = await client.post(f"{AUTH}/register", json=_creds("0833333333", "123456"))
    assert created.status_code == 201
    assert created.json()["user"]["roles"] == []

    again = await client.post(f"{AUTH}/register", json=_creds("0833333333", "123456"))
    assert again.status_code == 409

    short = await client.post(f"{AUTH}/register", json=_creds("0844444444", "12345"))
    assert short.status_code == 422
    assert "password" in short.json()["fields"]

    login = await client.post(f"{AUTH}/login", json=_creds("0833333333", "123456"))
    assert login.status_code == 200


async def test_refresh_rotates_and_reuse_signs_out_everywhere(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_user(db_session, "0855555555", "secret1")
    first = (await client.post(f"{AUTH}/login", json=_creds("0855555555", "secret1"))).json()
    old = {"refresh_token": first["refresh_token"]}

    second = await client.post(f"{AUTH}/refresh", json=old)
    assert second.status_code == 200
    rotated = {"refresh_token": second.json()["refresh_token"]}
    assert rotated != old

    # Within the grace window a replay is refused but nothing is revoked.
    assert (await client.post(f"{AUTH}/refresh", json=old)).status_code == 401
    newest = await client.post(f"{AUTH}/refresh", json=rotated)
    assert newest.status_code == 200, newest.json()

    # Past the grace window a replay means theft: every session is revoked.
    await db_session.execute(
        text(
            "UPDATE auth.refresh_token SET used_at = used_at - interval '5 minutes'"
            " WHERE used_at IS NOT NULL"
        ),
    )
    await db_session.commit()
    assert (await client.post(f"{AUTH}/refresh", json=old)).status_code == 401
    still_live = (
        await db_session.execute(
            text("SELECT count(*) FROM auth.refresh_token WHERE revoked_at IS NULL")
        )
    ).scalar_one()
    assert still_live == 0
    latest = {"refresh_token": newest.json()["refresh_token"]}
    assert (await client.post(f"{AUTH}/refresh", json=latest)).status_code == 401


async def test_logout_revokes_the_refresh_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_user(db_session, "0866666666", "secret1")
    pair = (await client.post(f"{AUTH}/login", json=_creds("0866666666", "secret1"))).json()
    refresh = {"refresh_token": pair["refresh_token"]}

    assert (await client.post(f"{AUTH}/logout", json=refresh)).status_code == 204
    assert (await client.post(f"{AUTH}/refresh", json=refresh)).status_code == 401


async def test_reset_password_needs_bearer_and_signs_out_other_devices(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_user(db_session, "0877777777", "old-pass", requires_reset=True)
    phone = (await client.post(f"{AUTH}/login", json=_creds("0877777777", "old-pass"))).json()
    laptop = (await client.post(f"{AUTH}/login", json=_creds("0877777777", "old-pass"))).json()
    assert phone["user"]["must_reset_password"] is True

    no_token = await client.patch(f"{AUTH}/reset-password", json={"new_password": "new-pass"})
    assert no_token.status_code == 401

    done = await client.patch(
        f"{AUTH}/reset-password",
        json={"new_password": "new-pass"},
        headers={"Authorization": f"Bearer {phone['access_token']}"},
    )
    assert done.status_code == 200
    assert done.json()["user"]["must_reset_password"] is False
    laptop_refresh = {"refresh_token": laptop["refresh_token"]}
    assert (await client.post(f"{AUTH}/refresh", json=laptop_refresh)).status_code == 401
    relogin = await client.post(f"{AUTH}/login", json=_creds("0877777777", "new-pass"))
    assert relogin.status_code == 200


async def test_load_actor_unions_permissions_of_every_role(db_session: AsyncSession) -> None:
    user_id = await seed_user(
        db_session,
        "0888888888",
        "secret1",
        roles={
            "farmer": ["read:profile:own", "read:farm:own"],
            "processor": ["read:profile:own", "read:station:own"],
        },
    )
    actor = await service.load_actor(db_session, user_id, channel="line")
    assert actor.roles == {"farmer", "processor"}
    assert actor.perms == {"read:profile:own", "read:farm:own", "read:station:own"}
