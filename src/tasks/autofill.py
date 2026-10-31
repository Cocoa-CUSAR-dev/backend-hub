"""Use last time's answers: the prefill offered when a form is opened.

Ported from mobile-backend's GetTaskAutofill (GET /tasks/:taskId/autofill),
GetLastAnswer (GET /service/tasks/last-answer) and SanitizeAutofill
(POST /service/autofill/sanitize). The mobile app calls `autofill`; the
chatbot will call `last_answer` and `sanitize` in-process instead of Go
(docs-and-plan#259). Both end in the same two steps, finding the last
COMPLETED answer and src/forms/autofill.sanitize, so the two channels offer
the same thing (US2-5).

No offer (None, a 204 on the route) whenever the chatbot wouldn't offer:
- a child handler's form (its answer needs a parent row picked first)
- a single-submit task the caller already answered (opening it is an edit)
- no earlier COMPLETED answer of this handler, from any task or channel
- nothing left after sanitizing

Permission: read:response:own (it reads the caller's own answers). Go
accepted any signed-in user.
"""

from typing import Any
from uuid import UUID

from src import db
from src.actor import Actor, require
from src.database import Session
from src.exceptions import Forbidden, NotFound
from src.forms import registry
from src.forms.autofill import OfferedQuestion, offered_questions
from src.forms.autofill import sanitize as sanitize_answer
from src.forms.detail import load_form
from src.tasks.schemas import AutofillOut, LastAnswerOut

_NO_FORM = "ไม่พบแบบฟอร์มสำหรับงานที่ระบุ"


def _caller(actor: Actor) -> UUID:
    if actor.user_id is None:
        raise Forbidden("A user is required")
    return actor.user_id


async def _last(session: Session, user_id: UUID, handler: str) -> LastAnswerOut | None:
    forms = await db.forms.forms_with_handlers(session, [handler])
    found = await db.tasks.last_completed_answer(session, user_id, [f.task_id for f in forms])
    if found is None:
        return None
    return LastAnswerOut(handler=handler, submitted_at=found.submitted_at, answer=found.answer)


async def last_answer(session: Session, actor: Actor, handler: str) -> LastAnswerOut | None:
    """The caller's most recent COMPLETED answer of this handler, unsanitized,
    or None. For the chatbot, which offers it one question at a time."""
    require(actor, "read:response:own")
    return await _last(session, _caller(actor), handler)


async def sanitize(
    session: Session, answer: dict[str, Any], questions: list[OfferedQuestion]
) -> dict[str, Any]:
    """`answer` cut down to what is safe to offer on a form with `questions`.
    Parent-id fields come from the handler registry."""
    parent_fields = (await registry.current(session)).parent_fields
    return sanitize_answer(answer, questions, parent_fields)


async def autofill(session: Session, actor: Actor, task_id: UUID) -> AutofillOut | None:
    """The sanitized prefill for a new answer to this task, or None for no offer.

    A task the caller can't see (or with no form) is NotFound, the same rule
    as the task form.
    """
    require(actor, "read:response:own")
    user_id = _caller(actor)
    task_form = None
    if await db.tasks.task_visible_to(session, user_id, task_id):
        task_form = await db.forms.form_of_task(session, task_id)
    if task_form is None:
        raise NotFound(_NO_FORM)

    handler = task_form.handler
    snapshot = await registry.current(session)
    if not handler or handler in snapshot.parents:
        return None
    if not task_form.is_multiple_submit and (
        await db.tasks.latest_response_id(session, user_id, task_id) is not None
    ):
        return None
    last = await _last(session, user_id, handler)
    if last is None:
        return None
    # The form as it is now, not as it was when the last answer was given.
    form = await load_form(session, task_form.form_id)
    if form is None:
        raise NotFound(_NO_FORM)
    offer = sanitize_answer(last.answer, offered_questions(form), snapshot.parent_fields)
    if not offer:
        return None  # an offer of nothing is worse than no offer
    return AutofillOut(submitted_at=last.submitted_at, answer=offer)
