"""Submitting and editing an answer to a task.

Ported from mobile-backend's submitAnswerForUser (POST /tasks and the
chatbot's POST /service/tasks) and UpdateTaskResponse (PUT /tasks). One
function per action for every door: the mobile app's routes now, the
chatbot once it calls these in-process instead of Go (docs-and-plan#259).

Order, as in Go: find the task's form, refuse a form type that can't be
saved yet, validate the whole answer (nothing is written until it passes),
then save the response and write its handler-table row in one transaction.

Permissions (database V35): create:response:own to submit, update:response:own
to edit one of your own answers. Go accepted any signed-in user.
"""

from typing import Any
from uuid import UUID, uuid4

from src import clock, db
from src.actor import Actor, require
from src.database import Session, transaction
from src.exceptions import AnswerRejectedByDatabase, Forbidden, Invalid, NotFound
from src.forms.detail import load_form
from src.forms.schemas import FormOut
from src.forms.validation import answer_errors
from src.tasks import dissect
from src.tasks.schemas import AnswerSavedOut

SUBMITTED = "ส่งงานเรียบร้อยแล้ว"
EDITED = "แก้ไขข้อมูลเรียบร้อยแล้ว"
_NO_FORM = "ไม่พบแบบฟอร์มสำหรับงานที่ระบุ"
_NO_RESPONSE = "ไม่พบข้อมูลที่ต้องการแก้ไข"


def _caller(actor: Actor) -> UUID:
    if actor.user_id is None:
        raise Forbidden("A user is required")
    return actor.user_id


async def _form_for(session: Session, user_id: UUID, task_id: UUID) -> tuple[UUID, str, FormOut]:
    """(form_id, handler, form) of a task the caller may see; NotFound
    otherwise, the same rule as the task list and the task form."""
    task_form = None
    if await db.tasks.task_visible_to(session, user_id, task_id):
        task_form = await db.forms.form_of_task(session, task_id)
    form = await load_form(session, task_form.form_id) if task_form else None
    if task_form is None or form is None or task_form.handler is None:
        raise NotFound(_NO_FORM)
    return task_form.form_id, task_form.handler, form


async def _check(session: Session, form: FormOut, answer: dict[str, Any]) -> None:
    """Every field problem at once: the form's own checks, then whether each
    dependent choice belongs to its parent (a plot of the chosen farm)."""
    errors = answer_errors(form, answer)
    for field, message in (await dissect.dependency_errors(session, answer)).items():
        errors.setdefault(field, message)
    if errors:
        raise Invalid("ข้อมูลไม่ผ่านการตรวจสอบ", fields=errors)


def _labelled(error: AnswerRejectedByDatabase, form: FormOut) -> Invalid:
    """Name the field the way the farmer saw it: "'น้ำหนัก (กก.)' ..."."""
    labels = {
        q.field_name: q.label
        for section in form.sections
        for q in section.questions
        if q.field_name and q.label
    }
    message = AnswerRejectedByDatabase.MESSAGES[error.reason]
    label = labels.get(error.column or "")
    if label:
        message = f"'{label}' {message}"
    return Invalid("ข้อมูลไม่ผ่านการตรวจสอบ", fields={error.column or "answer": message})


async def submit(
    session: Session, actor: Actor, task_id: UUID, answer: dict[str, Any]
) -> AnswerSavedOut:
    """Save the caller's answer to a task and write it into its handler table.

    A multi-submit task takes as many answers as the farmer files; a
    single-submit one takes a new answer each time too, as in Go (the app
    edits instead once it sees COMPLETED).
    """
    require(actor, "create:response:own")
    user_id = _caller(actor)
    form_id, handler, form = await _form_for(session, user_id, task_id)
    await dissect.handler_table(session, handler)  # 501 before anything is written
    await _check(session, form, answer)
    # Go stored the task id inside the answer too; readers of old rows rely on it.
    stored = {**answer, "task_id": str(task_id)}
    response_id = uuid4()
    try:
        async with transaction(session):
            await db.tasks.insert_response(
                session,
                response_id=response_id,
                task_id=task_id,
                task_form_id=form_id,
                user_id=user_id,
                answer=stored,
                submitted_at=clock.utc_now_naive(),
                status="COMPLETED",
            )
            await dissect.dissect(session, handler, stored)
    except AnswerRejectedByDatabase as error:
        raise _labelled(error, form) from error
    return AnswerSavedOut(message=SUBMITTED, response_id=response_id)


async def edit(
    session: Session,
    actor: Actor,
    task_id: UUID,
    answer: dict[str, Any],
    response_id: UUID | None = None,
) -> AnswerSavedOut:
    """Rewrite one of the caller's answers to a task: `response_id`, or the
    most recent one when None (Go's PUT /tasks without an id).

    As in Go, only the saved answer changes; the row written into the
    handler table at submit time is left as it was.
    """
    require(actor, "update:response:own")
    user_id = _caller(actor)
    _, _, form = await _form_for(session, user_id, task_id)
    await _check(session, form, answer)
    target = response_id or await db.tasks.latest_response_id(session, user_id, task_id)
    if target is None:
        raise NotFound(_NO_RESPONSE)
    async with transaction(session):
        updated = await db.tasks.update_response(
            session,
            response_id=target,
            task_id=task_id,
            user_id=user_id,
            answer={**answer, "task_id": str(task_id)},
            submitted_at=clock.utc_now_naive(),
        )
        if not updated:
            raise NotFound(_NO_RESPONSE)
    return AnswerSavedOut(message=EDITED, response_id=target)
