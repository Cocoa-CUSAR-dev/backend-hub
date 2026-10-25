"""/api/v1/admin/researchers (ADMIN-02..04) through HTTP, real Postgres
(RUN_DB_TESTS=1). The answers against Kotlin's are in tests/contract/
(K-45, K-47, K-49)."""

from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.auth_helpers import seed_role, seed_user

ADMIN = "/api/v1/admin/researchers"
ADMIN_PERMS = ["read:researcher:all", "delete:researcher:any"]


async def _login(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    pair = (
        await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    ).json()
    return {"Authorization": f"Bearer {pair['access_token']}"}


async def _researcher(
    session: AsyncSession, username: str, first: str, last: str, org: str, **roles: list[str]
) -> UUID:
    user_id = await seed_user(
        session, username, "pass-1234", roles={"researcher": ["read:form:all"], **roles}
    )
    await session.execute(
        text(
            "INSERT INTO research.researcher (user_id, first_name, last_name, organization)"
            " VALUES (:u, :f, :l, :o)"
        ),
        {"u": user_id, "f": first, "l": last, "o": org},
    )
    await session.commit()  # the app reads it on its own connection
    return user_id


async def _setup(session: AsyncSession) -> tuple[UUID, UUID]:
    await seed_role(session, "farmer", ["read:response:own"])
    await seed_user(session, "admin-r", "admin-pass", roles={"admin": ADMIN_PERMS})
    one = await _researcher(session, "res-1", "วิจัย", "หนึ่ง", "CUSAR")
    two = await _researcher(session, "res-2", "วิจัย", "สอง", "CU Intania", farmer=[])
    return one, two


async def test_list_filter_read_and_page(client: AsyncClient, db_session: AsyncSession) -> None:
    one, two = await _setup(db_session)
    admin = await _login(client, "admin-r", "admin-pass")

    everyone = (await client.get(ADMIN, headers=admin)).json()
    by_org = (await client.get(f"{ADMIN}?organization=CUSAR", headers=admin)).json()
    by_name = (await client.get(f"{ADMIN}?firstName=วิจัย&lastName=สอง", headers=admin)).json()
    first_page = (await client.get(f"{ADMIN}?size=1", headers=admin)).json()
    read = await client.get(f"{ADMIN}/{one}", headers=admin)

    assert [r["userId"] for r in everyone] == [str(two), str(one)]  # by last name: สอง < หนึ่ง
    assert [r["organization"] for r in by_org] == ["CUSAR"]
    assert [r["userId"] for r in by_name] == [str(two)]
    assert len(first_page) == 1
    assert read.json() == {
        "userId": str(one),
        "firstName": "วิจัย",
        "lastName": "หนึ่ง",
        "organization": "CUSAR",
    }
    assert (await client.get(f"{ADMIN}/{uuid4()}", headers=admin)).status_code == 404
    assert (await client.get(f"{ADMIN}?size=0", headers=admin)).status_code == 422


async def test_delete_takes_only_the_researcher_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _one, two = await _setup(db_session)
    admin = await _login(client, "admin-r", "admin-pass")

    deleted = await client.delete(f"{ADMIN}/{two}", headers=admin)

    assert (deleted.status_code, deleted.json()) == (200, "Successfully deleted researcher")
    roles = await db_session.execute(
        text(
            "SELECT r.role_name FROM auth.user_role ur JOIN auth.role r USING (role_id)"
            " WHERE ur.user_id = :u"
        ),
        {"u": two},
    )
    assert list(roles.scalars()) == ["farmer"]  # Kotlin removed this one too
    assert (await client.get(f"{ADMIN}/{two}", headers=admin)).status_code == 404
    assert (await client.delete(f"{ADMIN}/{two}", headers=admin)).status_code == 404


async def test_researchers_are_for_admins(client: AsyncClient, db_session: AsyncSession) -> None:
    one, _two = await _setup(db_session)
    researcher = await _login(client, "res-1", "pass-1234")

    assert (await client.get(ADMIN, headers=researcher)).status_code == 403
    assert (await client.delete(f"{ADMIN}/{one}", headers=researcher)).status_code == 403
    assert (await client.get(ADMIN)).status_code == 401
