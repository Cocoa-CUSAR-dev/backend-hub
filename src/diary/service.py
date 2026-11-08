"""The farmer's own history and daily diary (ME-03, ME-04, ME-05), ported from
web-backend's HistoryController, DiaryController and DiaryService.

- submission_days  the days I submitted anything on, newest first, paged
- answers_on       what I answered on one day: the history page's fallback
                   for a day with no diary yet
- diary_on         that day's diary
- generate_for     (re)write today's diary from every answer of the day;
                   called after the chatbot's submit, in-process (Kotlin's
                   POST /service/diaries/generate is gone)

A day is the farmer's calendar day in Bangkok (submitted_at is UTC, and so
is the diary's updated_at here; Kotlin wrote its host's Bangkok time). The
diary is the template's bullet list polished by the LLM; the polish is kept
only when it passes the fact-guard, else the template is saved as-is. A
failed or slow LLM never fails the diary.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from src import clock, db
from src.actor import Actor, require
from src.config import settings
from src.database import Session, transaction
from src.diary import writing
from src.diary.schemas import AnswerFieldOut, DiaryOut
from src.exceptions import DiaryNotAvailable, Forbidden, Invalid, LLMUnavailable, NotFound
from src.forms import detail
from src.llm import client as llm

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
# Extra attempts when the model is busy (Kotlin retried a Gemini 503 twice).
LLM_RETRIES = 2


def _me(actor: Actor) -> UUID:
    if actor.user_id is None:
        raise Forbidden("A user is required")
    return actor.user_id


def _utc_range(day: date) -> tuple[datetime, datetime]:
    """The UTC moments a Bangkok day starts and ends at."""
    start = datetime.combine(day, time.min, tzinfo=clock.BANGKOK).astimezone(UTC)
    naive = start.replace(tzinfo=None)
    return naive, naive + timedelta(days=1)


async def submission_days(
    session: Session, actor: Actor, *, page: int = 0, size: int = DEFAULT_PAGE_SIZE
) -> list[date]:
    require(actor, "read:response:own")
    errors = {}
    if page < 0:
        errors["page"] = "page must be 0 or more"
    if not 1 <= size <= MAX_PAGE_SIZE:
        errors["size"] = f"size must be between 1 and {MAX_PAGE_SIZE}"
    if errors:
        raise Invalid(fields=errors)
    return await db.tasks.submission_days(session, _me(actor), limit=size, offset=page * size)


async def _answers(session: Session, user_id: UUID, day: date) -> list[AnswerFieldOut]:
    """Every answered field of the day, in the order the answers were sent
    and, within one, the form's question order. Blank answers, and keys no
    question of that form asks (task_id), are left out, as in Kotlin."""
    start, end = _utc_range(day)
    responses = await db.tasks.answers_between(session, user_id, start, end)
    form_ids = list({r.task_form_id for r in responses if r.task_form_id})
    questions = await db.forms.question_fields(session, form_ids)
    fields: list[AnswerFieldOut] = []
    for response in responses:
        if response.task_form_id is None or not response.answer:
            continue
        for (form_id, name), question in questions.items():
            if form_id != response.task_form_id or name not in response.answer:
                continue
            value = response.answer[name]
            text = _as_text(value)
            if not text:
                continue
            fields.append(
                AnswerFieldOut(
                    field_name=name,
                    label=question.label or name,
                    input_type=question.input_type or "",
                    raw_value=text,
                )
            )
    return fields


def _as_text(value: object) -> str:
    """An answer as Jackson's asText() wrote it: true/false, a number as
    typed, text as is; "" for null or a blank string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value if value.strip() else ""
    return str(value)


async def answers_on(session: Session, actor: Actor, day: date) -> list[AnswerFieldOut]:
    require(actor, "read:response:own")
    return await _answers(session, _me(actor), day)


async def diary_on(session: Session, actor: Actor, day: date) -> DiaryOut:
    """404 when no diary was written that day (the page then shows answers_on)."""
    require(actor, "read:response:own")
    entry = await db.tasks.diary_entry(session, _me(actor), day)
    if entry is None:
        raise NotFound(f"No diary entry for {day.isoformat()}")
    return DiaryOut.model_validate(entry, from_attributes=True)


async def _readable(session: Session, field: AnswerFieldOut) -> writing.Fact:
    value = field.raw_value
    if field.input_type == "BOOLEAN":
        value = writing.yes_no(value)
    elif field.input_type == "OPTION":
        # A choice deleted since it was answered keeps its id: an imperfect
        # but honest line beats losing the fact.
        value = await detail.ref_label(session, field.field_name, value) or value
    return writing.Fact(field_name=field.field_name, label=field.label, value=value)


async def generate_for(session: Session, actor: Actor, day: date | None = None) -> str:
    """Write the caller's diary for `day` (today in Bangkok by default) from
    every answer of that day, and return its text. DiaryNotAvailable when
    nothing was submitted that day."""
    require(actor, "create:response:own")
    user_id = _me(actor)
    day = day or clock.bangkok_today()
    fields = await _answers(session, user_id, day)
    if not fields:
        raise DiaryNotAvailable(f"No submissions on {day.isoformat()} to generate a diary from")
    facts = [await _readable(session, field) for field in fields]
    template = writing.render(facts)
    text = template
    try:
        polished = await llm.complete(
            writing.polish_prompt(template),
            timeout=settings.DIARY_LLM_TIMEOUT_SECONDS,
            retries=LLM_RETRIES,
        )
    except LLMUnavailable:
        logger.warning("diary polish unavailable -- using the template", extra={"day": str(day)})
    else:
        if writing.preserves_facts(facts, polished):
            text = polished
        else:
            logger.warning(
                "diary polish dropped or changed a fact -- using the template",
                extra={"day": str(day), "template": template, "polished": polished},
            )
    async with transaction(session):
        await db.tasks.save_diary_entry(session, user_id, day, text, clock.utc_now_naive())
    return text
