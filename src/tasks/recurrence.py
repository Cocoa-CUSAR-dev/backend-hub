"""Opens the next round of every repeating task (JOB-03, US4-4), ported from
web-backend's RecurrenceService and RecurrenceSchedule.

Triggered from outside (POST /internal/cron/recurrence, by cron-job.org once
a day just after midnight Bangkok time) and by the scheduler on a persistent
host, for the reason src/jobs/router.py explains. Safe to run any number of
times, and late: which rounds exist is decided by the database (one round per
recurrence and day), never by what an earlier run remembers.

A round is a copy of the first round (the template task): its form, sections,
questions and question visibility, its people, and its active reminders. Each
round is written in its own transaction, so one that fails is logged and
counted and the rest still open.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from src import clock, db
from src.actor import Actor
from src.database import Session, async_session_maker, transaction
from src.db.notify import ScheduleRow
from src.db.tasks import RunningRecurrence
from src.reminders import service as reminders

logger = logging.getLogger(__name__)

# How many days back a missed round (the job didn't run, or ran late) is still
# opened. Older ones are skipped: long over, and a bad week of downtime
# shouldn't open a pile of dead tasks.
LOOKBACK_DAYS = 6


@dataclass
class RunResult:
    created: int = 0  # new rounds opened
    already_existed: int = 0  # that day's round was there (a repeat run, or a race)
    skipped_closed: int = 0  # the round would be over by now
    skipped_no_recipients: int = 0  # it has recipients, but none is a person any more
    failed: int = 0  # couldn't be opened (logged)


def due_dates(recurrence: RunningRecurrence, today: date) -> list[date]:
    """The days a round should exist for as of `today`, oldest first: never
    the start day (the template IS that round), never after the end day,
    never the future, and not further back than LOOKBACK_DAYS. Weekdays are
    ISO (1 = Monday ... 7 = Sunday)."""
    first = max(recurrence.start_date + timedelta(days=1), today - timedelta(days=LOOKBACK_DAYS))
    last = min(recurrence.until_date, today) if recurrence.until_date else today
    days = []
    day = first
    while day <= last:
        if recurrence.frequency == "DAILY" or day.isoweekday() in recurrence.weekdays:
            days.append(day)
        day += timedelta(days=1)
    return days


def window(open_at: datetime, close_at: datetime, day: date) -> tuple[datetime, datetime]:
    """The round's open and close: the template's time of day and length (a
    task open Mon 08:00 to Wed 17:00 stays two days nine hours each round)."""
    opens = datetime.combine(day, open_at.time())
    return opens, opens + (close_at - open_at)


async def _people(session: Session, task_id: UUID) -> tuple[bool, list[UUID]]:
    """(has recipient rules, the people they name now)."""
    rules = await db.tasks.task_recipients(session, task_id)
    if not rules:
        return False, []
    roles = [i for kind, i in rules if kind == "ROLE"]
    users = [i for kind, i in rules if kind == "USER"]
    named = await db.auth.existing_user_ids(session, users)
    by_role = await db.auth.user_ids_with_any_role(session, roles)
    return True, list(dict.fromkeys([*by_role, *(u for u in users if u in named)]))


async def _copy_reminders(
    session: Session, schedules: list[ScheduleRow], task_id: UUID, recurrence: RunningRecurrence
) -> None:
    """Best effort, as with a form's own reminder: the round is open already,
    and a missing reminder must not undo it or stop the other rounds."""
    for schedule in schedules:
        try:
            await reminders.create_schedule(
                session,
                Actor.system(),
                task_id=task_id,
                time_of_day=schedule.time_of_day,
                created_by=recurrence.created_by,
                recipients=list(schedule.recipients),
            )
        except Exception:
            logger.exception(
                "round opened but a reminder was not copied",
                extra={"task_id": str(task_id), "recurrence_id": str(recurrence.recurrence_id)},
            )


async def _open_rounds(
    session: Session, recurrence: RunningRecurrence, now: datetime, result: RunResult
) -> None:
    if recurrence.template_open_at is None or recurrence.template_close_at is None:
        logger.error(
            "recurrence skipped: its template task has no open/close time",
            extra={"recurrence_id": str(recurrence.recurrence_id)},
        )
        result.failed += 1
        return
    days = due_dates(recurrence, now.date())
    if not days:
        return
    try:
        restricted, people = await _people(session, recurrence.template_task_id)
        if restricted and not people:
            # Opening it anyway would show it to everybody: the opposite of
            # what the recipients were for.
            logger.warning(
                "recurrence skipped: its recipients no longer match any user",
                extra={"recurrence_id": str(recurrence.recurrence_id)},
            )
            result.skipped_no_recipients += len(days)
            return
        existing = await db.tasks.existing_rounds(session, recurrence.recurrence_id, days)
    except Exception:
        logger.exception(
            "recurrence skipped: could not read its recipients or rounds",
            extra={"recurrence_id": str(recurrence.recurrence_id)},
        )
        await session.rollback()
        result.failed += 1
        return

    template_reminders: list[ScheduleRow] | None = None
    for day in days:
        if day in existing:
            result.already_existed += 1
            continue
        opens, closes = window(recurrence.template_open_at, recurrence.template_close_at, day)
        if closes <= now:
            result.skipped_closed += 1  # over before it could be opened
            continue
        try:
            async with transaction(session):
                task_id = await db.tasks.insert_round(
                    session,
                    template_task_id=recurrence.template_task_id,
                    recurrence_id=recurrence.recurrence_id,
                    period_date=day,
                    open_at=opens,
                    close_at=closes,
                )
                if task_id is not None:
                    await db.forms.copy_form(session, recurrence.template_task_id, task_id)
                    await db.tasks.assign_users(session, task_id, people)
        except Exception:
            logger.exception(
                "recurrence round failed",
                extra={"recurrence_id": str(recurrence.recurrence_id), "day": str(day)},
            )
            result.failed += 1
            continue
        if task_id is None:
            result.already_existed += 1  # another run made it between our check and insert
            continue
        result.created += 1
        logger.info(
            "recurrence round opened",
            extra={"recurrence_id": str(recurrence.recurrence_id), "day": str(day)},
        )
        if template_reminders is None:
            template_reminders = [
                s
                for s in await db.notify.list_schedules_for_task(
                    session, recurrence.template_task_id
                )
                if s.is_active
            ]
        await _copy_reminders(session, template_reminders, task_id, recurrence)


async def run(session: Session, now: datetime) -> RunResult:
    """One pass over every running recurrence. `now` is Bangkok wall-clock
    with no zone, as the task times are."""
    result = RunResult()
    running = await db.tasks.running_recurrences(
        session, now.date() - timedelta(days=LOOKBACK_DAYS)
    )
    for recurrence in running:
        await _open_rounds(session, recurrence, now, result)
    logger.info("recurrence run finished", extra={"result": str(result)})
    return result


async def open_due_rounds() -> RunResult:
    """The job body: the scheduler and /internal/cron/recurrence both call it."""
    async with async_session_maker() as session:
        return await run(session, clock.bangkok_now().replace(tzinfo=None))
