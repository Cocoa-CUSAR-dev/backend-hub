"""Form definitions and the handler registry.

Owns:
    form.task_form
    form.section
    form.question
    form.question_visibility
    form.field_validation_rule
    form.handler_registry
    form.field_dependency

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class TaskForm:
    """The form a task is filled with."""

    task_id: UUID
    form_id: UUID
    # The mobile app caches the form and drops the copy when this changes.
    version: int
    handler: str | None
    is_multiple_submit: bool


@dataclass(frozen=True, slots=True)
class FormRow:
    """One row of the form join: the form, one section and one question.

    A section with no questions comes back once with every question_* field
    None; a form with no sections comes back once with every section_* field
    None too. Ordered by section, then question.
    """

    form_id: UUID
    title: str | None
    description: str | None
    is_multiple_submit: bool
    section_id: UUID | None
    section_title: str | None
    section_description: str | None
    section_sort_order: int | None
    section_is_active: bool | None
    question_id: UUID | None
    label: str | None
    input_type: str | None
    question_description: str | None
    field_name: str | None
    default_value: Any
    is_mandatory: bool | None
    question_is_active: bool | None
    carry_forward: bool | None
    question_sort_order: int | None
    validation_rule: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class FieldDependencyRow:
    """A field whose choices are filtered by another field's answer, e.g.
    plot_id by farm_id. link_table ("schema.table") holds both columns."""

    child_field: str
    parent_field: str
    link_table: str
    link_column: str
    label_th: str


@dataclass(frozen=True, slots=True)
class ChildHandlerRow:
    """A handler whose rows hang off another handler's row (a fertilizer
    record under a farm activity). `parent_field` is the column on the child's
    table that stores the parent's id."""

    handler: str
    parent_handler: str
    parent_field: str | None


_TASK_FORM_COLUMNS = """
    task_id, form_id, version, handler,
    COALESCE(is_multiple_submit, FALSE) AS is_multiple_submit
"""


async def form_of_task(session: AsyncSession, task_id: UUID) -> TaskForm | None:
    row = (
        await session.execute(
            text(f"SELECT {_TASK_FORM_COLUMNS} FROM form.task_form WHERE task_id = :task_id"),
            {"task_id": task_id},
        )
    ).first()
    return TaskForm(**row._mapping) if row else None


async def form_by_id(session: AsyncSession, form_id: UUID) -> TaskForm | None:
    row = (
        await session.execute(
            text(f"SELECT {_TASK_FORM_COLUMNS} FROM form.task_form WHERE form_id = :form_id"),
            {"form_id": form_id},
        )
    ).first()
    return TaskForm(**row._mapping) if row else None


@dataclass(frozen=True, slots=True)
class FormListRow:
    """One form in the researcher's form list (FORM-01)."""

    form_id: UUID
    task_id: UUID
    title: str | None
    content: str | None
    handler: str | None
    is_multiple_submit: bool
    description: str | None
    created_at: datetime | None


async def forms_with_sections(session: AsyncSession) -> list[FormListRow]:
    """Every form that has at least one section, newest first.

    A form with no section can't be filled, so it isn't listed (21 legacy
    rows, dynamic-form-proposal.md decision #5), as in web-backend.
    """
    result = await session.execute(
        text(
            """
            SELECT tf.form_id, tf.task_id, tf.title, tf.content, tf.handler,
                   COALESCE(tf.is_multiple_submit, FALSE) AS is_multiple_submit,
                   tf.description, tf.created_at
            FROM form.task_form tf
            WHERE EXISTS (SELECT 1 FROM form.section s WHERE s.form_id = tf.form_id)
            ORDER BY tf.created_at DESC, tf.form_id
            """
        )
    )
    return [FormListRow(**row._mapping) for row in result]


async def forms_of_tasks(session: AsyncSession, task_ids: list[UUID]) -> dict[UUID, TaskForm]:
    """The form of each task that has one, keyed by task_id."""
    if not task_ids:
        return {}
    result = await session.execute(
        text(f"SELECT {_TASK_FORM_COLUMNS} FROM form.task_form WHERE task_id = ANY(:task_ids)"),
        {"task_ids": task_ids},
    )
    return {row.task_id: TaskForm(**row._mapping) for row in result}


async def forms_with_handlers(session: AsyncSession, handlers: Sequence[str]) -> list[TaskForm]:
    """Every form whose handler is one of these."""
    result = await session.execute(
        text(f"SELECT {_TASK_FORM_COLUMNS} FROM form.task_form WHERE handler = ANY(:handlers)"),
        {"handlers": list(handlers)},
    )
    return [TaskForm(**row._mapping) for row in result]


async def child_handlers(session: AsyncSession) -> list[ChildHandlerRow]:
    """Every active handler that has a parent, from form.handler_registry."""
    result = await session.execute(
        text(
            "SELECT handler, parent_handler, parent_field FROM form.handler_registry "
            "WHERE is_active AND parent_handler IS NOT NULL"
        )
    )
    return [ChildHandlerRow(**row._mapping) for row in result]


async def form_rows(session: AsyncSession, form_id: UUID) -> list[FormRow]:
    """Everything needed to build one form: the form, its sections, their
    questions and each question's validation rule. Empty if the form does not
    exist. Inactive sections and questions are included; the caller decides
    what to show.
    """
    result = await session.execute(
        text(
            """
            SELECT
                tf.form_id,
                tf.title,
                tf.description,
                COALESCE(tf.is_multiple_submit, FALSE) AS is_multiple_submit,
                s.section_id,
                s.title AS section_title,
                s.description AS section_description,
                s.sort_order AS section_sort_order,
                s.is_active AS section_is_active,
                q.question_id,
                q.label,
                q.input_type,
                q.description AS question_description,
                q.field_name,
                q.default_value,
                q.is_mandatory,
                q.is_active AS question_is_active,
                q.carry_forward,
                q.sort_order AS question_sort_order,
                r.validation_rule
            FROM form.task_form tf
            LEFT JOIN form.section s ON s.form_id = tf.form_id
            LEFT JOIN form.question q ON q.section_id = s.section_id
            LEFT JOIN form.field_validation_rule r ON r.field_name = q.field_name
            WHERE tf.form_id = :form_id
            ORDER BY s.sort_order, q.sort_order
            """
        ),
        {"form_id": form_id},
    )
    return [FormRow(**row._mapping) for row in result]


@dataclass(frozen=True, slots=True)
class HandlerRow:
    """One handler_registry row: which table a form's answers are written to.
    A child handler (parent_handler set) hangs off an existing row of its
    parent, stored under parent_field on its own table. The label, category
    and the rest are what the form builder and the Data Map show."""

    handler: str
    schema_name: str
    table_name: str
    parent_handler: str | None
    parent_field: str | None
    is_active: bool
    label_th: str = ""
    description_th: str = ""
    category: str = ""
    frequency: str = ""
    # Nullable columns the form must still ask (farm_economic_eval.farm_id).
    extra_required_fields: tuple[str, ...] = ()
    sort_order: int = 0


async def handler_rows(session: AsyncSession) -> list[HandlerRow]:
    """Every handler_registry row as stored, active or not, in the registry's
    own order (sort_order, then name). Not validated: the caller drops rows
    with bad identifiers or a missing table."""
    result = await session.execute(
        text(
            """
            SELECT handler, schema_name, table_name, parent_handler, parent_field, is_active,
                   COALESCE(label_th, '') AS label_th,
                   COALESCE(description_th, '') AS description_th,
                   COALESCE(category, '') AS category,
                   COALESCE(frequency, '') AS frequency,
                   COALESCE(extra_required_fields, '{}') AS extra_required_fields,
                   COALESCE(sort_order, 0) AS sort_order
            FROM form.handler_registry
            ORDER BY sort_order, handler
            """
        )
    )
    return [
        HandlerRow(**{**row._mapping, "extra_required_fields": tuple(row.extra_required_fields)})
        for row in result
    ]


async def field_dependencies(session: AsyncSession) -> list[FieldDependencyRow]:
    """Every field_dependency row as stored. Not validated: the caller drops
    rows with bad identifiers or a missing link table."""
    result = await session.execute(
        text(
            """
            SELECT child_field, parent_field, link_table, link_column, label_th
            FROM form.field_dependency
            ORDER BY child_field, parent_field
            """
        )
    )
    return [FieldDependencyRow(**row._mapping) for row in result]


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """One column of a handler's table, as the form builder needs it."""

    name: str
    data_type: str  # information_schema's name: "character varying", "timestamp without ..."
    udt_name: str  # Postgres' short name: "varchar", "timestamp", "int4"
    not_null_without_default: bool  # an INSERT fails without it
    references: str | None  # "schema.table" of its foreign key, if it has one


async def table_columns(session: AsyncSession, schema: str, table: str) -> list[ColumnInfo]:
    """A table's columns in column order, each with the table its foreign key
    points at. Read from pg_catalog for the keys: information_schema only
    shows constraints on tables the current role owns."""
    result = await session.execute(
        text(
            """
            SELECT c.column_name AS name, c.data_type, c.udt_name,
                   (c.is_nullable = 'NO' AND c.column_default IS NULL) AS not_null_without_default,
                   fk.target AS fk_target
            FROM information_schema.columns c
            LEFT JOIN LATERAL (
                SELECT rn.nspname || '.' || rt.relname AS target
                FROM pg_constraint k
                JOIN pg_class t ON t.oid = k.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY (k.conkey)
                JOIN pg_class rt ON rt.oid = k.confrelid
                JOIN pg_namespace rn ON rn.oid = rt.relnamespace
                WHERE k.contype = 'f'
                  AND n.nspname = c.table_schema
                  AND t.relname = c.table_name
                  AND a.attname = c.column_name
                ORDER BY k.conname
                LIMIT 1
            ) fk ON true
            WHERE c.table_schema = :schema AND c.table_name = :table
            ORDER BY c.ordinal_position
            """
        ),
        {"schema": schema, "table": table},
    )
    return [
        ColumnInfo(
            name=row.name,
            data_type=row.data_type,
            udt_name=row.udt_name,
            not_null_without_default=row.not_null_without_default,
            references=row.fk_target,
        )
        for row in result
    ]


async def existing_tables(session: AsyncSession) -> set[str]:
    """Every base table in the database as "schema.table" -- one cheap query
    to check that registry rows point at tables that really exist."""
    result = await session.execute(
        text("SELECT table_schema, table_name FROM information_schema.tables")
    )
    return {f"{row.table_schema}.{row.table_name}" for row in result}


@dataclass(frozen=True, slots=True)
class QuestionField:
    """What a farmer was asked for one field of one form."""

    label: str | None
    input_type: str | None


async def question_fields(
    session: AsyncSession, form_ids: Sequence[UUID]
) -> dict[tuple[UUID, str], QuestionField]:
    """(form_id, field_name) -> its question's label and input type, in the
    forms' question order (section, then question). Scoped per form: one
    field_name can be asked with a different label on another form. A field
    asked twice on one form keeps its last question, as web-backend did."""
    if not form_ids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT s.form_id, q.field_name, q.label, q.input_type
            FROM form.question q
            JOIN form.section s ON s.section_id = q.section_id
            WHERE s.form_id = ANY(:form_ids) AND q.field_name IS NOT NULL
            ORDER BY s.form_id, s.sort_order NULLS LAST, q.sort_order NULLS LAST, q.question_id
            """
        ),
        {"form_ids": list(form_ids)},
    )
    fields: dict[tuple[UUID, str], QuestionField] = {}
    for row in result:
        key = (row.form_id, row.field_name)
        fields.pop(key, None)  # re-insert so the last question decides the order too
        fields[key] = QuestionField(label=row.label, input_type=row.input_type)
    return fields


# ---- the Data Map's counts (GET /api/v1/forms/handlers/catalog) ----------------------


@dataclass(frozen=True, slots=True)
class HandlerForm:
    """One form and the handler it writes to."""

    handler: str
    form_id: UUID
    task_id: UUID


async def handler_forms(session: AsyncSession) -> list[HandlerForm]:
    """Every form that names a handler."""
    result = await session.execute(
        text("SELECT handler, form_id, task_id FROM form.task_form WHERE handler IS NOT NULL")
    )
    return [HandlerForm(**row._mapping) for row in result]


async def handler_field_usage(session: AsyncSession) -> dict[str, dict[str, int]]:
    """handler -> field_name -> how many of its forms ask that field, counting
    only active sections and questions: what a farmer is actually asked."""
    result = await session.execute(
        text(
            """
            SELECT tf.handler, q.field_name, count(DISTINCT tf.form_id) AS forms
            FROM form.task_form tf
            JOIN form.section s ON s.form_id = tf.form_id AND COALESCE(s.is_active, true)
            JOIN form.question q ON q.section_id = s.section_id AND COALESCE(q.is_active, true)
            WHERE q.field_name IS NOT NULL AND tf.handler IS NOT NULL
            GROUP BY tf.handler, q.field_name
            """
        )
    )
    usage: dict[str, dict[str, int]] = {}
    for row in result:
        usage.setdefault(row.handler, {})[row.field_name] = row.forms
    return usage


async def field_labels(session: AsyncSession) -> dict[str, str]:
    """field_name -> the label researchers most often give it across active
    questions, so the Data Map can say ฟาร์ม instead of farm_id. Ties go to
    the alphabetically first label, so the page doesn't flicker."""
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (field_name) field_name, label
            FROM (
                SELECT q.field_name, btrim(q.label) AS label, count(*) AS uses
                FROM form.question q
                WHERE q.field_name IS NOT NULL AND btrim(q.label) <> ''
                  AND COALESCE(q.is_active, true)
                GROUP BY q.field_name, btrim(q.label)
            ) labels
            ORDER BY field_name, uses DESC, label
            """
        )
    )
    return {row.field_name: row.label for row in result}


# ---- the form builder's writes (FORM-03, FORM-04, FORM-05) ---------------------------


@dataclass(frozen=True, slots=True)
class QuestionWrite:
    """What a question is saved with, new or rewritten."""

    label: str
    description: str | None
    input_type: str
    field_name: str | None
    is_mandatory: bool
    is_active: bool
    # None on a rewrite = keep what is stored, so a client that doesn't know
    # the field yet can't switch it off.
    carry_forward: bool | None
    sort_order: int
    default_value: Any


async def insert_task_form(
    session: AsyncSession,
    *,
    task_id: UUID,
    title: str,
    description: str | None,
    handler: str,
    is_multiple_submit: bool,
) -> UUID:
    result = await session.execute(
        text(
            """
            INSERT INTO form.task_form (task_id, title, description, handler, is_multiple_submit)
            VALUES (:task_id, :title, :description, :handler, :is_multiple_submit)
            RETURNING form_id
            """
        ),
        {
            "task_id": task_id,
            "title": title,
            "description": description,
            "handler": handler,
            "is_multiple_submit": is_multiple_submit,
        },
    )
    form_id: UUID = result.scalar_one()
    return form_id


async def update_task_form(
    session: AsyncSession,
    form_id: UUID,
    *,
    description: str | None,
    is_multiple_submit: bool | None,
) -> None:
    """The description is always written (null clears it); is_multiple_submit
    only when given."""
    await session.execute(
        text(
            """
            UPDATE form.task_form
            SET description = :description,
                is_multiple_submit = COALESCE(CAST(:is_multiple_submit AS boolean),
                                              is_multiple_submit)
            WHERE form_id = :form_id
            """
        ),
        {"form_id": form_id, "description": description, "is_multiple_submit": is_multiple_submit},
    )


async def insert_section(
    session: AsyncSession,
    *,
    form_id: UUID,
    title: str,
    description: str | None,
    sort_order: int,
    is_active: bool = True,
) -> UUID:
    result = await session.execute(
        text(
            """
            INSERT INTO form.section (form_id, title, description, sort_order, is_active)
            VALUES (:form_id, :title, :description, :sort_order, :is_active)
            RETURNING section_id
            """
        ),
        {
            "form_id": form_id,
            "title": title,
            "description": description,
            "sort_order": sort_order,
            "is_active": is_active,
        },
    )
    section_id: UUID = result.scalar_one()
    return section_id


async def update_section(
    session: AsyncSession,
    section_id: UUID,
    *,
    title: str,
    description: str | None,
    sort_order: int,
    is_active: bool,
) -> None:
    await session.execute(
        text(
            """
            UPDATE form.section
            SET title = :title, description = :description, sort_order = :sort_order,
                is_active = :is_active
            WHERE section_id = :section_id
            """
        ),
        {
            "section_id": section_id,
            "title": title,
            "description": description,
            "sort_order": sort_order,
            "is_active": is_active,
        },
    )


def _question_params(question: QuestionWrite) -> dict[str, Any]:
    return {
        "label": question.label,
        "description": question.description,
        "input_type": question.input_type,
        "field_name": question.field_name,
        "is_mandatory": question.is_mandatory,
        "is_active": question.is_active,
        "carry_forward": question.carry_forward,
        "sort_order": question.sort_order,
        "default_value": None
        if question.default_value is None
        else json.dumps(question.default_value, ensure_ascii=False),
    }


async def insert_question(session: AsyncSession, section_id: UUID, question: QuestionWrite) -> UUID:
    result = await session.execute(
        text(
            """
            INSERT INTO form.question
                (section_id, label, description, input_type, field_name, is_mandatory,
                 is_active, carry_forward, sort_order, default_value)
            VALUES (:section_id, :label, :description, :input_type, :field_name, :is_mandatory,
                    :is_active, COALESCE(CAST(:carry_forward AS boolean), false), :sort_order,
                    CAST(:default_value AS jsonb))
            RETURNING question_id
            """
        ),
        {"section_id": section_id, **_question_params(question)},
    )
    question_id: UUID = result.scalar_one()
    return question_id


async def update_question(
    session: AsyncSession, question_id: UUID, question: QuestionWrite
) -> None:
    await session.execute(
        text(
            """
            UPDATE form.question
            SET label = :label, description = :description, input_type = :input_type,
                field_name = :field_name, is_mandatory = :is_mandatory, is_active = :is_active,
                carry_forward = COALESCE(CAST(:carry_forward AS boolean), carry_forward),
                sort_order = :sort_order, default_value = CAST(:default_value AS jsonb)
            WHERE question_id = :question_id
            """
        ),
        {"question_id": question_id, **_question_params(question)},
    )


async def section_ids_of(session: AsyncSession, form_id: UUID) -> set[UUID]:
    result = await session.execute(
        text("SELECT section_id FROM form.section WHERE form_id = :form_id"), {"form_id": form_id}
    )
    return set(result.scalars())


async def question_ids_of(session: AsyncSession, section_id: UUID) -> set[UUID]:
    result = await session.execute(
        text("SELECT question_id FROM form.question WHERE section_id = :section_id"),
        {"section_id": section_id},
    )
    return set(result.scalars())


async def delete_questions_except(
    session: AsyncSession, section_id: UUID, keep: Sequence[UUID]
) -> None:
    await session.execute(
        text(
            "DELETE FROM form.question WHERE section_id = :section_id"
            " AND NOT (question_id = ANY(:keep))"
        ),
        {"section_id": section_id, "keep": list(keep)},
    )


async def delete_sections(session: AsyncSession, section_ids: Sequence[UUID]) -> None:
    """The sections and their questions."""
    if not section_ids:
        return
    ids = {"ids": list(section_ids)}
    await session.execute(text("DELETE FROM form.question WHERE section_id = ANY(:ids)"), ids)
    await session.execute(text("DELETE FROM form.section WHERE section_id = ANY(:ids)"), ids)


async def set_section_active(
    session: AsyncSession, form_id: UUID, section_id: UUID, is_active: bool
) -> None:
    """Only a section of this form."""
    await session.execute(
        text(
            "UPDATE form.section SET is_active = :is_active"
            " WHERE section_id = :section_id AND form_id = :form_id"
        ),
        {"form_id": form_id, "section_id": section_id, "is_active": is_active},
    )


async def set_question_flags(
    session: AsyncSession,
    form_id: UUID,
    question_id: UUID,
    *,
    is_active: bool | None,
    is_mandatory: bool | None,
) -> None:
    """Switch a question of this form on or off, required or not; None keeps it."""
    await session.execute(
        text(
            """
            UPDATE form.question q
            SET is_active = COALESCE(CAST(:is_active AS boolean), q.is_active),
                is_mandatory = COALESCE(CAST(:is_mandatory AS boolean), q.is_mandatory)
            FROM form.section s
            WHERE q.question_id = :question_id AND s.section_id = q.section_id
              AND s.form_id = :form_id
            """
        ),
        {
            "form_id": form_id,
            "question_id": question_id,
            "is_active": is_active,
            "is_mandatory": is_mandatory,
        },
    )


# ---- a repeating task's new round gets its own copy of the form (JOB-03) ---------------

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# (schema, table) -> its column names, for copying rows whole. Only changes by
# migration plus restart, so it is cached for the life of the process.
_copy_columns: dict[tuple[str, str], list[str]] = {}


def _safe(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"not a safe SQL identifier: {name!r}")
    return f'"{name}"'


async def _copyable(session: AsyncSession, table: str, *skip: str) -> str:
    schema, name = table.split(".")
    if (schema, name) not in _copy_columns:
        result = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = :schema AND table_name = :table ORDER BY ordinal_position"
            ),
            {"schema": schema, "table": name},
        )
        _copy_columns[(schema, name)] = list(result.scalars())
    return ", ".join(_safe(c) for c in _copy_columns[(schema, name)] if c not in skip)


async def copy_form(session: AsyncSession, template_task_id: UUID, new_task_id: UUID) -> UUID:
    """Copy the template task's form, its sections, its questions and who may
    see each question onto `new_task_id`, every column as it is (a new id
    each, and the form's created_at now). Returns the new form's id."""
    form_cols = await _copyable(session, "form.task_form", "form_id", "task_id", "created_at")
    template = (
        await session.execute(
            text("SELECT form_id FROM form.task_form WHERE task_id = :t"), {"t": template_task_id}
        )
    ).scalar()
    if template is None:
        raise LookupError(f"template task {template_task_id} has no form to copy")
    new_form: UUID = (
        await session.execute(
            text(
                f"INSERT INTO form.task_form (task_id, {form_cols})"
                f" SELECT :new_task, {form_cols} FROM form.task_form WHERE form_id = :form"
                " RETURNING form_id"
            ),
            {"new_task": new_task_id, "form": template},
        )
    ).scalar_one()

    section_cols = await _copyable(session, "form.section", "section_id", "form_id")
    question_cols = await _copyable(session, "form.question", "question_id", "section_id")
    visibility_cols = await _copyable(
        session, "form.question_visibility", "visibility_id", "question_id"
    )
    sections = (
        await session.execute(
            text(
                "SELECT section_id FROM form.section WHERE form_id = :form"
                " ORDER BY sort_order, section_id"
            ),
            {"form": template},
        )
    ).scalars()
    for section in list(sections):
        new_section = (
            await session.execute(
                text(
                    f"INSERT INTO form.section (form_id, {section_cols})"
                    f" SELECT :new_form, {section_cols} FROM form.section WHERE section_id = :s"
                    " RETURNING section_id"
                ),
                {"new_form": new_form, "s": section},
            )
        ).scalar_one()
        questions = (
            await session.execute(
                text(
                    "SELECT question_id FROM form.question WHERE section_id = :s"
                    " ORDER BY sort_order, question_id"
                ),
                {"s": section},
            )
        ).scalars()
        for question in list(questions):
            new_question = (
                await session.execute(
                    text(
                        f"INSERT INTO form.question (section_id, {question_cols})"
                        f" SELECT :new_section, {question_cols} FROM form.question"
                        " WHERE question_id = :q RETURNING question_id"
                    ),
                    {"new_section": new_section, "q": question},
                )
            ).scalar_one()
            await session.execute(
                text(
                    f"INSERT INTO form.question_visibility (question_id, {visibility_cols})"
                    f" SELECT :new_question, {visibility_cols} FROM form.question_visibility"
                    " WHERE question_id = :q"
                ),
                {"new_question": new_question, "q": question},
            )
    return new_form
