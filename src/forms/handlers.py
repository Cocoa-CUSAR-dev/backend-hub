"""The form builder's handler pickers and the Data Map catalog (FORM-06, FORM-07).

Ported from web-backend's HandlerCatalogRepository and FormService:

- GET /forms/handlers                   the handlers a form can write to
- GET /forms/handlers/{handler}/fields  one handler's columns, as the builder needs them
- GET /forms/handlers/catalog           every handler with its columns and counts

All read the one registry (src/forms/registry.py). A column is `required`
when an INSERT fails without it (NOT NULL, no default), except a child
handler's parent_field, which the chatbot's parent picker fills; plus the
registry's extra_required_fields. Its allowed input types follow its column
type, so a researcher can't build a form whose answers can never be saved.
"""

import time
from datetime import datetime
from uuid import UUID

from src import db
from src.actor import Actor, require
from src.database import Session
from src.db.forms import ColumnInfo, HandlerForm, HandlerRow
from src.db.tasks import TaskBrief
from src.exceptions import Invalid
from src.forms import registry
from src.forms.registry import Registry
from src.forms.schemas import (
    CatalogEntryOut,
    CatalogFieldOut,
    CatalogOut,
    DependencyOut,
    HandlerFieldOut,
    HandlerFormsOut,
    RegistrationTableOut,
)

# Tables filled by the app's registration flows, not by forms: shown greyed on
# the Data Map so researchers see the whole databank. Not handlers.
REGISTRATION_TABLES = (
    ("agriculture.farmer", "เกษตรกร"),
    ("agriculture.farm", "ฟาร์ม"),
    ("agriculture.plot", "แปลง"),
    ("processing.hub", "จุดรวบรวม"),
    ("processing.processing_station", "สถานีแปรรูป"),
)

# The column type names web-backend answered with (jOOQ's), by Postgres'
# udt_name. A name not listed is the same in both (uuid, varchar, numeric,
# timestamp, date, text).
_TYPE_NAMES = {
    "bool": "boolean",
    "int2": "smallint",
    "int4": "integer",
    "int8": "bigint",
    "float4": "real",
    "float8": "double precision",
    "timestamptz": "timestamp with time zone",
}

_NUMBERS = {"integer", "bigint", "smallint"}
_DECIMALS = {"numeric", "real", "double precision"}
_TEXT = {"character varying", "text"}

_catalog: tuple[float, CatalogOut] | None = None


def reset_cache() -> None:
    """Tests only: forget the cached catalog."""
    global _catalog
    _catalog = None


def allowed_input_types(data_type: str, *, is_foreign_key: bool) -> list[str]:
    """The question input types a column of this information_schema type can
    hold, the builder's default first. A varchar foreign key (grade_code ->
    ref.grade_constant) is the one text column that also takes OPTION; an
    unknown type takes none, and saving the form then says so."""
    if data_type == "uuid":
        return ["OPTION"]
    if data_type in _NUMBERS:
        return ["INT"]
    if data_type in _DECIMALS:
        return ["FLOAT", "INT"]
    if data_type == "boolean":
        return ["BOOLEAN"]
    if data_type == "date":
        return ["DATE"]
    if data_type.startswith("timestamp"):
        return ["DATETIME", "DATE"]
    if data_type in _TEXT:
        return ["OPTION", "VARCHAR"] if is_foreign_key else ["VARCHAR"]
    return []


def _field(reg: Registry, handler: HandlerRow, column: ColumnInfo) -> HandlerFieldOut:
    dependency = reg.dependencies.get(column.name)
    required = column.name in handler.extra_required_fields or (
        column.not_null_without_default and column.name != handler.parent_field
    )
    return HandlerFieldOut(
        name=column.name,
        data_type=_TYPE_NAMES.get(column.udt_name, column.udt_name),
        required=required,
        references=column.references,
        depends_on=dependency.parent_field if dependency else None,
        allowed_input_types=allowed_input_types(
            column.data_type, is_foreign_key=column.references is not None
        ),
    )


async def _fields(session: Session, reg: Registry, handler: HandlerRow) -> list[HandlerFieldOut]:
    columns = await db.forms.table_columns(session, handler.schema_name, handler.table_name)
    return [_field(reg, handler, column) for column in columns]


async def handler_names(session: Session, actor: Actor) -> list[str]:
    """The handlers a form can write to, in the registry's order."""
    require(actor, "create:form:all")
    return [h.handler for h in (await registry.current(session)).handlers]


async def handler_fields(session: Session, actor: Actor, handler: str) -> list[HandlerFieldOut]:
    """One handler's columns in table order. An unknown or inactive handler
    is 422 (Kotlin: 400)."""
    require(actor, "create:form:all")
    reg = await registry.current(session)
    found = next((h for h in reg.handlers if h.handler == handler), None)
    if found is None:
        raise Invalid(f"Unknown handler: {handler}", fields={"handler": "No such handler"})
    return await _fields(session, reg, found)


async def catalog(session: Session, actor: Actor) -> CatalogOut:
    """Every handler with its columns, how many forms write to it and ask each
    column, and how many answers it holds. It changes slowly (registry rows by
    migration, counts as forms trickle in), so it is cached as long as the
    registry is."""
    require(actor, "read:form:all")
    global _catalog
    if _catalog is not None and time.monotonic() - _catalog[0] < registry.TTL_SECONDS:
        return _catalog[1]
    built = await _build_catalog(session)
    _catalog = (time.monotonic(), built)
    return built


def _newest(forms: list[HandlerForm], briefs: dict[UUID, TaskBrief]) -> TaskBrief | None:
    """The task of these forms that opens last; one with no opening time only
    when none has one (Kotlin: open_at DESC NULLS LAST)."""
    tasks = [briefs[f.task_id] for f in forms if f.task_id in briefs]
    timed = [t for t in tasks if t.open_at is not None]
    if timed:
        return max(timed, key=lambda t: t.open_at or datetime.min)
    return tasks[0] if tasks else None


async def _build_catalog(session: Session) -> CatalogOut:
    reg = await registry.current(session)
    forms = await db.forms.handler_forms(session)
    briefs = await db.tasks.task_briefs(session, list({f.task_id for f in forms}))
    answers = await db.tasks.response_counts_by_form(session)
    usage = await db.forms.handler_field_usage(session)

    entries = []
    for handler in reg.handlers:
        mine = [f for f in forms if f.handler == handler.handler]
        latest = _newest(mine, briefs)
        asked = usage.get(handler.handler, {})
        entries.append(
            CatalogEntryOut(
                handler=handler.handler,
                label_th=handler.label_th,
                description_th=handler.description_th,
                category=handler.category,
                frequency=handler.frequency,
                table=f"{handler.schema_name}.{handler.table_name}",
                parent_handler=handler.parent_handler,
                parent_field=handler.parent_field,
                fields=[
                    CatalogFieldOut(**f.model_dump(), used_by_forms=asked.get(f.name, 0))
                    for f in await _fields(session, reg, handler)
                ],
                forms=HandlerFormsOut(
                    count=len(mine),
                    latest_title=latest.title if latest else None,
                    latest_open_at=latest.open_at if latest else None,
                ),
                response_count=sum(answers.get(f.form_id, 0) for f in mine),
            )
        )
    return CatalogOut(
        handlers=entries,
        dependencies=[
            DependencyOut(
                child_field=d.child_field,
                parent_field=d.parent_field,
                link_table=d.link_table,
                link_column=d.link_column,
                label_th=d.label_th,
            )
            for d in reg.dependency_list
        ],
        registration_tables=[
            RegistrationTableOut(table=table, label_th=label)
            for table, label in REGISTRATION_TABLES
        ],
        field_labels=await db.forms.field_labels(session),
    )
