"""Reminder schedules: what the web-backend manages over HTTP, and which
schedules are due when the job runs. The SQL is in src/db/notify.py (and the
task title in src/db/tasks.py); this file only decides what goes together.
"""

from dataclasses import dataclass
from datetime import time
from uuid import UUID

from src import db
from src.database import Session, transaction
from src.db.notify import Recipient, ScheduleRow


@dataclass(frozen=True)
class DueReminder:
    schedule_id: UUID
    task_id: UUID
    task_title: str | None


async def due_reminders(session: Session, now_time: time) -> list[DueReminder]:
    """Active DAILY schedules whose time has already passed today, with the
    title of the task each is about (for the message text)."""
    due = await db.notify.due_schedules(session, now_time)
    titles = await db.tasks.task_titles(session, [d.task_id for d in due])
    return [DueReminder(d.schedule_id, d.task_id, titles.get(d.task_id)) for d in due]


async def create_schedule(
    session: Session,
    *,
    task_id: UUID,
    time_of_day: time,
    created_by: UUID,
    recipients: list[Recipient],
) -> ScheduleRow:
    """Creates a DAILY schedule and returns what was actually saved: repeated
    recipient rules are collapsed, so the caller should trust this, not the
    request it sent.
    """
    async with transaction(session):
        schedule_id = await db.notify.create_schedule(
            session,
            task_id=task_id,
            time_of_day=time_of_day,
            created_by=created_by,
            recipients=recipients,
        )
    schedules = await db.notify.list_schedules_for_task(session, task_id)
    return next(s for s in schedules if s.schedule_id == schedule_id)


async def list_schedules(session: Session, task_id: UUID) -> list[ScheduleRow]:
    return await db.notify.list_schedules_for_task(session, task_id)


async def update_schedule(
    session: Session,
    schedule_id: UUID,
    *,
    time_of_day: time | None = None,
    is_active: bool | None = None,
    recipients: list[Recipient] | None = None,
) -> ScheduleRow:
    """Changes only what is passed; `recipients` replaces the whole list."""
    async with transaction(session):
        return await db.notify.update_schedule(
            session,
            schedule_id,
            time_of_day=time_of_day,
            is_active=is_active,
            recipients=recipients,
        )
