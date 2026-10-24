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
