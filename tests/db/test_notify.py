from datetime import datetime, time
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.db.notify import Recipient
from src.exceptions import InvalidReminderReference, ReminderNotFound
from tests.db.conftest import insert


async def _user(session: AsyncSession) -> UUID:
    user_id = await insert(session, "auth.user_account", username=f"user-{uuid4()}")
    assert user_id is not None
    return user_id


async def _role(session: AsyncSession) -> UUID:
    role_id = await insert(session, "auth.role", role_name=f"role-{uuid4()}")
    assert role_id is not None
    return role_id


async def _task(session: AsyncSession, title: str | None = "harvest") -> UUID:
    task_id = await insert(session, "form.task", title=title)
    assert task_id is not None
    return task_id


async def _schedule(
    session: AsyncSession,
    task_id: UUID,
    created_by: UUID,
    at: time = time(9, 0),
    recipients: list[Recipient] | None = None,
) -> UUID:
    return await db.notify.create_schedule(
        session,
        task_id=task_id,
        time_of_day=at,
        created_by=created_by,
        recipients=recipients or [],
    )


async def test_a_new_schedule_is_daily_and_active(db_session: AsyncSession) -> None:
    task, user = await _task(db_session), await _user(db_session)

    schedule_id = await _schedule(db_session, task, user, time(17, 0))

    [row] = await db.notify.list_schedules_for_task(db_session, task)
    assert (row.schedule_id, row.cadence, row.time_of_day, row.is_active) == (
        schedule_id,
        "DAILY",
        time(17, 0),
        True,
    )
    assert row.created_by == user
    assert row.recipients == ()


async def test_recipients_are_saved_and_repeats_collapsed(db_session: AsyncSession) -> None:
    task, user, role = await _task(db_session), await _user(db_session), await _role(db_session)
    target = await _user(db_session)

    await _schedule(
        db_session,
        task,
        user,
        recipients=[
            Recipient("ROLE", role),
            Recipient("USER", target),
            Recipient("ROLE", role),
        ],
    )

    [row] = await db.notify.list_schedules_for_task(db_session, task)
    assert set(row.recipients) == {Recipient("ROLE", role), Recipient("USER", target)}
    assert len(row.recipients) == 2


async def test_an_unknown_task_or_creator_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(InvalidReminderReference):
        await _schedule(db_session, uuid4(), await _user(db_session))


async def test_an_unknown_recipient_is_rejected(db_session: AsyncSession) -> None:
    task, user = await _task(db_session), await _user(db_session)
    with pytest.raises(InvalidReminderReference):
        await _schedule(db_session, task, user, recipients=[Recipient("USER", uuid4())])


async def test_update_changes_only_what_is_passed(db_session: AsyncSession) -> None:
    task, user = await _task(db_session), await _user(db_session)
    target = await _user(db_session)
    schedule_id = await _schedule(db_session, task, user, recipients=[Recipient("USER", target)])

    row = await db.notify.update_schedule(db_session, schedule_id, time_of_day=time(18, 30))
    assert (row.time_of_day, row.is_active, row.recipients) == (
        time(18, 30),
        True,
        (Recipient("USER", target),),
    )

    row = await db.notify.update_schedule(db_session, schedule_id, is_active=False)
    assert (row.time_of_day, row.is_active) == (time(18, 30), False)


async def test_update_replaces_the_whole_recipient_list(db_session: AsyncSession) -> None:
    task, user = await _task(db_session), await _user(db_session)
    first, second = await _user(db_session), await _user(db_session)
    schedule_id = await _schedule(db_session, task, user, recipients=[Recipient("USER", first)])

    row = await db.notify.update_schedule(
        db_session, schedule_id, recipients=[Recipient("USER", second)]
    )
    assert row.recipients == (Recipient("USER", second),)

    row = await db.notify.update_schedule(db_session, schedule_id, recipients=[])
    assert row.recipients == ()


async def test_updating_an_unknown_schedule_is_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(ReminderNotFound):
        await db.notify.update_schedule(db_session, uuid4(), is_active=False)


async def test_due_schedules_are_active_daily_ones_whose_time_has_passed(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    early, late, off = [await _task(db_session) for _ in range(3)]
    early_id = await _schedule(db_session, early, user, time(8, 0))
    await _schedule(db_session, late, user, time(20, 0))
    off_id = await _schedule(db_session, off, user, time(8, 0))
    await db.notify.deactivate_schedule(db_session, off_id)

    due = await db.notify.due_schedules(db_session, time(12, 0))

    assert [(d.schedule_id, d.task_id) for d in due] == [(early_id, early)]


async def test_only_sent_rows_count_as_already_reminded(db_session: AsyncSession) -> None:
    task = await _task(db_session)
    sent_user, failed_user, yesterday_user = [await _user(db_session) for _ in range(3)]
    midnight = datetime(2026, 9, 7, 0, 0)

    await db.notify.record_reminders(
        db_session,
        task_id=task,
        user_ids=[sent_user],
        status="sent",
        sent_at=datetime(2026, 9, 7, 9, 0),
    )
    await db.notify.record_reminders(
        db_session,
        task_id=task,
        user_ids=[failed_user],
        status="failed",
        sent_at=datetime(2026, 9, 7, 9, 0),
    )
    await db.notify.record_reminders(
        db_session,
        task_id=task,
        user_ids=[yesterday_user],
        status="sent",
        sent_at=datetime(2026, 9, 6, 23, 0),
    )

    done = await db.notify.already_reminded_since(db_session, task, midnight)

    assert done == {sent_user}


async def test_recording_nobody_writes_nothing(db_session: AsyncSession) -> None:
    task = await _task(db_session)

    await db.notify.record_reminders(
        db_session, task_id=task, user_ids=[], status="sent", sent_at=datetime(2026, 9, 7, 9, 0)
    )

    assert await db.notify.already_reminded_since(db_session, task, datetime(2026, 9, 7)) == set()
