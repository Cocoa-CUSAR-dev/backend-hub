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
