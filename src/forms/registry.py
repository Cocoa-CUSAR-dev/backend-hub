"""The handler registry (US2-13): which table each form writes to, and which
form fields depend on which.

- Handlers (the handler_registry table): a form's handler names the table its
  answers are written to ("dissected" into). A child handler hangs off an
  existing row of its parent handler, whose id it stores under parent_field.
- Field dependencies (the field_dependency table): a dependent field's choices
  are filtered by the answer to its parent field, e.g. plot_id by farm_id.

The rows change only by migration, so both are read through one small
in-process cache with a 5-minute TTL (the same as the old Kotlin and Go
copies): cheap on every request, yet a newly migrated row shows up without
a restart.

Rows with an unsafe identifier or a table that does not exist are dropped
with an error log instead of failing every caller. The migration's own CHECK
should already stop them; this is defence in depth, because these names end
up in dynamic SQL.
"""

import logging
import re
import time
from dataclasses import dataclass

from src import db
from src.database import Session
from src.db.forms import FieldDependencyRow, HandlerRow

logger = logging.getLogger(__name__)

TTL_SECONDS = 5 * 60

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class Registry:
    # handler -> "schema.table", active handlers whose table exists only.
    tables: dict[str, str]
    # handler -> parent handler, for child handlers (active or not).
    parents: dict[str, str]
    # Every parent_field in the registry: an id of a parent row, which must
    # never be offered again as a prefill (it points at last time's row).
    parent_fields: frozenset[str]
    # child field -> its dependency.
    dependencies: dict[str, FieldDependencyRow]
    # The active handlers whose table exists, in the registry's order
    # (sort_order, then name): what the form builder offers.
    handlers: tuple[HandlerRow, ...] = ()
    # Every valid dependency, ordered by child then parent field: what the
    # Data Map draws (`dependencies` keeps only the first per child).
    dependency_list: tuple[FieldDependencyRow, ...] = ()


_cache: tuple[float, Registry] | None = None


def reset_cache() -> None:
    """Tests only: forget the cached registry."""
    global _cache
    _cache = None


def is_identifier(name: str) -> bool:
    """Safe to put in SQL as a (quoted) schema, table or column name."""
    return bool(_IDENTIFIER.match(name))


def _safe_dependency(dependency: FieldDependencyRow) -> bool:
    link = dependency.link_table.split(".")
    names = [dependency.child_field, dependency.parent_field, dependency.link_column, *link]
    return len(link) == 2 and all(is_identifier(name) for name in names)


def _handler_tables(rows: list[HandlerRow], existing: set[str]) -> dict[str, str]:
    tables: dict[str, str] = {}
    for row in rows:
        if not row.is_active:
            continue
        table = f"{row.schema_name}.{row.table_name}"
        if not (is_identifier(row.schema_name) and is_identifier(row.table_name)):
            logger.error(
                "handler_registry row has an invalid identifier -- ignored",
                extra={"handler": row.handler},
            )
        elif table not in existing:
            logger.error(
                "handler_registry row points at a missing table -- ignored",
                extra={"handler": row.handler, "table": table},
            )
        else:
            tables[row.handler] = table
    return tables


def _dependencies(
    rows: list[FieldDependencyRow], existing: set[str]
) -> dict[str, FieldDependencyRow]:
    by_child: dict[str, FieldDependencyRow] = {}
    for dependency in rows:
        if not _safe_dependency(dependency):
            logger.error(
                "field_dependency row has an invalid identifier -- ignored",
                extra={"child_field": dependency.child_field},
            )
        elif dependency.link_table not in existing:
            logger.error(
                "field_dependency row points at a missing table -- ignored",
                extra={"child_field": dependency.child_field, "link_table": dependency.link_table},
            )
        else:
            # Rows are ordered by child_field, parent_field: the first wins,
            # as it did in the Kotlin copy.
            by_child.setdefault(dependency.child_field, dependency)
    return by_child


async def _load(session: Session) -> Registry:
    existing = await db.forms.existing_tables(session)
    handlers = await db.forms.handler_rows(session)
    tables = _handler_tables(handlers, existing)
    dependency_rows = await db.forms.field_dependencies(session)
    return Registry(
        tables=tables,
        parents={r.handler: r.parent_handler for r in handlers if r.parent_handler},
        parent_fields=frozenset(
            r.parent_field for r in handlers if r.parent_field and is_identifier(r.parent_field)
        ),
        dependencies=_dependencies(dependency_rows, existing),
        handlers=tuple(r for r in handlers if r.handler in tables),
        dependency_list=tuple(
            d for d in dependency_rows if _safe_dependency(d) and d.link_table in existing
        ),
    )


async def current(session: Session) -> Registry:
    """The registry as of at most TTL_SECONDS ago."""
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < TTL_SECONDS:
        return _cache[1]
    loaded = await _load(session)
    _cache = (time.monotonic(), loaded)
    return loaded


async def dependencies(session: Session) -> dict[str, FieldDependencyRow]:
    """Valid dependencies keyed by child field."""
    return (await current(session)).dependencies
