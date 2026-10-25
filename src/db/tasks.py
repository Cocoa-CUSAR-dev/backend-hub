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


# ---- a researcher's view of a task's answers (FORM-12, FORM-13, FORM-14) ------------


@dataclass(frozen=True, slots=True)
class TaskResponse:
    """One answer sent to a task, with who sent it."""

    response_id: UUID
    task_form_id: UUID | None
    user_id: UUID
    submitted_at: datetime | None
    answer: Any
    status: str | None


_TASK_RESPONSE_COLUMNS = "response_id, task_form_id, user_id, submitted_at, answer, status"


async def responses_of_task(
    session: AsyncSession,
    task_id: UUID,
    *,
    limit: int,
    offset: int,
    newest_first: bool = False,
    only_users: Sequence[UUID] | None = None,
) -> list[TaskResponse]:
    """A page of the task's answers: by response id (the per-question view),
    or newest first, unsent ones last (the review table). `only_users`
    keeps those people's answers only."""
    order = "submitted_at DESC NULLS LAST, response_id" if newest_first else "response_id"
    users = "" if only_users is None else " AND user_id = ANY(:only_users)"
    result = await session.execute(
        text(
            f"SELECT {_TASK_RESPONSE_COLUMNS} FROM form.response"
            f" WHERE task_log_id = :task_id{users} ORDER BY {order} LIMIT :limit OFFSET :offset"
        ),
        {
            "task_id": task_id,
            "limit": limit,
            "offset": offset,
            "only_users": list(only_users or []),
        },
    )
    return [TaskResponse(**row._mapping) for row in result]


async def response_of_task(
    session: AsyncSession, task_id: UUID, response_id: UUID, *, lock: bool = False
) -> TaskResponse | None:
    """One answer of this task (None for another task's or no such answer).
    `lock` holds the row until the transaction ends, so two corrections of
    one answer can't overwrite each other with a stale copy."""
    result = await session.execute(
        text(
            f"SELECT {_TASK_RESPONSE_COLUMNS} FROM form.response"
            " WHERE response_id = :response_id AND task_log_id = :task_id"
            + (" FOR UPDATE" if lock else "")
        ),
        {"task_id": task_id, "response_id": response_id},
    )
    row = result.first()
    return TaskResponse(**row._mapping) if row else None


async def set_answer(session: AsyncSession, response_id: UUID, answer: dict[str, Any]) -> None:
    """Replace a response's answer JSON (a correction: submitted_at stays)."""
    await session.execute(
        text("UPDATE form.response SET answer = CAST(:answer AS jsonb) WHERE response_id = :r"),
        {"r": response_id, "answer": json.dumps(answer, ensure_ascii=False)},
    )


async def log_correction(
    session: AsyncSession,
    *,
    response_id: UUID,
    field_name: str,
    old_value: str | None,
    new_value: str | None,
    corrected_by: UUID,
    reason: str | None,
    corrected_at: datetime,
) -> None:
    """The audit row of one corrected field (docs-and-plan#173)."""
    await session.execute(
        text(
            """
            INSERT INTO form.response_correction_log
                (response_id, field_name, old_value, new_value, corrected_by, reason, corrected_at)
            VALUES (:response_id, :field_name, :old_value, :new_value, :corrected_by, :reason,
                    :corrected_at)
            """
        ),
        {
            "response_id": response_id,
            "field_name": field_name,
            "old_value": old_value,
            "new_value": new_value,
            "corrected_by": corrected_by,
            "reason": reason,
            "corrected_at": corrected_at,
        },
    )
