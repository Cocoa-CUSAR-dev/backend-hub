"""The form builder: create, rewrite and switch parts of a form (FORM-03,
FORM-04, FORM-05). Ported from web-backend's FormService and FormRepository.

- create_form  POST /forms: a new task and its form, who it's for, its repeat
               rule and its daily reminder
- update_form  PUT /forms/{id}: rewrite every section and question; one with
               no id is new, one left out is deleted
- edit_form    PUT /forms/{id}/edit: only switch sections and questions on or
               off, and questions required or not

Each write runs in one transaction. Every saved form must pass
forms.definition (the handler's required columns asked, input types that fit
their columns, a dependent field after its parent) and have at least one
section with at least one question; every problem is reported at once (422).
The reminder is created after the form, in-process (Kotlin called the chatbot
over HTTP); if it fails, the form stays and the failure is logged, as before.
"""

import logging
from uuid import UUID

from src import db
from src.actor import Actor, require
from src.database import Session, transaction
from src.db.forms import HandlerRow, QuestionWrite
from src.db.notify import Recipient
from src.exceptions import Forbidden, FormNotFound, Invalid
from src.forms import definition, handlers, registry
from src.forms.detail import load_form
from src.forms.schemas import (
    FormCreateIn,
    FormEditIn,
    FormOut,
    FormUpdateIn,
    RecipientIn,
    SectionCreateIn,
    SectionUpdateIn,
)
from src.reminders import service as reminders

logger = logging.getLogger(__name__)

EDITED = "Successfully audited form"


def _me(actor: Actor) -> UUID:
    if actor.user_id is None:
        raise Forbidden("A user is required")
    return actor.user_id


def _sections_have_questions(sections: list[SectionCreateIn] | list[SectionUpdateIn]) -> None:
    """A form with no section is never listed, and a section with no question
    asks nothing: both are refused, so a saved form is usable end to end."""
    if not sections:
        raise Invalid("A form must have at least one section", fields={"sections": "Add a section"})
    for section in sections:
        if not section.questions:
            message = f"Section '{section.title}' must have at least one question"
            raise Invalid(message, fields={"sections": message})


async def _check_definition(
    session: Session, handler_name: str | None, questions: list[definition.QuestionSpec]
) -> None:
    reg = await registry.current(session)
    handler: HandlerRow | None = next((h for h in reg.handlers if h.handler == handler_name), None)
    if handler is None:
        raise Invalid(f"Unknown handler: {handler_name}", fields={"handler": "No such handler"})
    fields = await handlers.fields_of(session, reg, handler)
    found = definition.problems(handler, fields, reg.dependencies, questions)
    if found:
        message = "\n".join(found)
        raise Invalid(message, fields={"sections": message})


def _check_recurrence(body: FormCreateIn) -> None:
    """The task being created is round one, so its own dates must make sense."""
    recurrence = body.recurrence
    if recurrence is None:
        return
    errors: dict[str, str] = {}
    if body.close_at <= body.open_at:
        errors["closeAt"] = "A repeating task must close after it opens"
    if recurrence.frequency == "WEEKLY":
        weekdays = recurrence.weekdays or []
        if not weekdays:
            errors["recurrence"] = "A weekly task needs at least one weekday"
        elif not all(1 <= d <= 7 for d in weekdays):
            errors["recurrence"] = "Weekdays must be numbers from 1 (Monday) to 7 (Sunday)"
    if recurrence.until_date is not None and recurrence.until_date < body.open_at.date():
        errors["recurrence"] = "The end date must not be before the day the task first opens"
    if errors:
        raise Invalid(next(iter(errors.values())), fields=errors)


async def _assignees(session: Session, recipients: list[RecipientIn]) -> list[UUID]:
    """The people the recipients name. None at all = the task is for everyone,
    as it always was; recipients that name nobody are refused instead of
    silently publishing a task meant to be restricted."""
    if not recipients:
        return []
    roles = [r.id for r in recipients if r.type == "ROLE"]
    users = [r.id for r in recipients if r.type == "USER"]
    known_roles = await db.auth.existing_role_ids(session, roles)
    known_users = await db.auth.existing_user_ids(session, users)
    unknown = [
        f"{r.type.lower()} {r.id}"
        for r in recipients
        if r.id not in (known_roles if r.type == "ROLE" else known_users)
    ]
    if unknown:
        message = "Unknown recipient: " + ", ".join(unknown)
        raise Invalid(message, fields={"recipients": message})
    people = list(dict.fromkeys([*await db.auth.user_ids_with_any_role(session, roles), *users]))
    if not people:
        message = "The chosen recipients do not match any user"
        raise Invalid(message, fields={"recipients": message})
    return people


async def create_form(session: Session, actor: Actor, body: FormCreateIn) -> FormOut:
    require(actor, "create:form:all")
    me = _me(actor)
    errors = {}
    if not body.title.strip():
        errors["title"] = "Form title must not be blank"
    if not body.handler.strip():
        errors["handler"] = "Form handler must not be blank"
    if errors:
        raise Invalid(next(iter(errors.values())), fields=errors)
    _sections_have_questions(body.sections)
    await _check_definition(
        session,
        body.handler,
        [
            definition.QuestionSpec(s.sort_order, q.sort_order, q.label, q.input_type, q.field_name)
            for s in body.sections
            for q in s.questions
        ],
    )
    _check_recurrence(body)
    people = await _assignees(session, body.recipients)

    async with transaction(session):
        task_id = await db.tasks.insert_task(
            session,
            title=body.title,
            description=body.description or "",
            task_type=body.task_type,
            open_at=body.open_at,
            close_at=body.close_at,
        )
        form_id = await db.forms.insert_task_form(
            session,
            task_id=task_id,
            title=body.title,
            description=body.description,
            handler=body.handler,
            is_multiple_submit=body.is_multiple_submit,
        )
        for section in body.sections:
            section_id = await db.forms.insert_section(
                session,
                form_id=form_id,
                title=section.title,
                description=section.description,
                sort_order=section.sort_order,
            )
            for q in section.questions:
                await db.forms.insert_question(
                    session,
                    section_id,
                    QuestionWrite(
                        label=q.label,
                        description=q.description,
                        input_type=q.input_type,
                        field_name=q.field_name,
                        is_mandatory=q.is_mandatory,
                        is_active=True,
                        carry_forward=q.carry_forward,
                        sort_order=q.sort_order,
                        default_value=q.default_value,
                    ),
                )
        # Who the task is for, in the same transaction: it is never visible
        # to everyone for a moment before it is restricted.
        if body.recipients:
            await db.tasks.insert_recipients(
                session, task_id, [(r.type, r.id) for r in body.recipients]
            )
            await db.tasks.assign_users(session, task_id, people)
        if body.recurrence is not None:
            weekly = body.recurrence.frequency == "WEEKLY"
            await db.tasks.insert_recurrence(
                session,
                template_task_id=task_id,
                frequency=body.recurrence.frequency,
                weekdays=sorted(set(body.recurrence.weekdays or [])) if weekly else None,
                start_date=body.open_at.date(),
                until_date=body.recurrence.until_date,
                created_by=me,
            )

    if body.reminder is not None and body.reminder.enabled:
        try:
            await reminders.create_schedule(
                session,
                actor,
                task_id=task_id,
                time_of_day=body.reminder.time_of_day,
                created_by=me,
                recipients=[Recipient(type=r.type, id=r.id) for r in body.reminder.recipients],
            )
        except Exception:
            # The form is saved; a reminder can be set from its settings page.
            logger.exception("form created but its reminder could not be", extra={"form": form_id})

    created = await load_form(session, form_id)
    assert created is not None
    return created


async def update_form(session: Session, actor: Actor, form_id: UUID, body: FormUpdateIn) -> FormOut:
    require(actor, "update:form:all")
    form = await db.forms.form_by_id(session, form_id)
    if form is None:
        raise FormNotFound()
    _sections_have_questions(body.sections)
    # Only what stays active is asked, so only that can satisfy a column. The
    # handler can't change on update: it comes from the stored form.
    await _check_definition(
        session,
        form.handler,
        [
            definition.QuestionSpec(s.sort_order, q.sort_order, q.label, q.input_type, q.field_name)
            for s in body.sections
            if s.is_active
            for q in s.questions
            if q.is_active
        ],
    )

    async with transaction(session):
        await db.forms.update_task_form(
            session,
            form_id,
            description=body.description,
            is_multiple_submit=body.is_multiple_submit,
        )
        existing = await db.forms.section_ids_of(session, form_id)
        kept: list[UUID] = []
        for section in body.sections:
            if section.section_id is not None and section.section_id in existing:
                section_id = section.section_id
                await db.forms.update_section(
                    session,
                    section_id,
                    title=section.title,
                    description=section.description,
                    sort_order=section.sort_order,
                    is_active=section.is_active,
                )
            else:
                section_id = await db.forms.insert_section(
                    session,
                    form_id=form_id,
                    title=section.title,
                    description=section.description,
                    sort_order=section.sort_order,
                    is_active=section.is_active,
                )
            kept.append(section_id)

            questions = await db.forms.question_ids_of(session, section_id)
            kept_questions: list[UUID] = []
            for q in section.questions:
                write = QuestionWrite(
                    label=q.label,
                    description=q.description,
                    input_type=q.input_type,
                    field_name=q.field_name,
                    is_mandatory=q.is_mandatory,
                    is_active=q.is_active,
                    carry_forward=q.carry_forward,
                    sort_order=q.sort_order,
                    default_value=q.default_value,
                )
                if q.question_id is not None and q.question_id in questions:
                    await db.forms.update_question(session, q.question_id, write)
                    kept_questions.append(q.question_id)
                else:
                    kept_questions.append(
                        await db.forms.insert_question(session, section_id, write)
                    )
            await db.forms.delete_questions_except(session, section_id, kept_questions)
        await db.forms.delete_sections(session, [s for s in existing if s not in kept])

    updated = await load_form(session, form_id)
    assert updated is not None
    return updated


async def edit_form(session: Session, actor: Actor, form_id: UUID, body: FormEditIn) -> str:
    """Only a section or question of this form changes (Kotlin matched any id),
    and a flag left out keeps its value (Kotlin wrote null)."""
    require(actor, "update:form:all")
    if await db.forms.form_by_id(session, form_id) is None:
        raise FormNotFound()
    async with transaction(session):
        for section in body.sections:
            await db.forms.set_section_active(
                session, form_id, section.section_id, section.is_active
            )
            for q in section.questions:
                await db.forms.set_question_flags(
                    session,
                    form_id,
                    q.question_id,
                    is_active=q.is_active,
                    is_mandatory=q.is_mandatory,
                )
    return EDITED
