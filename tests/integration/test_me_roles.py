"""Multi-role: /api/v1/me and /api/v1/admin role grants (RUN_DB_TESTS=1)."""

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.auth_helpers import seed_role, seed_user

AUTH = "/api/v1/auth"
ME = "/api/v1/me"
ADMIN = "/api/v1/admin"
PROFILE_PERMS = ["read:profile:own", "update:profile:own"]

GEO = {
    "province_id": str(uuid.uuid4()),
    "district_id": str(uuid.uuid4()),
    "subdistrict_id": str(uuid.uuid4()),
}
FARMER = {"first_name": "สมชาย", "last_name": "ใจดี", **GEO}
PROCESSOR = {"first_name": "สมชาย", "last_name": "ใจดี", "nickname": "ชาย"}


async def _seed_roles(session: AsyncSession) -> None:
    for role in ("farmer", "hub_collector", "processor", "researcher"):
        await seed_role(session, role, PROFILE_PERMS)
    await seed_role(session, "admin", ["update:role:any"])


def _bearer(pair: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {pair['access_token']}"}


async def _new_account(client: AsyncClient, username: str) -> dict[str, Any]:
    response = await client.post(
        f"{AUTH}/register", json={"username": username, "password": "secret1"}
    )
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


async def test_new_account_has_no_roles_and_no_profiles(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    pair = await _new_account(client, "0811000001")
    me = (await client.get(ME, headers=_bearer(pair))).json()
    assert me["roles"] == []
    assert me["profiles"] == {
        "farmer": None,
        "hub_collector": None,
        "processor": None,
        "researcher": None,
    }


async def test_one_person_becomes_farmer_and_processor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_roles(db_session)
    pair = await _new_account(client, "0811000002")

    as_farmer = await client.post(f"{ME}/roles/farmer", json=FARMER, headers=_bearer(pair))
    assert as_farmer.status_code == 201
    assert as_farmer.json()["user"]["roles"] == ["farmer"]

    # The new token already carries the new role -- no re-login needed.
    as_both = await client.post(
        f"{ME}/roles/processor", json=PROCESSOR, headers=_bearer(as_farmer.json())
    )
    assert as_both.status_code == 201
    assert as_both.json()["user"]["roles"] == ["farmer", "processor"]

    me = (await client.get(ME, headers=_bearer(as_both.json()))).json()
    assert me["roles"] == ["farmer", "processor"]
    # Each profile on its own, never merged.
    assert me["profiles"]["farmer"]["province_id"] == GEO["province_id"]
    assert me["profiles"]["processor"]["nickname"] == "ชาย"
    assert me["profiles"]["farmer"]["nickname"] is None
    assert me["profiles"]["hub_collector"] is None


async def test_adding_a_role_twice_is_a_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_roles(db_session)
    pair = await _new_account(client, "0811000003")
    first = await client.post(f"{ME}/roles/farmer", json=FARMER, headers=_bearer(pair))
    again = await client.post(f"{ME}/roles/farmer", json=FARMER, headers=_bearer(first.json()))
    assert again.status_code == 409


async def test_farmer_profile_needs_its_required_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_roles(db_session)
    pair = await _new_account(client, "0811000004")
    missing = await client.post(
        f"{ME}/roles/farmer", json={"first_name": "ก"}, headers=_bearer(pair)
    )
    assert missing.status_code == 422


async def test_existing_profile_row_without_the_role_is_kept(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Accounts from the old apps can have a profile row but no role row."""
    await _seed_roles(db_session)
    pair = await _new_account(client, "0811000005")
    await db_session.execute(
        text(
            "INSERT INTO processing.processor (user_id, first_name, phone_number)"
            " VALUES (:u, 'เดิม', '0899999999')"
        ),
        {"u": pair["user"]["user_id"]},
    )
    await db_session.commit()

    added = await client.post(f"{ME}/roles/processor", json=PROCESSOR, headers=_bearer(pair))
    assert added.status_code == 201
    me = (await client.get(ME, headers=_bearer(added.json()))).json()
    assert me["profiles"]["processor"]["first_name"] == "สมชาย"
    assert me["profiles"]["processor"]["phone_number"] == "0899999999"


async def test_researcher_signs_up_with_the_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_roles(db_session)
    pair = await _new_account(client, "0811000006")
    body = {"first_name": "Ann", "last_name": "Lee", "organization": "CU"}

    wrong = await client.post(
        f"{ME}/roles/researcher", json={**body, "signup_code": "guess"}, headers=_bearer(pair)
    )
    assert wrong.status_code == 403

    right = await client.post(
        f"{ME}/roles/researcher",
        json={**body, "signup_code": "test-researcher-code"},
        headers=_bearer(pair),
    )
    assert right.status_code == 201
    assert right.json()["user"]["roles"] == ["researcher"]


async def test_edit_profile_only_for_a_role_i_hold(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_roles(db_session)
    pair = (
        await client.post(
            f"{ME}/roles/farmer",
            json=FARMER,
            headers=_bearer(await _new_account(client, "0811000007")),
        )
    ).json()

    edited = await client.patch(
        f"{ME}/profiles/farmer", json={"nickname": "ชาย"}, headers=_bearer(pair)
    )
    assert edited.status_code == 200
    assert edited.json()["nickname"] == "ชาย"
    assert edited.json()["first_name"] == "สมชาย"  # untouched fields stay

    not_mine = await client.patch(
        f"{ME}/profiles/processor", json={"nickname": "x"}, headers=_bearer(pair)
    )
    assert not_mine.status_code == 403

    unknown_field = await client.patch(
        f"{ME}/profiles/farmer", json={"user_id": str(uuid.uuid4())}, headers=_bearer(pair)
    )
    assert unknown_field.status_code == 422


async def test_admin_grants_and_removes_roles(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_roles(db_session)
    admin_id = await seed_user(
        db_session, "admin01", "admin-pass", roles={"admin": ["update:role:any"]}
    )
    admin = (
        await client.post(f"{AUTH}/login", json={"username": "admin01", "password": "admin-pass"})
    ).json()
    target = await _new_account(client, "0811000008")
    target_id = target["user"]["user_id"]

    by_non_admin = await client.post(
        f"{ADMIN}/users/{target_id}/roles/researcher", headers=_bearer(target)
    )
    assert by_non_admin.status_code == 403

    farmer_by_admin = await client.post(
        f"{ADMIN}/users/{target_id}/roles/farmer", headers=_bearer(admin)
    )
    assert farmer_by_admin.status_code == 422  # only researcher / admin here

    granted = await client.post(
        f"{ADMIN}/users/{target_id}/roles/researcher", headers=_bearer(admin)
    )
    assert granted.status_code == 200
    assert granted.json()["roles"] == ["researcher"]
    # The target picks it up on their next refresh.
    refreshed = await client.post(
        f"{AUTH}/refresh", json={"refresh_token": target["refresh_token"]}
    )
    assert refreshed.json()["user"]["roles"] == ["researcher"]

    removed = await client.delete(
        f"{ADMIN}/users/{target_id}/roles/researcher", headers=_bearer(admin)
    )
    assert removed.status_code == 200
    assert removed.json()["roles"] == []
    # Removal signs the user out everywhere.
    stale = await client.post(
        f"{AUTH}/refresh", json={"refresh_token": refreshed.json()["refresh_token"]}
    )
    assert stale.status_code == 401

    self_demote = await client.delete(
        f"{ADMIN}/users/{admin_id}/roles/admin", headers=_bearer(admin)
    )
    assert self_demote.status_code == 403


async def test_admin_creates_a_researcher_who_must_reset_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_roles(db_session)
    await seed_role(db_session, "admin", ["update:role:any", "create:user:any"])
    await seed_user(db_session, "admin02", "admin-pass", roles={"admin": ["create:user:any"]})
    admin = (
        await client.post(f"{AUTH}/login", json={"username": "admin02", "password": "admin-pass"})
    ).json()
    body = {
        "username": "researcher01",
        "temporary_password": "temp-pass",
        "first_name": "Ann",
        "organization": "CU",
    }

    by_nobody = await client.post(
        f"{ADMIN}/users", json=body, headers=_bearer(await _new_account(client, "0811000009"))
    )
    assert by_nobody.status_code == 403

    created = await client.post(f"{ADMIN}/users", json=body, headers=_bearer(admin))
    assert created.status_code == 201
    assert created.json()["roles"] == ["researcher"]
    again = await client.post(f"{ADMIN}/users", json=body, headers=_bearer(admin))
    assert again.status_code == 409

    first_login = (
        await client.post(
            f"{AUTH}/login", json={"username": "researcher01", "password": "temp-pass"}
        )
    ).json()
    assert first_login["user"]["must_reset_password"] is True
    me = (await client.get(ME, headers=_bearer(first_login))).json()
    assert me["profiles"]["researcher"]["organization"] == "CU"
