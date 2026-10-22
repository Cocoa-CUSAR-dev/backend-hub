"""A real-Postgres session for query tests, rolled back after each test.

The code under test only reads or writes through the session it is given and
never commits, so wrapping each test in one outer transaction and rolling it
back leaves the database exactly as it was -- no cleanup list to keep in
step. Skips unless RUN_DB_TESTS is set.

Also used by service tests that want real rows (tests/forms, tests/tasks):
they import `db_session` from here.
"""

import os
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import engine


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    if not os.getenv("RUN_DB_TESTS"):
        pytest.skip("RUN_DB_TESTS not set -- no local Postgres to test against")
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


async def insert(session: AsyncSession, table: str, **values: Any) -> UUID | None:
    """INSERT one row and return its first column (the uuid primary key)."""
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    row = (
        await session.execute(
            text(f"INSERT INTO {table} ({columns}) VALUES ({params}) RETURNING *"),
            values,
        )
    ).first()
    return row[0] if row else None
