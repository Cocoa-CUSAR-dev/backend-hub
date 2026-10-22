"""Which form fields depend on which (the field-dependency registry, US2-13).

A dependent field's choices are filtered by the answer to its parent field,
e.g. plot_id by farm_id. The rows change only by migration, so they are read
through a small in-process cache with a 5-minute TTL (the same as the old
Kotlin and Go copies): cheap on every form request, yet a newly migrated row
shows up without a restart.

Rows with an unsafe identifier or a link table that does not exist are
dropped with an error log instead of failing every form. The migration's own
CHECK should already stop them; this is defence in depth, because these
names end up in dynamic SQL.

The handler half of the registry (which table a form writes to) joins this
module with docs-and-plan#263.
"""

import logging
import re
import time

from src import db
from src.db.forms import FieldDependencyRow

logger = logging.getLogger(__name__)

TTL_SECONDS = 5 * 60

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

_cache: tuple[float, dict[str, FieldDependencyRow]] | None = None


def reset_cache() -> None:
    """Tests only: forget the cached registry."""
    global _cache
    _cache = None


def _is_safe(dependency: FieldDependencyRow) -> bool:
    link = dependency.link_table.split(".")
    names = [dependency.child_field, dependency.parent_field, dependency.link_column, *link]
    return len(link) == 2 and all(_IDENTIFIER.match(name) for name in names)


async def _load(session: db.Session) -> dict[str, FieldDependencyRow]:
    tables = await db.forms.existing_tables(session)
    by_child: dict[str, FieldDependencyRow] = {}
    for dependency in await db.forms.field_dependencies(session):
        if not _is_safe(dependency):
            logger.error(
                "field_dependency row has an invalid identifier -- ignored",
                extra={"child_field": dependency.child_field},
            )
        elif dependency.link_table not in tables:
            logger.error(
                "field_dependency row points at a missing table -- ignored",
                extra={"child_field": dependency.child_field, "link_table": dependency.link_table},
            )
        else:
            # Rows are ordered by child_field, parent_field: the first wins,
            # as it did in the Kotlin copy.
            by_child.setdefault(dependency.child_field, dependency)
    return by_child


async def dependencies(session: db.Session) -> dict[str, FieldDependencyRow]:
    """Valid dependencies keyed by child field."""
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < TTL_SECONDS:
        return _cache[1]
    loaded = await _load(session)
    _cache = (time.monotonic(), loaded)
    return loaded
