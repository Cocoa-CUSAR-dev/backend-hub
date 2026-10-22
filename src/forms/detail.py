"""Build one form's full definition: sections, questions, choices and rules.

The one place every caller gets a form from -- the mobile app's task form
(tasks.service.get_form) today, and the researcher form view and the chatbot
as they move in -- so the app, the web and LINE can't drift apart on what a
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
from collections.abc import Iterable
from uuid import UUID

from src import db
from src.database import Session
from src.db.forms import FieldDependencyRow, FormRow
from src.db.ref import Choice
from src.forms import registry
from src.forms.schemas import ChoiceOut, FormOut, QuestionOut, SectionOut

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
