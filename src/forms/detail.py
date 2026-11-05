"""Build one form's full definition: sections, questions, choices and rules.

The one place every caller gets a form from -- the mobile app's task form
(tasks.service.get_form), the researcher form view and the chatbot
(`load_chat_form`) -- so the app, the web and LINE can't drift apart on what a
form looks like. Only reads; permission checks belong to the service that
calls `load_form`.

Ported from web-backend's FormRepository.fetchForm, choice rules included:
- An OPTION question's choices come from the ref table named after its
  field: plot_id -> plot_constant in the ref schema, id column plot_id,
  name column the first one starting with plot_name.
- grade_code stores the grade's name, so its choices use the name as the id.
- drying_facility_type_id's table is drying_facility_constant (no "_type").
- A dependent field (plot_id depends on farm_id) also gets each choice's
  parent id, so the client can filter by the parent answer.
- One field whose choices can't be loaded gets an empty list and an error
  log; the rest of the form still loads.
"""

import logging
import re
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from src import db
from src.database import Session
from src.db.forms import FieldDependencyRow, FormRow
from src.db.ref import Choice
from src.exceptions import FormNotFound
from src.forms import registry
from src.forms.schemas import ChoiceOut, FormDetail, FormOut, QuestionOut, SectionOut

logger = logging.getLogger(__name__)

OPTION = "OPTION"

# The one ref table that breaks the "<field minus _id>_constant" naming.
_TABLE_OVERRIDES = {"drying_facility_type_id": "drying_facility_constant"}

# field_name -> (table, id column, name column). Only changes by migration
# plus restart, so it is cached for the life of the process; the choice rows
# themselves are read fresh every time.
_choice_columns: dict[str, tuple[str, str, str]] = {}


def reset_cache() -> None:
    """Tests only: forget resolved ref tables."""
    _choice_columns.clear()


async def _choice_source(session: Session, field_name: str) -> tuple[str, str, str]:
    cached = _choice_columns.get(field_name)
    if cached:
        return cached
    stem = field_name.removesuffix("_id")
    table = _TABLE_OVERRIDES.get(field_name, f"{stem}_constant")
    columns = await db.ref.columns_of(session, table)
    if not columns:
        raise LookupError(f"no ref table {table} for field {field_name}")
    if field_name not in columns:
        raise LookupError(f"no id column {field_name} in ref table {table}")
    name_column = next((c for c in columns if c.startswith(f"{stem}_name")), None)
    if name_column is None:
        raise LookupError(f"no {stem}_name* column in ref table {table}")
    _choice_columns[field_name] = (table, field_name, name_column)
    return _choice_columns[field_name]


async def _choices_for(
    session: Session, field_name: str, dependency: FieldDependencyRow | None
) -> list[Choice]:
    if field_name == "grade_code":
        return await db.ref.grade_codes(session)
    table, id_column, name_column = await _choice_source(session, field_name)
    if dependency is None:
        return await db.ref.choices(session, table, id_column, name_column)
    return await db.ref.choices_with_parent(
        session,
        table,
        id_column,
        name_column,
        link_table=dependency.link_table,
        child_column=dependency.child_field,
        parent_column=dependency.link_column,
    )


async def ref_label(session: Session, field_name: str, value: str) -> str | None:
    """The name an OPTION answer stands for (a farm id -> its farm name), from
    the same ref table its choices come from; None when it can't be found.
    grade_code already stores the grade's name."""
    if field_name == "grade_code":
        return value
    try:
        table, id_column, name_column = await _choice_source(session, field_name)
    except LookupError:
        return None
    return await db.ref.choice_name(session, table, id_column, name_column, value)


async def _all_choices(
    session: Session, rows: Iterable[FormRow], dependencies: dict[str, FieldDependencyRow]
) -> dict[str, list[ChoiceOut]]:
    option_fields = dict.fromkeys(
        row.field_name for row in rows if row.input_type == OPTION and row.field_name
    )
    loaded: dict[str, list[ChoiceOut]] = {}
    for field_name in option_fields:
        try:
            found = await _choices_for(session, field_name, dependencies.get(field_name))
        except (LookupError, ValueError):
            logger.exception(
                "could not load choices for an OPTION field -- returning none",
                extra={"field_name": field_name},
            )
            found = []
        loaded[field_name] = [ChoiceOut(id=c.id, name=c.name, parent_id=c.parent_id) for c in found]
    return loaded


def _question(
    row: FormRow,
    choices: dict[str, list[ChoiceOut]],
    dependencies: dict[str, FieldDependencyRow],
) -> QuestionOut:
    assert row.question_id is not None and row.section_id is not None
    field_name = row.field_name
    dependency = dependencies.get(field_name) if field_name else None
    return QuestionOut(
        question_id=row.question_id,
        section_id=row.section_id,
        label=row.label,
        input_type=row.input_type,
        description=row.question_description,
        field_name=field_name,
        default_value=row.default_value,
        is_mandatory=bool(row.is_mandatory),
        is_active=row.question_is_active is not False,
        carry_forward=bool(row.carry_forward),
        sort_order=row.question_sort_order,
        choices=(choices.get(field_name, []) if field_name else [])
        if row.input_type == OPTION
        else None,
        validation_rule=row.validation_rule,
        depends_on=dependency.parent_field if dependency else None,
    )


async def load_form(session: Session, form_id: UUID) -> FormOut | None:
    """The whole form, or None if it does not exist. Sections and questions
    keep their sort order; inactive ones are included with is_active false."""
    rows = await db.forms.form_rows(session, form_id)
    if not rows:
        return None
    dependencies = await registry.dependencies(session)
    choices = await _all_choices(session, rows, dependencies)

    sections: dict[UUID, SectionOut] = {}
    for row in rows:
        if row.section_id is None:
            continue
        section = sections.get(row.section_id)
        if section is None:
            section = SectionOut(
                section_id=row.section_id,
                title=row.section_title,
                description=row.section_description,
                sort_order=row.section_sort_order,
                is_active=row.section_is_active is not False,
                questions=[],
            )
            sections[row.section_id] = section
        if row.question_id is not None:
            section.questions.append(_question(row, choices, dependencies))

    first = rows[0]
    return FormOut(
        form_id=first.form_id,
        title=first.title,
        description=first.description,
        is_multiple_submit=first.is_multiple_submit,
        sections=list(sections.values()),
    )


_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _camel_to_snake(key: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", key).lower()


def snake_keys(value: Any) -> Any:
    """Recursively turns camelCase dict keys into snake_case -- needed for the
    nested sections[].questions[] structure and for a question's
    validation_rule (stored with camelCase keys such as maxLength).
    """
    if isinstance(value, dict):
        return {_camel_to_snake(k): snake_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [snake_keys(item) for item in value]
    return value


async def load_chat_form(session: Session, form_id: UUID | str) -> FormDetail:
    """The form as the chatbot reads it: the same definition as `load_form`
    (what web-backend's GET /service/forms/{formId} used to hand the chatbot
    over HTTP), with snake_case keys and the id named `task_form_id`, the way
    chat.conversation calls it. FormNotFound if there is no such form, also
    when `form_id` is not even an id.
    """
    try:
        wanted = form_id if isinstance(form_id, UUID) else UUID(form_id)
    except ValueError:
        raise FormNotFound(f"Form {form_id} not found") from None
    form = await load_form(session, wanted)
    if form is None:
        raise FormNotFound(f"Form {form_id} not found")
    value = snake_keys(form.model_dump(mode="json", by_alias=True))
    value["task_form_id"] = value.pop("form_id")
    return FormDetail.model_validate(value)
