"""Tasks, who gets them, and the answers people submit.

Owns:
    form.task
    form.assignment
    form.task_recipient
    form.task_recurrence
    form.response
    form.response_correction_log
    form.diary_entry

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class TaskRow:
    task_id: UUID
    title: str | None
    description: str | None
    # Bangkok wall-clock, as the researcher typed it (no zone stored).
    open_at: datetime | None
    close_at: datetime | None
    # Whether this user has filed at least one response to the task.
    answered: bool


@dataclass(frozen=True, slots=True)
class TaskBrief:
    """A task's name and when it opens, for lists that only need those."""

    title: str | None
    open_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskWindow:
    """When a task accepts answers. None on either side means no limit there.
    Bangkok wall-clock, no zone stored."""

    open_at: datetime | None
    close_at: datetime | None


@dataclass(frozen=True, slots=True)
class ResponseRow:
    response_id: UUID
    task_form_id: UUID | None
    submitted_at: datetime | None
    status: str | None
    answer: dict[str, Any] | None


# Who may see a task: a task with assignment rows is only for those people;
# a task with none is for everyone (every task from before assignments
# existed). One copy, used by the task list and by the single-task reads.
_VISIBLE_TO_USER = """
    (
        NOT EXISTS (SELECT 1 FROM form.assignment a WHERE a.task_id = t.task_id)
        OR EXISTS (
            SELECT 1 FROM form.assignment a
            WHERE a.task_id = t.task_id AND a.user_id = :user_id
        )
    )
"""


async def tasks_for_user(
    session: AsyncSession,
    user_id: UUID,
    *,
    open_on: date | None,
    limit: int,
    offset: int,
) -> list[TaskRow]:
    """The tasks a user can see, newest first, and whether they answered each.

    Visibility: a task with assignment rows is only for those people; a task
    with none is for everyone (every task from before assignments existed).
    `open_on` keeps only tasks opening on that day.
    """
    result = await session.execute(
        text(
            f"""
            SELECT
                t.task_id,
                t.title,
                t.description,
                t.open_at,
                t.close_at,
                EXISTS (
                    SELECT 1 FROM form.response r
                    WHERE r.task_log_id = t.task_id AND r.user_id = :user_id
                ) AS answered
            FROM form.task t
            WHERE (CAST(:open_on AS date) IS NULL OR DATE(t.open_at) = :open_on)
              AND {_VISIBLE_TO_USER}
            ORDER BY t.open_at DESC, t.task_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {"user_id": user_id, "open_on": open_on, "limit": limit, "offset": offset},
    )
    return [TaskRow(**row._mapping) for row in result]


async def task_window(session: AsyncSession, task_id: UUID) -> TaskWindow | None:
    """None if the task does not exist."""
    row = (
        await session.execute(
            text("SELECT open_at, close_at FROM form.task WHERE task_id = :task_id"),
            {"task_id": task_id},
        )
    ).first()
    return TaskWindow(**row._mapping) if row else None


async def assignee_ids(session: AsyncSession, task_id: UUID) -> list[UUID]:
    """Who the task is assigned to. Empty means it is for everyone."""
    result = await session.execute(
        text("SELECT user_id FROM form.assignment WHERE task_id = :task_id"),
        {"task_id": task_id},
    )
    return list(result.scalars())


async def responder_ids(session: AsyncSession, task_id: UUID) -> set[UUID]:
    """Everyone who has filed at least one response to the task."""
    result = await session.execute(
        text("SELECT DISTINCT user_id FROM form.response WHERE task_log_id = :task_id"),
        {"task_id": task_id},
    )
    return set(result.scalars())


async def task_briefs(session: AsyncSession, task_ids: list[UUID]) -> dict[UUID, TaskBrief]:
    """task_id -> title and opening time, for the ids that exist."""
    if not task_ids:
        return {}
    rows = await session.execute(
        text("SELECT task_id, title, open_at FROM form.task WHERE task_id = ANY(:ids)"),
        {"ids": task_ids},
    )
    return {row.task_id: TaskBrief(title=row.title, open_at=row.open_at) for row in rows}


async def response_counts_by_form(session: AsyncSession) -> dict[UUID, int]:
    """form_id -> how many answers were saved with it. Every response carries
    task_form_id (database V25 backfilled the old ones)."""
    result = await session.execute(
        text(
            "SELECT task_form_id, count(*) AS n FROM form.response"
            " WHERE task_form_id IS NOT NULL GROUP BY task_form_id"
        )
    )
    return {row.task_form_id: row.n for row in result}


async def task_visible_to(session: AsyncSession, user_id: UUID, task_id: UUID) -> bool:
    """Whether the task exists and this user may see it (same rule as the
    task list)."""
    found = await session.execute(
        text(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM form.task t
                WHERE t.task_id = :task_id AND {_VISIBLE_TO_USER}
            )
            """
        ),
        {"user_id": user_id, "task_id": task_id},
    )
    return bool(found.scalar())


@dataclass(frozen=True, slots=True)
class TaskInfo:
    """A task as the researcher's task list shows it (FORM-01 ?include=stats)."""

    task_id: UUID
    title: str | None
    description: str | None
    task_type: str | None
    # Bangkok wall-clock, as the researcher typed it (no zone stored).
    open_at: datetime | None
    close_at: datetime | None


_TASK_INFO_COLUMNS = "task_id, title, description, task_type, open_at, close_at"


async def all_tasks(session: AsyncSession, *, limit: int, offset: int) -> list[TaskInfo]:
    """Every task, newest opening first. task_id breaks ties, so a page never
    repeats or skips a row (web-backend BE-9). Tasks with no open_at come first,
    as Postgres sorts NULLs in a descending order."""
    result = await session.execute(
        text(
            f"""
            SELECT {_TASK_INFO_COLUMNS} FROM form.task
            ORDER BY open_at DESC, task_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset},
    )
    return [TaskInfo(**row._mapping) for row in result]


async def task_info(session: AsyncSession, task_id: UUID) -> TaskInfo | None:
    row = (
        await session.execute(
            text(f"SELECT {_TASK_INFO_COLUMNS} FROM form.task WHERE task_id = :task_id"),
            {"task_id": task_id},
        )
    ).first()
    return TaskInfo(**row._mapping) if row else None


async def latest_answer(
    session: AsyncSession, user_id: UUID, task_id: UUID
) -> dict[str, Any] | None:
    """The user's most recent answer to a task, or None if they never answered."""
    row = (
        await session.execute(
            text(
                """
                SELECT answer FROM form.response
                WHERE task_log_id = :task_id AND user_id = :user_id
                ORDER BY submitted_at DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"task_id": task_id, "user_id": user_id},
        )
    ).first()
    if row is None:
        return None
    answer: dict[str, Any] = row.answer or {}
    return answer


async def responses_of_user(
    session: AsyncSession, user_id: UUID, task_id: UUID
) -> list[ResponseRow]:
    """Every response the user filed against a task, newest first."""
    result = await session.execute(
        text(
            """
            SELECT response_id, task_form_id, submitted_at, status, answer
            FROM form.response
            WHERE task_log_id = :task_id AND user_id = :user_id
            ORDER BY submitted_at DESC NULLS LAST
            """
        ),
        {"task_id": task_id, "user_id": user_id},
    )
    return [ResponseRow(**row._mapping) for row in result]


async def insert_response(
    session: AsyncSession,
    *,
    response_id: UUID,
    task_id: UUID,
    task_form_id: UUID,
    user_id: UUID,
    answer: dict[str, Any],
    submitted_at: datetime,
    status: str,
) -> None:
    """Save one submitted answer (task_log_id holds the task id, as it always has)."""
    await session.execute(
        text(
            """
            INSERT INTO form.response
                (response_id, task_log_id, task_form_id, user_id, answer, submitted_at, status)
            VALUES
                (:response_id, :task_id, :task_form_id, :user_id, CAST(:answer AS jsonb),
                 :submitted_at, :status)
            """
        ),
        {
            "response_id": response_id,
            "task_id": task_id,
            "task_form_id": task_form_id,
            "user_id": user_id,
            "answer": json.dumps(answer, ensure_ascii=False),
            "submitted_at": submitted_at,
            "status": status,
        },
    )


async def latest_response_id(session: AsyncSession, user_id: UUID, task_id: UUID) -> UUID | None:
    """The user's most recent response to a task, or None."""
    found = await session.execute(
        text(
            """
            SELECT response_id FROM form.response
            WHERE task_log_id = :task_id AND user_id = :user_id
            ORDER BY submitted_at DESC NULLS LAST
            LIMIT 1
            """
        ),
        {"task_id": task_id, "user_id": user_id},
    )
    response_id: UUID | None = found.scalar()
    return response_id


async def update_response(
    session: AsyncSession,
    *,
    response_id: UUID,
    task_id: UUID,
    user_id: UUID,
    answer: dict[str, Any],
    submitted_at: datetime,
) -> bool:
    """Rewrite one of the user's own responses to this task. False if no such
    response (wrong id, someone else's, or another task's): user_id and
    task_id are part of the match, so an id alone can't touch it."""
    updated = await session.execute(
        text(
            """
            UPDATE form.response
            SET answer = CAST(:answer AS jsonb), submitted_at = :submitted_at
            WHERE response_id = :response_id AND user_id = :user_id AND task_log_id = :task_id
            RETURNING response_id
            """
        ),
        {
            "response_id": response_id,
            "task_id": task_id,
            "user_id": user_id,
            "answer": json.dumps(answer, ensure_ascii=False),
            "submitted_at": submitted_at,
        },
    )
    return updated.first() is not None


# ---- a new task from the form builder (FORM-03) --------------------------------------

# Two bind parameters per row; Postgres allows 32767 per statement.
_ASSIGN_CHUNK = 5000


async def insert_task(
    session: AsyncSession,
    *,
    title: str,
    description: str,
    task_type: str,
    open_at: datetime,
    close_at: datetime,
) -> UUID:
    result = await session.execute(
        text(
            """
            INSERT INTO form.task (title, description, task_type, open_at, close_at)
            VALUES (:title, :description, :task_type, :open_at, :close_at)
            RETURNING task_id
            """
        ),
        {
            "title": title,
            "description": description,
            "task_type": task_type,
            "open_at": open_at,
            "close_at": close_at,
        },
    )
    task_id: UUID = result.scalar_one()
    return task_id


async def insert_recipients(
    session: AsyncSession, task_id: UUID, recipients: Sequence[tuple[str, UUID]]
) -> None:
    """Who the task is aimed at, as rules ("ROLE", role_id) or ("USER", user_id),
    not the people they resolve to. A repeated rule is saved once."""
    for kind, recipient_id in dict.fromkeys(recipients):
        await session.execute(
            text(
                """
                INSERT INTO form.task_recipient (task_id, recipient_type, role_id, user_id)
                VALUES (:task_id, :kind, :role_id, :user_id)
                """
            ),
            {
                "task_id": task_id,
                "kind": kind,
                "role_id": recipient_id if kind == "ROLE" else None,
                "user_id": recipient_id if kind == "USER" else None,
            },
        )


async def assign_users(session: AsyncSession, task_id: UUID, user_ids: Sequence[UUID]) -> None:
    """One assignment per person; someone already assigned is left alone."""
    people = list(dict.fromkeys(user_ids))
    for start in range(0, len(people), _ASSIGN_CHUNK):
        await session.execute(
            text(
                """
                INSERT INTO form.assignment (task_id, user_id)
                SELECT :task_id, unnest(CAST(:user_ids AS uuid[]))
                ON CONFLICT DO NOTHING
                """
            ),
            {"task_id": task_id, "user_ids": people[start : start + _ASSIGN_CHUNK]},
        )


async def insert_recurrence(
    session: AsyncSession,
    *,
    template_task_id: UUID,
    frequency: str,
    weekdays: Sequence[int] | None,
    start_date: date,
    until_date: date | None,
    created_by: UUID,
) -> UUID:
    """A repeating task whose first round is `template_task_id`, which is
    pointed back at it for its first day: that is what keeps the round job
    from copying it twice."""
    result = await session.execute(
        text(
            """
            INSERT INTO form.task_recurrence
                (template_task_id, frequency, weekdays, start_date, until_date, created_by)
            VALUES (:task_id, :frequency, CAST(:weekdays AS smallint[]), :start_date,
                    :until_date, :created_by)
            RETURNING recurrence_id
            """
        ),
        {
            "task_id": template_task_id,
            "frequency": frequency,
            "weekdays": list(weekdays) if weekdays is not None else None,
            "start_date": start_date,
            "until_date": until_date,
            "created_by": created_by,
        },
    )
    recurrence_id: UUID = result.scalar_one()
    await session.execute(
        text(
            "UPDATE form.task SET recurrence_id = :recurrence_id, period_date = :start_date"
            " WHERE task_id = :task_id"
        ),
        {"recurrence_id": recurrence_id, "start_date": start_date, "task_id": template_task_id},
    )
    return recurrence_id
