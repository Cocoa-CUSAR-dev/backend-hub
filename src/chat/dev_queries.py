"""All the database reads used only by the dev test router
(src/chat/dev_router.py), kept in one file so what is dev-only is easy to
see. None of the functions below are used by the real LINE webhook path.

The dev DB (chatbot/.env's DATABASE_URL) turned out to already have real-
shaped form/user data (84 forms, 35 users) -- not empty as first assumed, so
database/seed/mock_forms.sql's rows are NOT used here. Instead this filters
the picker down to the 4 "standalone" handlers (task-dissection-design.md)
and reuses the existing `farmer01` account (role=farmer) as the test
conversation owner.

The queries themselves are in src/db/; this file only picks which rows the
dev picker offers and shapes them like the real Kotlin responses.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from src import db
from src.chat.schemas import FormSummary
from src.database import Session
from src.exceptions import ConversationNotFound, FormNotFound
from src.forms.client import _convert_keys
from src.forms.schemas import FormDetail

logger = logging.getLogger(__name__)

TEST_USERNAME = "farmer01"  # existing farmer-role account in the dev DB

# The 4 "standalone" handlers (task-dissection-design.md) -- own generated
# PK, no parent-row dependency. Deliberately excludes the 5 "blocked"
# handlers (farm_activity_fertilizer, farm_activity_chemical,
# harvest_grade_detail, fermentation_batch, drying_batch, plus the 5th
# standalone `batch`), which need a parent-ID resolution that's still an
# open product question.
TESTABLE_HANDLERS = ("harvest", "farm_activity", "farm_pest_disease_record", "processing_record")

# Strict allowlist before this field_name gets interpolated into a table/
# column identifier below -- form.question.field_name is researcher-set
# (via Kotlin's form builder), not farmer-controlled, but not code either.
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*_id$")

_CHOICE_LIMIT = 13  # LINE's own Quick Reply cap -- see src/line/temp_task_picker.py


async def list_testable_forms(session: Session) -> list[FormSummary]:
    """Uses form.task.title, not form.task_form.title -- the task_form title
    is a generic template name shared by every recurring instance of a task
    (e.g. every "farm_activity" row says the same thing), while form.task's
    own title is the real per-instance one (e.g. "... (20/04/2026)"). Using
    the wrong one made the picker show a wall of identical-looking buttons.
    """
    forms = await db.forms.forms_with_handlers(session, TESTABLE_HANDLERS)
    briefs = await db.tasks.task_briefs(session, [f.task_id for f in forms])
    listed = [(f, briefs[f.task_id]) for f in forms if f.task_id in briefs]
    listed.sort(
        key=lambda p: (p[0].handler or "", p[1].open_at is None, p[1].open_at or datetime.min)
    )
    return [
        FormSummary(
            task_id=form.task_id,
            task_form_id=form.form_id,
            title=brief.title or "",
            handler=form.handler or "",
        )
        for form, brief in listed
    ]


async def _load_choices(session: Session, field_name: str) -> list[dict[str, str]]:
    """Mirrors Kotlin's real fetchRefChoices naming convention (confirmed by
    reading web-backend's FormRepository.kt directly, not guessed): strip
    "_id", append "_constant" for the table name; the id column is
    field_name itself; the label column is "<field_name minus _id>_name".

    Verified against every OPTION field this sprint's 4 forms actually use
    (farm_id, plot_id, farm_activity_type_id, pest_disease_id,
    processing_activity_type_id) -- all 5 follow this pattern for real,
    including a DB trigger keeping ref.farm_constant in sync with the real
    agriculture.farm table, so this isn't a guess-and-hope convention.
    """
    if not _FIELD_NAME_RE.match(field_name):
        logger.warning(
            "field_name %r doesn't match the expected '..._id' shape -- skipping choices",
            field_name,
        )
        return []

    base = field_name.removesuffix("_id")
    table = f"{base}_constant"
    name_col = f"{base}_name"

    choices = await db.ref.choices(session, table, field_name, name_col)
    by_name = sorted(choices, key=lambda c: c.name or "")
    return [{"id": c.id, "name": c.name} for c in by_name[:_CHOICE_LIMIT]]


async def load_form_detail(session: Session, task_form_id: UUID) -> FormDetail:
    """Direct-query counterpart to forms.client.get_form() -- builds the same
    FormDetail shape service.py expects, from the local `form.*` tables
    instead of a real Kotlin call. OPTION-type questions get a real
    `choices` list attached, same shape Kotlin's own response uses; every
    question gets `validation_rule` from the field validation rules, same
    LEFT JOIN on field_name web-backend's FormRepository.kt now does (null
    for OPTION/BOOLEAN/upload or any field_name with no rule row).
    """
    rows = await db.forms.form_rows(session, task_form_id)
    questions: list[dict[str, Any]] = [
        {
            "question_id": r.question_id,
            "label": r.label,
            "field_name": r.field_name,
            "input_type": r.input_type,
            "is_mandatory": r.is_mandatory,
            "sort_order": r.question_sort_order,
            "validation_rule": r.validation_rule,
        }
        for r in rows
        if r.question_id is not None
    ]
    questions.sort(key=lambda q: (q["sort_order"] is None, q["sort_order"] or 0))
    if not questions:
        raise FormNotFound(f"No questions found for task_form_id={task_form_id}")

    for question in questions:
        if question["input_type"] == "OPTION" and question.get("field_name"):
            question["choices"] = await _load_choices(session, question["field_name"])
        # SQLAlchemy's asyncpg dialect auto-decodes jsonb into a dict at the
        # connection level (confirmed directly against this app's own
        # async_session_maker -- NOT the same as a bare asyncpg.connect(),
        # which returns raw text; that difference is what made an earlier
        # version of this fix silently no-op). Handle both shapes so this
        # doesn't re-break if that decoding behavior ever changes.
        raw_rule = question.get("validation_rule")
        if raw_rule is not None:
            if isinstance(raw_rule, str):
                raw_rule = json.loads(raw_rule)
            # The jsonb column itself stores camelCase keys (errorMessage,
            # maxLength, ...) -- forms/client.py's get_form() normally does
            # this same camelCase -> snake_case conversion for the real
            # Kotlin response before service.py/validation.py ever see it.
            # This dev-only path reads the column directly, bypassing that
            # conversion entirely, so it has to apply it here too -- without
            # this, every _valid_* helper's rule.get("max_length") etc. would
            # silently miss (still spelled maxLength) and every answer would
            # pass unvalidated, exactly what happened testing this locally.
            question["validation_rule"] = _convert_keys(raw_rule)

    return FormDetail(task_form_id=str(task_form_id), sections=[{"questions": questions}])


async def resolve_test_user_id(session: Session) -> UUID:
    user = await db.auth.user_by_username(session, TEST_USERNAME)
    if user is None:
        raise ConversationNotFound(
            f"Seed user '{TEST_USERNAME}' not found -- run database/seed/mock_forms.sql first"
        )
    return user.user_id
