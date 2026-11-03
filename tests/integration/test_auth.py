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
        "email": None,
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


# ---- email -----------------------------------------------------------------


async def test_sign_in_with_the_username_or_the_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = await client.post(
        f"{AUTH}/register",
        json={**_creds("0822222222", "cocoa-pass"), "email": " Somchai@Example.COM "},
    )
    assert created.status_code == 201
    assert created.json()["user"]["email"] == "somchai@example.com"

    for name in ("0822222222", "somchai@example.com", "SOMCHAI@example.com"):
        response = await client.post(f"{AUTH}/login", json=_creds(name, "cocoa-pass"))
        assert response.status_code == 200, name
        assert response.json()["user"]["username"] == "0822222222"


async def test_email_is_optional_and_a_blank_one_is_none(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    without = await client.post(f"{AUTH}/register", json=_creds("0822222223", "cocoa-pass"))
    blank = await client.post(
        f"{AUTH}/register", json={**_creds("0822222224", "cocoa-pass"), "email": "  "}
    )
    assert without.json()["user"]["email"] is None
    assert blank.json()["user"]["email"] is None


async def test_a_username_that_is_an_email_is_the_email_too(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(f"{AUTH}/register", json=_creds("Ann@Test.com", "cocoa-pass"))
    assert response.json()["user"]["username"] == "Ann@Test.com"
    assert response.json()["user"]["email"] == "ann@test.com"


async def test_a_bad_email_is_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.post(
        f"{AUTH}/register", json={**_creds("0822222225", "cocoa-pass"), "email": "not-an-email"}
    )
    assert response.status_code == 422


async def test_no_name_signs_in_to_two_accounts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    first = {**_creds("0822222226", "cocoa-pass"), "email": "taken@test.com"}
    assert (await client.post(f"{AUTH}/register", json=first)).status_code == 201

    same_email = {**_creds("0822222227", "cocoa-pass"), "email": "TAKEN@test.com"}
    email_as_username = _creds("taken@test.com", "cocoa-pass")
    assert (await client.post(f"{AUTH}/register", json=same_email)).status_code == 409
    assert (await client.post(f"{AUTH}/register", json=email_as_username)).status_code == 409


async def test_a_wrong_password_is_refused_through_the_email_too(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    body = {**_creds("0822222229", "cocoa-pass"), "email": "pw@test.com"}
    assert (await client.post(f"{AUTH}/register", json=body)).status_code == 201
    response = await client.post(f"{AUTH}/login", json=_creds("pw@test.com", "wrong-pass"))
    assert response.status_code == 401


async def test_me_changes_and_removes_the_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = await client.post(f"{AUTH}/register", json=_creds("0822222230", "cocoa-pass"))
    headers = {"Authorization": f"Bearer {created.json()['access_token']}"}
    other = {**_creds("0822222231", "cocoa-pass"), "email": "other@test.com"}
    assert (await client.post(f"{AUTH}/register", json=other)).status_code == 201

    added = await client.patch("/api/v1/me/account", json={"email": "Me@Test.com"}, headers=headers)
    assert added.status_code == 200
    assert added.json()["email"] == "me@test.com"
    assert added.json()["username"] == "0822222230"
    login = await client.post(f"{AUTH}/login", json=_creds("me@test.com", "cocoa-pass"))
    assert login.status_code == 200

    taken = await client.patch(
        "/api/v1/me/account", json={"email": "other@test.com"}, headers=headers
    )
    assert taken.status_code == 409

    # Saving the same address again isn't taken by myself.
    again = await client.patch("/api/v1/me/account", json={"email": "me@test.com"}, headers=headers)
    assert again.status_code == 200

    removed = await client.patch("/api/v1/me/account", json={"email": None}, headers=headers)
    assert removed.json()["email"] is None
    gone = await client.post(f"{AUTH}/login", json=_creds("me@test.com", "cocoa-pass"))
    assert gone.status_code == 401


async def test_changing_the_email_needs_a_signed_in_person(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch("/api/v1/me/account", json={"email": "a@test.com"})
    assert response.status_code == 401
