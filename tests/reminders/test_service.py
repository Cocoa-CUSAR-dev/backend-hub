"""Who may manage reminder schedules: whoever may create forms."""

from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.actor import Actor
from src.exceptions import Forbidden
from src.reminders import service


def _actor(*perms: str) -> Actor:
    return Actor(user_id=uuid4(), roles=frozenset({"x"}), perms=frozenset(perms), channel="api")


@pytest.fixture
def session() -> MagicMock:
    # commit/rollback are awaited by transaction()
    return MagicMock(commit=AsyncMock(), rollback=AsyncMock())


async def test_creating_needs_create_form_all(session: MagicMock) -> None:
    with (
        patch("src.db.notify.create_schedule", new=AsyncMock()) as create,
        pytest.raises(Forbidden),
    ):
        await service.create_schedule(
            session,
            _actor("read:form:assigned"),
            task_id=uuid4(),
            time_of_day=time(9, 0),
            created_by=uuid4(),
            recipients=[],
        )
    create.assert_not_awaited()


async def test_listing_needs_create_form_all(session: MagicMock) -> None:
    with (
        patch("src.db.notify.list_schedules_for_task", new=AsyncMock()) as read,
        pytest.raises(Forbidden),
    ):
        await service.list_schedules(session, _actor(), uuid4())
    read.assert_not_awaited()


async def test_updating_needs_create_form_all(session: MagicMock) -> None:
    with (
        patch("src.db.notify.update_schedule", new=AsyncMock()) as update,
        pytest.raises(Forbidden),
    ):
        await service.update_schedule(session, _actor(), uuid4(), is_active=False)
    update.assert_not_awaited()


async def test_a_researcher_may_list(session: MagicMock) -> None:
    task_id = uuid4()
    with patch("src.db.notify.list_schedules_for_task", new=AsyncMock(return_value=[])) as read:
        rows = await service.list_schedules(session, _actor("create:form:all"), task_id)

    assert rows == []
    read.assert_awaited_once_with(session, task_id)


async def test_the_system_may_do_all_three(session: MagicMock) -> None:
    task_id = uuid4()
    with (
        patch("src.db.notify.list_schedules_for_task", new=AsyncMock(return_value=[])),
        patch("src.db.notify.update_schedule", new=AsyncMock(return_value="row")),
    ):
        assert await service.list_schedules(session, Actor.system(), task_id) == []
        assert (
            await service.update_schedule(session, Actor.system(), uuid4(), is_active=False)
            == "row"
        )
