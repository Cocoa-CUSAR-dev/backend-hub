"""The one reminder query that still reaches into other schemas.

`users_owing_task` joins form, auth and notify tables in a single statement,
which CONVENTIONS.md §2 does not allow. It stays here, listed in
scripts/conventions_baseline.txt, until it is split into one query per owner
file (the tasks, auth and notify modules in src/db/) and composed in the service.
"""

import uuid
from datetime import datetime

from sqlalchemy import text

from src.database import Session


async def users_owing_task(
    session: Session,
    task_id: uuid.UUID,
    now: datetime,
    schedule_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """LINE-reachable users who have no form.response for this task, while the
    task is inside its open/close window.

    With a `schedule_id`, also narrowed to the people that schedule is aimed
    at (notify.reminder_recipient): anyone holding a targeted role, plus any
    individually targeted user. A schedule with no recipient rows -- every
    schedule created before recipients existed -- is aimed at everyone, so
    this behaves exactly as it always did for those.

    US4-4: a task that has form.assignment rows is only for those people (a
    task with none is for everyone, as before), so only they can owe it -- a
    reminder must never name a task the person cannot see. Same rule as the
    task list in temp_task_picker and mobile-backend.

    Scoped to auth.line_identity on purpose: a LINE push is the only delivery
    channel in the MVP (ADR 0006), so users with no linked LINE account are
    simply out of scope for a reminder -- not an error to report.
    """
    rows = await session.execute(
        text(
            """
            SELECT li.user_id
            FROM auth.line_identity li
            JOIN form.task t ON t.task_id = :task_id
            LEFT JOIN form.response r
              ON r.task_log_id = t.task_id AND r.user_id = li.user_id
            WHERE r.response_id IS NULL
              AND (t.open_at IS NULL OR t.open_at <= :now)
              AND (t.close_at IS NULL OR t.close_at >= :now)
              AND (
                NOT EXISTS (SELECT 1 FROM form.assignment a WHERE a.task_id = t.task_id)
                OR EXISTS (
                  SELECT 1 FROM form.assignment a
                  WHERE a.task_id = t.task_id AND a.user_id = li.user_id
                )
              )
              AND (
                CAST(:schedule_id AS uuid) IS NULL
                OR NOT EXISTS (
                  SELECT 1 FROM notify.reminder_recipient rr
                  WHERE rr.schedule_id = CAST(:schedule_id AS uuid)
                )
                OR EXISTS (
                  SELECT 1 FROM notify.reminder_recipient rr
                  WHERE rr.schedule_id = CAST(:schedule_id AS uuid)
                    AND (
                      rr.user_id = li.user_id
                      OR rr.role_id IN (
                        SELECT ur.role_id FROM auth.user_role ur
                        WHERE ur.user_id = li.user_id
                      )
                    )
                )
              )
            """
        ),
        {
            "task_id": str(task_id),
            "now": now,
            "schedule_id": str(schedule_id) if schedule_id else None,
        },
    )
    return [row.user_id for row in rows]
