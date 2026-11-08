"""Task lists and reminders follow form.assignment (US4-4, docs-and-plan#236).

The rule, shared with mobile-backend's queryTasksForUser: a task that has
assignment rows is for those people only; a task with none is for everyone, as
it always was, so every task that existed before assignments keeps working.

Both queries are raw SQL, so only a real Postgres proves them.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.reminders.service import users_owing_task
from tests.integration.helpers import (
    pending_tasks_of,
    seed_form_response,
    seed_task_form,
    seed_user_with_line_identity,
)


async def _assign(session: AsyncSession, *, task_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await session.execute(
        text("INSERT INTO form.assignment (task_id, user_id) VALUES (:task_id, :user_id)"),
        {"task_id": task_id, "user_id": user_id},
    )
    await session.commit()


async def _farmer(session: AsyncSession) -> uuid.UUID:
    return await seed_user_with_line_identity(session, line_user_id=f"U{uuid.uuid4().hex}")


def _now() -> datetime:
    # form.task timestamps are tz-naive.
    return datetime.now(UTC).replace(tzinfo=None)


class TestPickerFollowsAssignments:
    async def test_a_task_nobody_is_assigned_to_is_listed_for_everyone(
        self, db_session: AsyncSession
    ) -> None:
        task_id, _ = await seed_task_form(db_session, title="legacy")
        a, b = await _farmer(db_session), await _farmer(db_session)

        for user in (a, b):
            assert [t.task_id for t in await pending_tasks_of(db_session, user)] == [task_id]

    async def test_an_assigned_task_is_listed_only_for_its_assignees(
        self, db_session: AsyncSession
    ) -> None:
        task_id, _ = await seed_task_form(db_session, title="assigned")
        a, b = await _farmer(db_session), await _farmer(db_session)
        await _assign(db_session, task_id=task_id, user_id=a)

        assert [t.task_id for t in await pending_tasks_of(db_session, a)] == [task_id]
        assert await pending_tasks_of(db_session, b) == []

    async def test_only_the_task_assigned_to_someone_else_drops_out(
        self, db_session: AsyncSession
    ) -> None:
        legacy, _ = await seed_task_form(db_session, title="legacy")
        mine, _ = await seed_task_form(db_session, title="mine")
        theirs, _ = await seed_task_form(db_session, title="theirs")
        me, other = await _farmer(db_session), await _farmer(db_session)
        await _assign(db_session, task_id=mine, user_id=me)
        await _assign(db_session, task_id=theirs, user_id=other)

        listed = {t.task_id for t in await pending_tasks_of(db_session, me)}

        assert listed == {legacy, mine}

    async def test_an_assigned_multi_submit_task_stays_listed_after_a_response(
        self, db_session: AsyncSession
    ) -> None:
        """Assignment only decides who sees the task; the multi-submit rule
        (stay listed after the first response) is untouched."""
        task_id, _ = await seed_task_form(db_session, title="grades", is_multiple_submit=True)
        me = await _farmer(db_session)
        await _assign(db_session, task_id=task_id, user_id=me)
        await seed_form_response(db_session, task_id=task_id, user_id=me)

        assert [t.task_id for t in await pending_tasks_of(db_session, me)] == [task_id]

    async def test_an_assigned_single_submit_task_disappears_once_submitted(
        self, db_session: AsyncSession
    ) -> None:
        task_id, _ = await seed_task_form(db_session, title="once")
        me = await _farmer(db_session)
        await _assign(db_session, task_id=task_id, user_id=me)
        await seed_form_response(db_session, task_id=task_id, user_id=me)

        assert await pending_tasks_of(db_session, me) == []


class TestReminderFollowsAssignments:
    async def test_everyone_with_line_owes_a_task_nobody_is_assigned_to(
        self, db_session: AsyncSession
    ) -> None:
        task_id, _ = await seed_task_form(db_session, title="legacy")
        a, b = await _farmer(db_session), await _farmer(db_session)

        owing = await users_owing_task(db_session, task_id, _now())

        assert set(owing) == {a, b}

    async def test_only_the_assignees_owe_an_assigned_task(self, db_session: AsyncSession) -> None:
        task_id, _ = await seed_task_form(db_session, title="assigned")
        a, b = await _farmer(db_session), await _farmer(db_session)
        await _assign(db_session, task_id=task_id, user_id=a)

        owing = await users_owing_task(db_session, task_id, _now())

        assert owing == [a]
        assert b not in owing

    async def test_an_assignee_who_already_submitted_no_longer_owes_it(
        self, db_session: AsyncSession
    ) -> None:
        task_id, _ = await seed_task_form(db_session, title="assigned")
        done, pending = await _farmer(db_session), await _farmer(db_session)
        await _assign(db_session, task_id=task_id, user_id=done)
        await _assign(db_session, task_id=task_id, user_id=pending)
        await seed_form_response(db_session, task_id=task_id, user_id=done)

        owing = await users_owing_task(db_session, task_id, _now())

        assert owing == [pending]
