"""US2-4: "offer reusing my last submission's answers."

The rules for what is safe to reuse live in src/forms/autofill.py and the
last answer comes from src/tasks/autofill.py, the same two the mobile app's
autofill uses, so the channels can't drift apart. This module adapts them to
the chatbot's questions and turns the result into real ConversationAnswer
rows (build_answer_rows).
"""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.auth.service import load_actor
from src.chat.constants import AnswerSource
from src.database import Session
from src.db.models import ConversationAnswer
from src.forms.autofill import OfferedQuestion
from src.tasks import autofill

if TYPE_CHECKING:
    from src.chat.service import Choice, Question

# service.py's own sentinel Choice ids (_SKIP_CHOICE_ID / _PAUSE_CHOICE_ID) --
# duplicated as bare strings rather than imported, since importing them at
# runtime (not just TYPE_CHECKING) would need service.py -> reuse.py ->
# service.py to resolve, and service.py is the one that will import
# start_conversation_with_autofill FROM this module. Question.choices always
# has pause prepended (and skip too, for non-mandatory) -- see service.py's
# _choices_for -- so these must be stripped before treating a Question's
# choices as "the real, storable values for this field." Purely a LINE/
# guided-flow UI concept, so it's stripped here -- a static-form caller would
# never have these ids in the first place.
_SENTINEL_CHOICE_IDS = frozenset({"__skip__", "__pause__"})


def _real_choices(question: "Question") -> list["Choice"]:
    if not question.has_constrained_choices or not question.choices:
        return []
    return [c for c in question.choices if c.id not in _SENTINEL_CHOICE_IDS]


async def last_answer(session: Session, user_id: UUID, handler: str) -> dict[str, Any] | None:
    """This farmer's most recent COMPLETED answer for `handler`, unfiltered, or
    None. Someone without read:response:own just gets no offer: reuse is a
    convenience and must never stop them starting the form.
    """
    actor = await load_actor(session, user_id, "line")
    if not actor.can("read:response:own"):
        return None
    found = await autofill.last_answer(session, actor, handler)
    return found.answer if found is not None else None


async def sanitize_for_autofill(
    session: Session, raw_answer: dict[str, Any], questions: list["Question"]
) -> dict[str, Any]:
    """`raw_answer` cut down to what is safe to offer on `questions`."""
    offered = [
        OfferedQuestion(
            field_name=q.field_name,
            input_type=q.input_type,
            choice_ids=tuple(c.id for c in _real_choices(q)),
        )
        for q in questions
    ]
    return await autofill.sanitize(session, raw_answer, offered)


def format_autofill_preview(sanitized_answer: dict[str, Any], questions: list["Question"]) -> str:
    """US2-4: "label: value" lines for the offer message itself -- a farmer
    should see what's actually being offered before deciding "ใช้ข้อมูลเดิม",
    not just an abstract "reuse old data?" prompt with no content shown
    until after they've already agreed. Same label/value resolution
    build_answer_rows uses (an OPTION/BOOLEAN value shown by its current
    label, not the raw id) -- sorted by sort_order, matching every other
    answer listing in this codebase (_format_answered_lines et al).
    """
    question_by_field = {q.field_name: q for q in questions}
    ordered_fields = sorted(
        (f for f in sanitized_answer if f in question_by_field),
        key=lambda f: question_by_field[f].sort_order,
    )
    lines = []
    for field_name in ordered_fields:
        question = question_by_field[field_name]
        value = sanitized_answer[field_name]
        if question.has_constrained_choices:
            display_value = next(
                (c.label for c in _real_choices(question) if c.id == str(value)), str(value)
            )
        else:
            display_value = str(value)
        lines.append(f"- {question.label}: {display_value}")
    return "\n".join(lines)


def build_answer_rows(
    conversation_id: UUID, sanitized_answer: dict[str, Any], questions: list["Question"]
) -> list[ConversationAnswer]:
    """Turns a sanitized reuse-answer dict into real ConversationAnswer rows
    -- the exact {"text": ..., "value": ...} shape handle_answer already
    writes for a freshly-typed answer, so confirm_conversation, the
    confirmation summary, and the edit flow all treat a prefilled field
    exactly like one the farmer just answered, no special-casing needed
    anywhere downstream of this function.

    source=GUIDED_FLOW, not a new "autofilled" value -- AnswerSource has no
    such value yet (would need its own migration), and the answer's actual
    provenance genuinely IS the guided flow, just prefilled rather than
    typed this turn. Worth reconsidering once #105 replaces this module.
    """
    question_by_field = {q.field_name: q for q in questions}
    rows = []
    for field_name, value in sanitized_answer.items():
        question = question_by_field.get(field_name)
        if question is None:
            continue  # a field the current form no longer asks
        answer: dict[str, Any] = {"text": str(value)}
        if question.has_constrained_choices:
            # OPTION/BOOLEAN -- value is already the resolved id (matched
            # against real choices in sanitize_for_autofill above); the
            # human-readable label is what a farmer actually reviews at
            # confirmation (_format_answered_lines reads answer["text"]).
            label = next(
                (c.label for c in _real_choices(question) if c.id == str(value)),
                str(value),
            )
            answer = {"text": label, "value": str(value)}
        rows.append(
            ConversationAnswer(
                conversation_id=conversation_id,
                question_id=question.question_id,
                answer=answer,
                source=AnswerSource.GUIDED_FLOW,
            )
        )
    return rows
