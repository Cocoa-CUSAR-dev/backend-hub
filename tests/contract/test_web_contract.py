"""Replays the Kotlin web-backend fixtures against backend-hub (docs-and-plan#270).

One test per recorded case. A case is skipped until its route exists in the
app (the porting PR makes it run), and skipped without RUN_DB_TESTS, because
it needs the seed loaded into a real Postgres. Each case runs inside one
transaction that is rolled back, so cases never see each other's writes.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.auth.tokens import issue_access_token
from src.config import settings
from src.database import engine, get_session
from src.main import app
from tests.contract.fixtures import (
    ACTORS,
    Case,
    Fixture,
    all_fixtures,
    compare,
    seed_sql,
    seed_tables,
    to_date,
    xlsx_summary,
)

FIXTURES = all_fixtures()


def _served(method: str, path: str) -> bool:
    """Whether the app has this route. Read from the OpenAPI paths: FastAPI
    nests included routers, so app.routes doesn't list them flat."""
    return method.lower() in app.openapi().get("paths", {}).get(path, {})


def _skip_reason(fx: Fixture) -> str | None:
    if fx.hub is None:
        return f"{fx.kid} becomes an in-process call; its cases are for service tests"
    if not _served(fx.hub["method"], fx.hub["path"]):
        return f"{fx.kid} -> {fx.after} {fx.hub['method']} {fx.hub['path']} is not ported yet"
    return None


def _cases() -> list[Any]:
    params = []
    for fx in FIXTURES:
        reason = _skip_reason(fx)
        marks = [pytest.mark.skip(reason=reason)] if reason else []
        for i, case in enumerate(fx.cases):
            params.append(pytest.param(fx, case, id=f"{fx.kid}-{i}-{case.name}", marks=marks))
    return params


async def _hub_path(session: AsyncSession, fx: Fixture, case: Case) -> str:
    assert fx.hub is not None
    path: str = fx.hub["path"]
    for hub_name, kotlin_name in fx.hub.get("params", {}).items():
        if kotlin_name.startswith("="):  # a fixed value, e.g. {mode} = "sum"
            path = path.replace("{" + hub_name + "}", kotlin_name[1:])
            continue
        value = case.params[kotlin_name.removeprefix("form_of:")]
        if kotlin_name.startswith("form_of:"):
            # Kotlin took a task id where the hub takes that task's form id (docs-and-plan#271).
            form_id = (
                await session.execute(
                    text("SELECT form_id FROM form.task_form WHERE task_id = :t"), {"t": value}
                )
            ).scalar()
            value = str(form_id or value)
        path = path.replace("{" + hub_name + "}", value)
    for name, value in case.params.items():  # same name on both sides, e.g. {date}
        path = path.replace("{" + name + "}", value)
    return path


async def _headers(session: AsyncSession, case: Case) -> dict[str, str]:
    headers: dict[str, str] = {}
    if case.actor:
        user_id, username = ACTORS[case.actor]
        roles, perms = await db.auth.roles_and_permissions(session, UUID(user_id))
        headers["Authorization"] = "Bearer " + issue_access_token(
            UUID(user_id), username, roles, perms
        )
    if case.service_key:
        ok = case.status != 401
        headers["X-Service-Key"] = settings.CHATBOT_SERVICE_KEY if ok else "wrong-key"
        headers["Authorization"] = "Bearer " + (settings.CRON_SECRET if ok else "wrong-secret")
    return headers


@pytest.fixture
async def seeded() -> AsyncGenerator[AsyncSession, None]:
    if not os.getenv("RUN_DB_TESTS"):
        pytest.skip("RUN_DB_TESTS not set -- the contract replay needs Postgres")
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")

        async def override() -> AsyncGenerator[AsyncSession, None]:
            yield session

        app.dependency_overrides[get_session] = override
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_session, None)
            await session.close()
            await outer.rollback()


# What a case can need from the database beyond its seed, and how to tell the
# database has it. tests/resources/schema.sql has neither: these cases only
# replay against a database built from the migrations.
REQUIREMENTS = {
    # The drawn-area analytics (K-36..K-41).
    "postgis": "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis')",
    # The handler pickers and the Data Map (K-15, K-16, K-CAT) answer with the
    # handler tables' own columns, so every active handler's table must be
    # there with its real columns, as recorded.
    "registry": (
        "SELECT bool_and(to_regclass(schema_name || '.' || table_name) IS NOT NULL)"
        " AND count(*) > 10 FROM form.handler_registry WHERE is_active"
    ),
}


async def _missing_requirement(session: AsyncSession, fx: Fixture, case: Case) -> str | None:
    """A fixture's requirements, plus a single case's own."""
    assert fx.hub is not None
    for need in [*fx.hub.get("requires", []), *case.hub.get("requires", [])]:
        if not (await session.execute(text(REQUIREMENTS[need]))).scalar():
            return need
    return None


@pytest.mark.parametrize(("fx", "case"), _cases())
async def test_hub_answers_like_kotlin(fx: Fixture, case: Case, seeded: AsyncSession) -> None:
    assert fx.hub is not None
    session = seeded
    if need := await _missing_requirement(session, fx, case):
        pytest.skip(f"{fx.kid} needs {need}, which tests/resources/schema.sql doesn't have")
    missing = [
        t
        for t in seed_tables(fx.seeds)
        if (await session.execute(text("SELECT to_regclass(:t)"), {"t": t})).scalar() is None
    ]
    assert not missing, (
        f"{fx.kid} is ported, so its replay runs: add {', '.join(missing)} "
        "to tests/resources/schema.sql "
        "(copy them from the database repo's migrations)"
    )
    raw = await (await session.connection()).get_raw_connection()
    for sql in seed_sql(fx.seeds):
        await raw.driver_connection.execute(sql)  # type: ignore[union-attr]

    path = await _hub_path(session, fx, case)
    query = {**case.query, **fx.hub.get("query", {})}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.request(
            fx.hub["method"],
            path,
            params=query,
            json=case.body,
            headers=await _headers(session, case),
        )
    ctype = r.headers.get("content-type", "")
    if "spreadsheetml" in ctype:
        body: Any = {"xlsx": xlsx_summary(r.content)}
    else:
        body = r.json() if "json" in ctype and r.content else (r.text or None)

    today: date = datetime.now(UTC).date()
    result = compare(case, r.status_code, body, to_date(fx.recorded_on), today)
    assert result.ok, f"{fx.kid} {case.name}: {result.message}"
