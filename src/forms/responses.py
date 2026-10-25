"""A researcher's view of a form's answers (FORM-12, FORM-13, FORM-14), ported
from web-backend's FormResponseController and ResponseReviewController:

- answers_by_question  GET /forms/{id}/responses: one row per question, every
                       person's answer to it (paged by response)
- review               GET /forms/{id}/responses?view=review: one row per
                       submission, field by field, newest first, with where
                       the chatbot got each value
- one_response         GET /forms/{id}/responses/{rid}
- correct              PATCH .../responses/{rid}/fields/{field}: fix one field;
                       the answer and its audit row are written together

`{id}` is a form id (docs-and-plan#271); answers are found through its task.
Only the answer JSON changes on a correction: the handler-table row written
from the same submission is not touched, as before.
"""

from typing import Any
from uuid import UUID

from src import clock, db
from src.actor import Actor, require
from src.database import Session, transaction
from src.db.forms import FormQuestion, TaskForm
from src.db.tasks import TaskResponse
from src.exceptions import Forbidden, FormNotFound, Invalid, ResponseNotFound
from src.forms import coercion
from src.forms.schemas import (
    AnswerOut,
    CorrectionIn,
    QuestionAnswersOut,
    ResponseOut,
    ReviewFieldOut,
    SubmissionOut,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def _page(page: int, size: int) -> tuple[int, int]:
    errors = {}
    if page < 0:
        errors["page"] = "page must be 0 or more"
    if not 1 <= size <= MAX_PAGE_SIZE:
        errors["size"] = f"size must be between 1 and {MAX_PAGE_SIZE}"
    if errors:
        raise Invalid(fields=errors)
    return size, page * size


async def _form(session: Session, form_id: UUID) -> TaskForm:
    form = await db.forms.form_by_id(session, form_id)
    if form is None:
        raise FormNotFound()
    return form


async def _names(session: Session, user_ids: list[UUID], *, username_too: bool) -> dict[UUID, str]:
    """Who sent each answer: their farmer name, else their processor name
    (else their username, for the review table), as Kotlin joined them."""
    names = await db.processing.processor_names(session, user_ids)
    names |= await db.agriculture.farmer_names(session, user_ids)  # a farmer's name first
    if username_too:
        usernames = await db.auth.usernames(session, user_ids)
        names = usernames | names
    return names


# ---- one row per question -----------------------------------------------------------


async def answers_by_question(
    session: Session, actor: Actor, form_id: UUID, *, page: int = 0, size: int = DEFAULT_PAGE_SIZE
) -> list[QuestionAnswersOut]:
    require(actor, "read:response:all")
    form = await _form(session, form_id)
    limit, offset = _page(page, size)
    responses = await db.tasks.responses_of_task(session, form.task_id, limit=limit, offset=offset)
    if not responses:
        return []
    names = await _names(session, list({r.user_id for r in responses}), username_too=False)
    # One row per field, in question order; a field asked twice keeps its
    # first place and its last label, as Kotlin's map did.
    labels: dict[str | None, str] = {}
    for q in await db.forms.form_questions(session, form_id):
        labels[q.field_name] = q.label or q.field_name or ""
    return [
        QuestionAnswersOut(
            question_title=label,
            answers=[
                AnswerOut(full_name=names[r.user_id], answer=coercion.as_text(r.answer[field]))
                for r in responses
                if r.user_id in names
                and field is not None
                and isinstance(r.answer, dict)
                and r.answer.get(field) is not None
            ],
        )
        for field, label in labels.items()
    ]


# ---- the review table ---------------------------------------------------------------


def _review_questions(questions: list[FormQuestion]) -> list[FormQuestion]:
    """Each asked field once, in question order, label and type defaulted."""
    seen: dict[str, FormQuestion] = {}
    for q in questions:
        if q.field_name and q.field_name not in seen:
            seen[q.field_name] = FormQuestion(
                q.question_id, q.field_name, q.label or q.field_name, q.input_type or "VARCHAR"
            )
    return list(seen.values())


async def _sources(
    session: Session, task_id: UUID, responses: list[TaskResponse], questions: list[FormQuestion]
) -> dict[UUID, dict[str, str]]:
    """response_id -> field_name -> how the chatbot got it. There's no key from
    an answer to the conversation that sent it, so: the person's finished
    conversation about this task that last changed closest to when the
    answer was sent. An answer from the mobile app has none."""
    conversations = await db.chat.completed_conversations(
        session, list({r.user_id for r in responses}), task_id
    )
    picked: dict[UUID, UUID] = {}
    for r in responses:
        mine = [c for c in conversations if c.user_id == r.user_id and c.updated_at is not None]
        if not mine or r.submitted_at is None:
            continue
        sent = r.submitted_at
        closest = min(mine, key=lambda c: abs(((c.updated_at or sent) - sent).total_seconds()))
        picked[r.response_id] = closest.conversation_id
    by_conversation = await db.chat.answer_sources(session, list(set(picked.values())))
    field_of = {q.question_id: q.field_name for q in questions if q.field_name}
    return {
        response_id: {
            field_of[question_id]: source
            for question_id, source in by_conversation.get(conversation_id, {}).items()
            if question_id in field_of
        }
        for response_id, conversation_id in picked.items()
    }


def _field(question: FormQuestion, stored: Any, source: str | None) -> ReviewFieldOut:
    return ReviewFieldOut(
        field_name=question.field_name or "",
        label=question.label or "",
        input_type=question.input_type or "VARCHAR",
        value=coercion.display(stored),
        source=source,
        editable=coercion.is_editable(question.input_type),
    )


async def review(
    session: Session,
    actor: Actor,
    form_id: UUID,
    *,
    ai_only: bool = False,
    page: int = 0,
    size: int = DEFAULT_PAGE_SIZE,
) -> list[SubmissionOut]:
    """Every submission, newest first, each question of the form in order,
    including the ones left empty (where an AI is likeliest to have missed
    something). `ai_only` keeps the people with at least one AI-extracted
    answer for this task."""
    require(actor, "read:response:all")
    form = await _form(session, form_id)
    limit, offset = _page(page, size)
    only = await db.chat.users_with_ai_answers(session, form.task_id) if ai_only else None
    responses = await db.tasks.responses_of_task(
        session, form.task_id, limit=limit, offset=offset, newest_first=True, only_users=only
    )
    if not responses:
        return []
    all_questions = await db.forms.form_questions(session, form_id)
    questions = _review_questions(all_questions)
    sources = await _sources(session, form.task_id, responses, all_questions)
    names = await _names(session, list({r.user_id for r in responses}), username_too=True)
    return [
        SubmissionOut(
            response_id=r.response_id,
            submitter=names.get(r.user_id, ""),
            submitted_at=r.submitted_at,
            fields=[
                _field(
                    q,
                    r.answer.get(q.field_name) if isinstance(r.answer, dict) else None,
                    sources.get(r.response_id, {}).get(q.field_name or ""),
                )
                for q in questions
            ],
        )
        for r in responses
    ]


# ---- one answer ---------------------------------------------------------------------


async def one_response(
    session: Session, actor: Actor, form_id: UUID, response_id: UUID
) -> ResponseOut:
    """Kotlin answered 404 for every one (it matched the form id against the
    task id, docs-and-plan#270); this finds the answer through the task."""
    require(actor, "read:response:all")
    form = await _form(session, form_id)
    found = await db.tasks.response_of_task(session, form.task_id, response_id)
    if found is None:
        raise ResponseNotFound()
    return ResponseOut(
        response_id=found.response_id,
        form_id=form_id,
        user_id=found.user_id,
        submitted_at=found.submitted_at,
        answer=found.answer,
        status=found.status,
    )


# ---- a correction -------------------------------------------------------------------


async def correct(
    session: Session,
    actor: Actor,
    form_id: UUID,
    response_id: UUID,
    field_name: str,
    body: CorrectionIn,
) -> ReviewFieldOut:
    """Fix one field of one answer and return it as the review table now shows
    it. The answer's row is locked while it is rewritten, and its audit row
    is written in the same transaction: a correction never lands without
    its record. Old and new values live in that table, not in the logs."""
    require(actor, "update:response:all")
    if actor.user_id is None:
        raise Forbidden("A user is required")
    form = await _form(session, form_id)
    all_questions = await db.forms.form_questions(session, form_id)
    question = next(
        (q for q in _review_questions(all_questions) if q.field_name == field_name), None
    )
    if question is None:
        message = f"Unknown field '{field_name}' for this task"
        raise Invalid(message, fields={"fieldName": message})
    if not coercion.is_editable(question.input_type):
        message = f"Fields of type {question.input_type} cannot be corrected here"
        raise Invalid(message, fields={"fieldName": message})

    async with transaction(session):
        found = await db.tasks.response_of_task(session, form.task_id, response_id, lock=True)
        if found is None:
            raise ResponseNotFound()
        if not isinstance(found.answer, dict):
            message = "This response has no answer data to correct"
            raise Invalid(message, fields={"value": message})
        old = found.answer.get(field_name)
        new = coercion.coerce(question.input_type or "VARCHAR", body.value, old)
        await db.tasks.set_answer(session, response_id, {**found.answer, field_name: new})
        await db.tasks.log_correction(
            session,
            response_id=response_id,
            field_name=field_name,
            old_value=coercion.display(old),
            new_value=coercion.display(new),
            corrected_by=actor.user_id,
            reason=body.reason if body.reason and body.reason.strip() else None,
            corrected_at=clock.utc_now_naive(),
        )
    sources = await _sources(session, form.task_id, [found], all_questions)
    return _field(question, new, sources.get(response_id, {}).get(field_name))
