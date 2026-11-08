"""What is safe to prefill from someone's last answer: one sanitizer for every door.

Ported from mobile-backend's SanitizeAutofillAnswer (autofill_sanitizer.go).
The mobile app's GET /api/v1/tasks/{id}/autofill runs it, and the chatbot
will call the same function in-process instead of Go's
POST /service/autofill/sanitize (docs-and-plan#259), so both channels offer
the same thing (US2-5). Rules:

1. task_id and every parent-id field (the registry's parent_field: a
   farm_activity_id / harvest_id / batch_id) are always dropped. Reusing one
   would attach the new answer to an old parent row.
2. An OPTION or BOOLEAN value that is not one of the current form's choices
   is dropped: a farm or fertilizer deleted since then is not offered.
3. A GEODATA value is dropped: the question means "where are you now".
4. The "upload" field (a photo, stored as VARCHAR) is always dropped: last
   time's photo is not evidence of today's work.
5. Everything else passes through, including fields the current form no
   longer asks (as in Go; the caller decides what to show).

A pure function: the caller reads the form and the registry.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any

from src.forms.schemas import FormOut
from src.forms.validation import choice_key

_NOT_ANSWERS = frozenset({"task_id"})
_UPLOAD = "upload"
# The two ids a BOOLEAN question offers (the chatbot's _BOOLEAN_CHOICES).
_BOOLEAN_IDS = frozenset({"true", "false"})


@dataclass(frozen=True, slots=True)
class OfferedQuestion:
    """A question as the sanitizer needs it. The chatbot builds these from
    its own questions, leaving out its skip/pause choices."""

    field_name: str
    input_type: str | None
    choice_ids: tuple[str, ...] = ()


def offered_questions(form: FormOut) -> list[OfferedQuestion]:
    """The form's active questions (in active sections) that have a field name."""
    return [
        OfferedQuestion(
            field_name=question.field_name,
            input_type=question.input_type,
            choice_ids=tuple(choice.id for choice in question.choices or []),
        )
        for section in form.sections
        if section.is_active
        for question in section.questions
        if question.is_active and question.field_name
    ]


def sanitize(
    answer: dict[str, Any],
    questions: Iterable[OfferedQuestion],
    parent_fields: Collection[str],
) -> dict[str, Any]:
    """The part of `answer` that is safe to offer on the form `questions` describe."""
    choices: dict[str, frozenset[str]] = {}
    geodata: set[str] = set()
    for question in questions:
        kind = (question.input_type or "").upper()
        if kind == "OPTION":
            choices[question.field_name] = frozenset(choice_key(i) for i in question.choice_ids)
        elif kind == "BOOLEAN":
            choices[question.field_name] = _BOOLEAN_IDS
        elif kind == "GEODATA":
            geodata.add(question.field_name)

    kept: dict[str, Any] = {}
    for field, value in answer.items():
        if field in _NOT_ANSWERS or field in parent_fields:
            continue
        if field in geodata or field == _UPLOAD:
            continue
        if field in choices and choice_key(value) not in choices[field]:
            continue
        kept[field] = value
    return kept
