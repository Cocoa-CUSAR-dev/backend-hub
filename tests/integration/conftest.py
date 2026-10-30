"""DB-gated fixtures for integration tests -- kept in tests/integration/ only
so files outside this directory never pull in a real Postgres dependency.
"""

import os
from collections.abc import AsyncGenerator, Generator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import async_session_maker
from tests.integration.helpers import SERVED_FORMS

_CLEANUP_TABLES = (
    "agriculture.plot",
    "agriculture.farmer_farm",
    "agriculture.farm",
    "notify.reminder_log",
    "notify.reminder_recipient",
    "notify.reminder_schedule",
    "chat.conversation_answer",
    "chat.conversation",
    "auth.line_identity",
    "form.assignment",
    "form.response",
    "form.question",
    "form.task_form",
    "form.task",
    "auth.refresh_token",
    "auth.sso_used_token",
    "agriculture.farmer",
    "processing.processor",
    "processing.hub_collector",
    "research.researcher",
    "auth.user_role",
    "auth.role_permission",
    "auth.permission",
    "auth.role",
    "auth.user_account",
)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """The app's own async_session_maker, not a session pinned to one
    connection+SAVEPOINT -- webhook-level tests need the code under test to
    see committed rows from a different connection. Cleaned up by deleting
    every row afterward instead of rolling back. Skips unless RUN_DB_TESTS
    is set.
    """
    if not os.getenv("RUN_DB_TESTS"):
        pytest.skip("RUN_DB_TESTS not set -- no local Postgres to test against")

    async with async_session_maker() as session:
        try:
            yield session
        finally:
            for table in _CLEANUP_TABLES:
                await session.execute(text(f"DELETE FROM {table}"))
            await session.commit()


@pytest.fixture(autouse=True)
def served_forms(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Forms are read from the database in production. Here the tests do not
    seed form rows; they `serve_form(...)` the form they want, and the
    database read (forms.detail.load_form) answers with it. None for a form
    nobody served, like a form that does not exist.
    """

    async def load_served_form(_session: object, form_id: object) -> object:
        return SERVED_FORMS.get(form_id)  # type: ignore[call-overload]

    monkeypatch.setattr("src.forms.detail.load_form", load_served_form)
    SERVED_FORMS.clear()
    yield
    SERVED_FORMS.clear()
