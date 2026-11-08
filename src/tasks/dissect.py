"""Writing a submitted answer into its handler table ("dissecting" it).

A form's handler names the table its answers belong to (the farm activity
table, the harvest table, ...). After the answer itself is saved, the parts of it that
match real columns of that table are inserted there too, inside the same
transaction. Ported from mobile-backend's dissectAnswer, liveColumns and
dependencyChecker (form_handler.go, handler_registry.go).

Plain helpers with no permission check: the submit service that calls them
(docs-and-plan#262) checks the caller first.
"""

from collections.abc import Collection
from typing import Any

from src import db
from src.database import Session
from src.exceptions import HandlerNotSupported, Invalid
from src.forms import registry

# "schema.table" -> column -> type. Columns only change by migration plus a
# restart, so they are cached for the life of the process, as Go did.
_column_types: dict[str, dict[str, str]] = {}


def reset_cache() -> None:
    """Tests only: forget cached table columns."""
    _column_types.clear()


async def _columns(session: Session, table: str) -> dict[str, str]:
    if table not in _column_types:
        _column_types[table] = await db.handler_rows.column_types(session, table)
    return _column_types[table]


async def handler_table(session: Session, handler: str) -> str:
    """The table this handler writes to. HandlerNotSupported (501) if the
    registry has no active handler by that name (or its table is missing),
    checked before anything is saved."""
    table = (await registry.current(session)).tables.get(handler)
    if table is None:
        raise HandlerNotSupported(f"Form type {handler!r} can't be saved automatically yet")
    return table


async def dissect(
    session: Session, handler: str, answer: dict[str, Any], fields: Collection[str]
) -> None:
    """Insert the parts of `answer` that are both one of the form's own
    `fields` and a real column of the handler's table. Other keys (task_id,
    keys of a form the table doesn't have) are left out, and so is any column
    the form never asks for: a hand-made request can't set an owner id, a
    primary key or a parent id that isn't a question on the form.
    Invalid (422) if nothing in the answer matches at all.
    """
    table = await handler_table(session, handler)
    types = await _columns(session, table)
    allowed = set(fields)
    values = {key: value for key, value in answer.items() if key in types and key in allowed}
    if not values:
        raise Invalid(f"ไม่มีคำตอบที่ตรงกับคอลัมน์ของ handler {handler}")
    await db.handler_rows.insert_row(session, table, values, types)


def _scalar(value: Any) -> str | None:
    """A single answer value as the text compared in SQL, or None when it
    isn't one (absent, empty, a list...)."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


async def dependency_errors(session: Session, answer: dict[str, Any]) -> dict[str, str]:
    """field -> message for each dependent field whose chosen value does not
    belong to the chosen parent (a plot from another farm). Clients filter
    the choices already, so this only catches a stale screen, an old app or
    a hand-made request. A pair with either side missing is skipped: the
    required-field check is the validator's job.
    """
    errors: dict[str, str] = {}
    for dependency in (await registry.current(session)).dependencies.values():
        child = _scalar(answer.get(dependency.child_field))
        parent = _scalar(answer.get(dependency.parent_field))
        if child is None or parent is None:
            continue
        if not await db.handler_rows.link_exists(
            session,
            dependency.link_table,
            child_column=dependency.child_field,
            child_value=child,
            parent_column=dependency.link_column,
            parent_value=parent,
        ):
            errors[dependency.child_field] = dependency.label_th
    return errors
