"""A form's reminder settings and the recipient pickers (FORM-08..11), ported
from web-backend's ReminderService. Kotlin asked the chatbot over HTTP for
the schedules; here they are read and written in-process through
reminders.service, with the same permission (create:form:all).

A form's task normally has at most one schedule (the create-form page makes
one); with several, the active one is the one shown and changed.
"""

from uuid import UUID

from src import db
from src.actor import Actor, require
from src.database import Session
from src.db.notify import Recipient, ScheduleRow
from src.exceptions import Forbidden, FormNotFound, Invalid
from src.reminders import service
from src.reminders.schemas import (
    FormReminderIn,
    FormReminderOut,
    RecipientDetailOut,
    RoleOptionOut,
    UserOptionOut,
)

MAX_RECIPIENTS = 200
USER_SEARCH_LIMIT = 50
_MANAGE = "create:form:all"


async def _task_of(session: Session, form_id: UUID) -> UUID:
    form = await db.forms.form_by_id(session, form_id)
    if form is None:
        raise FormNotFound()
    return form.task_id


def _pick(schedules: list[ScheduleRow]) -> ScheduleRow | None:
    return next((s for s in schedules if s.is_active), schedules[0] if schedules else None)


async def _out(session: Session, schedule: ScheduleRow | None) -> FormReminderOut:
    if schedule is None:
        return FormReminderOut(schedule_id=None, enabled=False, time_of_day=None, recipients=[])
    roles = await db.auth.role_names(
        session, [r.id for r in schedule.recipients if r.type == "ROLE"]
    )
    users = await db.auth.usernames(
        session, [r.id for r in schedule.recipients if r.type == "USER"]
    )
    return FormReminderOut(
        schedule_id=schedule.schedule_id,
        enabled=schedule.is_active,
        time_of_day=schedule.time_of_day,
        recipients=[
            RecipientDetailOut(
                type=r.type,
                id=r.id,
                label=(roles if r.type == "ROLE" else users).get(r.id) or str(r.id),
            )
            for r in schedule.recipients
        ],
    )


async def form_reminder(session: Session, actor: Actor, form_id: UUID) -> FormReminderOut:
    require(actor, _MANAGE)
    task_id = await _task_of(session, form_id)
    return await _out(session, _pick(await service.list_schedules(session, actor, task_id)))


async def save_form_reminder(
    session: Session, actor: Actor, form_id: UUID, body: FormReminderIn
) -> FormReminderOut:
    """Turn the reminder on (creating it the first time) with this time and
    these recipients, or off. Off keeps the saved time and recipients, so
    switching it back on restores them."""
    require(actor, _MANAGE)
    if actor.user_id is None:
        raise Forbidden("A user is required")
    if len(body.recipients) > MAX_RECIPIENTS:
        message = f"Too many recipients (max {MAX_RECIPIENTS})"
        raise Invalid(message, fields={"recipients": message})
    task_id = await _task_of(session, form_id)
    existing = _pick(await service.list_schedules(session, actor, task_id))
    recipients = [Recipient(type=r.type, id=r.id) for r in body.recipients]
    saved: ScheduleRow | None
    if body.enabled and existing is None:
        saved = await service.create_schedule(
            session,
            actor,
            task_id=task_id,
            time_of_day=body.time_of_day,
            created_by=actor.user_id,
            recipients=recipients,
        )
    elif body.enabled and existing is not None:
        saved = await service.update_schedule(
            session,
            actor,
            existing.schedule_id,
            time_of_day=body.time_of_day,
            is_active=True,
            recipients=recipients,
        )
    elif existing is not None:
        saved = await service.update_schedule(session, actor, existing.schedule_id, is_active=False)
    else:
        saved = None
    return await _out(session, saved)


async def role_options(session: Session, actor: Actor) -> list[RoleOptionOut]:
    require(actor, _MANAGE)
    return [
        RoleOptionOut(role_id=r.role_id, role_name=r.role_name)
        for r in await db.auth.role_options(session)
    ]


async def user_options(session: Session, actor: Actor, query: str) -> list[UserOptionOut]:
    """Up to 50 people whose username contains `query`, any case."""
    require(actor, _MANAGE)
    return [
        UserOptionOut(user_id=u.user_id, username=u.username, roles=u.roles)
        for u in await db.auth.user_options(session, query, limit=USER_SEARCH_LIMIT)
    ]
