"""Reminder schedules, recipients and send log.

Owns:
    notify.reminder_schedule
    notify.reminder_recipient
    notify.reminder_log

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.

Nothing here commits: the caller (a service) wraps writes in `transaction()`.
"""

from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import InvalidReminderReference, ReminderNotFound


@dataclass(frozen=True, slots=True)
class Recipient:
    """Who a schedule is aimed at: every holder of a role (`type` "ROLE", `id`
    is a role_id) or exactly one person ("USER", `id` is a user_id).
    """

    type: Literal["ROLE", "USER"]
    id: UUID


@dataclass(frozen=True, slots=True)
class ScheduleRow:
    schedule_id: UUID
    task_id: UUID
    cadence: str
    time_of_day: time
    is_active: bool
    created_by: UUID
    recipients: tuple[Recipient, ...]


@dataclass(frozen=True, slots=True)
class DueSchedule:
    schedule_id: UUID
    task_id: UUID


# ---- schedules -------------------------------------------------------------


async def create_schedule(
    session: AsyncSession,
    *,
    task_id: UUID,
    time_of_day: time,
    created_by: UUID,
    recipients: list[Recipient],
) -> UUID:
    """Inserts a schedule and its recipient rules. cadence is hardcoded to
    'DAILY', not a parameter: `due_schedules` only recognizes that one value,
    and any other would be a row the job silently never picks up. Nothing
    calling this can pass a different cadence, because there is nowhere to
    pass one.

    Raises InvalidReminderReference if the task, creator, role or user does
    not exist.
    """
    schedule_id = uuid4()
    try:
        await session.execute(
            text(
                "INSERT INTO notify.reminder_schedule "
                "(schedule_id, task_id, cadence, time_of_day, is_active, created_by) "
                "VALUES (:schedule_id, :task_id, 'DAILY', :time_of_day, true, :created_by)"
            ),
            {
                "schedule_id": schedule_id,
                "task_id": task_id,
                "time_of_day": time_of_day,
                "created_by": created_by,
            },
        )
        await _insert_recipients(session, schedule_id, recipients)
    except IntegrityError as exc:
        raise InvalidReminderReference from exc
    return schedule_id


async def list_schedules_for_task(session: AsyncSession, task_id: UUID) -> list[ScheduleRow]:
    rows = await session.execute(
        text(
            "SELECT schedule_id, task_id, cadence, time_of_day, is_active, created_by "
            "FROM notify.reminder_schedule WHERE task_id = :task_id ORDER BY schedule_id"
        ),
        {"task_id": task_id},
    )
    found = [row._mapping for row in rows]
    recipients = await _recipients_for(session, [f["schedule_id"] for f in found])
    return [ScheduleRow(**f, recipients=tuple(recipients.get(f["schedule_id"], []))) for f in found]


async def update_schedule(
    session: AsyncSession,
    schedule_id: UUID,
    *,
    time_of_day: time | None = None,
    is_active: bool | None = None,
    recipients: list[Recipient] | None = None,
) -> ScheduleRow:
    """Changes only what is passed. `recipients` replaces the whole rule list
    (an empty list means everyone again).

    Raises ReminderNotFound for an unknown id, InvalidReminderReference if a
    role or user in `recipients` does not exist.
    """
    found = await session.execute(
        text("SELECT task_id FROM notify.reminder_schedule WHERE schedule_id = :id"),
        {"id": schedule_id},
    )
    task_id = found.scalar_one_or_none()
    if task_id is None:
        raise ReminderNotFound

    try:
        if time_of_day is not None:
            await session.execute(
                text(
                    "UPDATE notify.reminder_schedule SET time_of_day = :t WHERE schedule_id = :id"
                ),
                {"t": time_of_day, "id": schedule_id},
            )
        if is_active is not None:
            await session.execute(
                text("UPDATE notify.reminder_schedule SET is_active = :a WHERE schedule_id = :id"),
                {"a": is_active, "id": schedule_id},
            )
        if recipients is not None:
            await session.execute(
                text("DELETE FROM notify.reminder_recipient WHERE schedule_id = :id"),
                {"id": schedule_id},
            )
            await _insert_recipients(session, schedule_id, recipients)
    except IntegrityError as exc:
        raise InvalidReminderReference from exc

    schedules = await list_schedules_for_task(session, task_id)
    return next(s for s in schedules if s.schedule_id == schedule_id)


async def deactivate_schedule(session: AsyncSession, schedule_id: UUID) -> None:
    """Turns a schedule off -- used when nobody owes its task any more, so it
    is not checked again every day forever. Not a delete: the row, and its
    history in notify.reminder_log, stay; only future checks skip it.
    """
    await session.execute(
        text("UPDATE notify.reminder_schedule SET is_active = false WHERE schedule_id = :id"),
        {"id": schedule_id},
    )


async def due_schedules(session: AsyncSession, now_time: time) -> list[DueSchedule]:
    """Active schedules whose time_of_day has already passed today.

    DAILY only: notify.reminder_schedule has no day-of-week column to drive
    other cadences, so adding them is a schema change (database repo) first.

    "Already passed" plus a 15-minute check interval means a reminder fires on
    the first check at or after time_of_day; the caller drops anyone already in
    notify.reminder_log for today, so later checks the same day are no-ops.
    """
    rows = await session.execute(
        text(
            "SELECT schedule_id, task_id FROM notify.reminder_schedule "
            "WHERE is_active = true AND cadence = 'DAILY' AND time_of_day <= :now_time "
            "ORDER BY schedule_id"
        ),
        {"now_time": now_time},
    )
    return [DueSchedule(**row._mapping) for row in rows]


# ---- recipients ------------------------------------------------------------


def _dedupe(recipients: list[Recipient]) -> list[Recipient]:
    # notify.reminder_recipient is UNIQUE per (schedule, role) and
    # (schedule, user) -- collapse repeats here instead of failing on them.
    return list({(r.type, r.id): r for r in recipients}.values())


async def _insert_recipients(
    session: AsyncSession, schedule_id: UUID, recipients: list[Recipient]
) -> None:
    rows = [
        {
            "schedule_id": schedule_id,
            "type": r.type,
            "role_id": r.id if r.type == "ROLE" else None,
            "user_id": r.id if r.type == "USER" else None,
        }
        for r in _dedupe(recipients)
    ]
    if not rows:
        return
    await session.execute(
        text(
            "INSERT INTO notify.reminder_recipient "
            "(schedule_id, recipient_type, role_id, user_id) "
            "VALUES (:schedule_id, :type, :role_id, :user_id)"
        ),
        rows,
    )


async def _recipients_for(
    session: AsyncSession, schedule_ids: list[UUID]
) -> dict[UUID, list[Recipient]]:
    if not schedule_ids:
        return {}
    rows = await session.execute(
        text(
            "SELECT schedule_id, recipient_type, role_id, user_id "
            "FROM notify.reminder_recipient WHERE schedule_id = ANY(:ids) "
            "ORDER BY created_at, recipient_id"
        ),
        {"ids": schedule_ids},
    )
    grouped: dict[UUID, list[Recipient]] = {}
    for row in rows:
        target = row.role_id if row.recipient_type == "ROLE" else row.user_id
        grouped.setdefault(row.schedule_id, []).append(
            Recipient(type=row.recipient_type, id=target)
        )
    return grouped


async def recipients_of(session: AsyncSession, schedule_id: UUID) -> list[Recipient]:
    """The targeting rules of one schedule. Empty means everyone."""
    return (await _recipients_for(session, [schedule_id])).get(schedule_id, [])


# ---- send log --------------------------------------------------------------


async def already_reminded_since(
    session: AsyncSession, task_id: UUID, since: datetime
) -> set[UUID]:
    """The idempotency check, against the persisted notify.reminder_log and
    never APScheduler's in-memory state, so it survives a restart (ADR 0006).

    Compares `sent_at >= since` rather than `sent_at::date = today`: the caller
    writes sent_at and passes `since` in the same wall-clock frame (see
    record_reminders), so this holds whatever timezone the database session is
    in. The `::date` form broke near midnight Bangkok when the DB stored UTC:
    sent_at's date and "today" disagreed by one day and every run re-sent.

    Only counts status='sent'. A row logged 'failed' (LINE was down or rate
    limited for that recipient) means the push never reached them, and
    counting it would skip a real retry for the rest of the day.
    """
    rows = await session.execute(
        text(
            "SELECT user_id FROM notify.reminder_log "
            "WHERE task_id = :task_id AND channel = 'push' AND status = 'sent' "
            "AND sent_at >= :since"
        ),
        {"task_id": task_id, "since": since},
    )
    return {row.user_id for row in rows}


async def record_reminders(
    session: AsyncSession,
    *,
    task_id: UUID,
    user_ids: list[UUID],
    status: str,
    sent_at: datetime,
) -> None:
    """One notify.reminder_log row per user we attempted, written AFTER the
    push returns. This is what `already_reminded_since` reads back.

    sent_at is written explicitly (not left to the column's `now()` default)
    so it lands in the same frame `already_reminded_since` compares against.
    """
    if not user_ids:
        return
    await session.execute(
        text(
            "INSERT INTO notify.reminder_log (user_id, task_id, sent_at, channel, status) "
            "VALUES (:user_id, :task_id, :sent_at, 'push', :status)"
        ),
        [
            {"user_id": uid, "task_id": task_id, "sent_at": sent_at, "status": status}
            for uid in user_ids
        ],
    )
