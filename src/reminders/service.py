"""Reminder schedules: what the web-backend manages over HTTP, and which
schedules are due when the job runs. The SQL is in src/db/notify.py (and the
task title in src/db/tasks.py); this file only decides what goes together.
"""

from dataclasses import dataclass
from datetime import datetime, time
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
    briefs = await db.tasks.task_briefs(session, [d.task_id for d in due])
    return [
        DueReminder(
            d.schedule_id, d.task_id, briefs[d.task_id].title if d.task_id in briefs else None
        )
        for d in due
    ]


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


async def users_owing_task(
    session: Session,
    task_id: UUID,
    now: datetime,
    schedule_id: UUID | None = None,
) -> list[UUID]:
    """LINE-reachable users who have not answered this task, while the task is
    inside its open/close window.

    With a `schedule_id`, also narrowed to the people that schedule is aimed
    at: anyone holding a targeted role, plus any individually targeted user. A
    schedule with no recipient rules -- every schedule created before
    recipients existed -- is aimed at everyone, so this behaves exactly as it
    always did for those.

    US4-4: a task that has assignees is only for those people (a task with
    none is for everyone, as before), so only they can owe it -- a reminder
    must never name a task the person cannot see. Same rule as the task list.

    Scoped to people with a linked LINE account on purpose: a LINE push is the
    only delivery channel in the MVP (ADR 0006), so users with no linked
    account are simply out of scope for a reminder -- not an error to report.
    """
    window = await db.tasks.task_window(session, task_id)
    if window is None:
        return []
    if (window.open_at is not None and window.open_at > now) or (
        window.close_at is not None and window.close_at < now
    ):
        return []

    candidates = set(await db.auth.line_linked_user_ids(session))
    candidates -= await db.tasks.responder_ids(session, task_id)

    assignees = await db.tasks.assignee_ids(session, task_id)
    if assignees:
        candidates &= set(assignees)

    if schedule_id is not None:
        rules = await db.notify.recipients_of(session, schedule_id)
        if rules:
            targeted = {r.id for r in rules if r.type == "USER"}
            targeted |= await db.auth.user_ids_with_any_role(
                session, [r.id for r in rules if r.type == "ROLE"]
            )
            candidates &= targeted
    return list(candidates)
