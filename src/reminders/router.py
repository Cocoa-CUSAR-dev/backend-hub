"""HTTP surface for managing notify.reminder_schedule rows and who they are
aimed at (notify.reminder_recipient) -- the write side of reminders that
isn't APScheduler-triggered (see scheduler.py/jobs.py for the delivery
side). The only caller is web-backend (create-form page + the per-task
reminder settings), never web-app directly.

Deliberately no DELETE: a schedule is switched off with PATCH is_active=false
(it also turns itself off once nobody owes the task, see jobs.py) so its
history in notify.reminder_log keeps a schedule to point at.

Gated by the same X-Service-Key trust model as src/notifications (reusing
its dependency directly rather than a second copy of the same check).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from src.database import Session, get_session
from src.db.notify import Recipient, ScheduleRow
from src.notifications.dependencies import require_service_key
from src.reminders import service
from src.reminders.schemas import (
    RecipientRule,
    ReminderScheduleCreate,
    ReminderScheduleResponse,
    ReminderScheduleUpdate,
)

router = APIRouter(
    prefix="/service/reminders",
    tags=["reminders"],
    dependencies=[Depends(require_service_key)],
)

SessionDep = Annotated[Session, Depends(get_session)]


def _to_recipients(rules: list[RecipientRule] | None) -> list[Recipient] | None:
    if rules is None:
        return None
    return [Recipient(type=r.type, id=r.id) for r in rules]


def _to_response(row: ScheduleRow) -> ReminderScheduleResponse:
    return ReminderScheduleResponse(
        schedule_id=row.schedule_id,
        task_id=row.task_id,
        cadence=row.cadence,
        time_of_day=row.time_of_day,
        is_active=row.is_active,
        created_by=row.created_by,
        recipients=[RecipientRule(type=r.type, id=r.id) for r in row.recipients],
    )


@router.post("", response_model=ReminderScheduleResponse)
async def create_reminder(
    body: ReminderScheduleCreate,
    session: SessionDep,
) -> ReminderScheduleResponse:
    row = await service.create_schedule(
        session,
        task_id=body.task_id,
        time_of_day=body.time_of_day,
        created_by=body.created_by,
        recipients=_to_recipients(body.recipients) or [],
    )
    return _to_response(row)


@router.get("", response_model=list[ReminderScheduleResponse])
async def list_reminders(task_id: UUID, session: SessionDep) -> list[ReminderScheduleResponse]:
    return [_to_response(row) for row in await service.list_schedules(session, task_id)]


@router.patch("/{schedule_id}", response_model=ReminderScheduleResponse)
async def update_reminder(
    schedule_id: UUID,
    body: ReminderScheduleUpdate,
    session: SessionDep,
) -> ReminderScheduleResponse:
    row = await service.update_schedule(
        session,
        schedule_id,
        time_of_day=body.time_of_day,
        is_active=body.is_active,
        recipients=_to_recipients(body.recipients),
    )
    return _to_response(row)
