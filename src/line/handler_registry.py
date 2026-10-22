"""Which handlers are child handlers, and what their parent is -- read from
form.handler_registry (database V30, US2-13 / docs-and-plan#242).

parent_picker.py used to hard-code this as _PARENT_KIND_BY_HANDLER and
FIELD_NAME, one of four copies (web-backend, Go x2, here) the team had to keep
in sync by hand. Now a child handler added by migration under an EXISTING
parent kind needs no code here at all. A brand-new parent kind still needs
one picker query in parent_picker.py -- how to label "which harvest?" for a
farmer is product logic, not data -- and parent_picker refuses honestly until
it exists.

Read through a small in-process cache (5-minute TTL, the same as web-backend
and Go): the rows change only by migration, so re-reading them on every
conversation start would be pure overhead.

Same deliberate exception to "the chatbot never touches form.* directly"
(ADR 0001) that parent_picker.py and temp_task_picker.py take: Kotlin exposes
no service endpoint for the registry, and this is a read of reviewed,
migration-only rows.
"""

import logging
import re
import time
from dataclasses import dataclass

from sqlalchemy import text

from src.database import Session

logger = logging.getLogger(__name__)

_TTL_SECONDS = 5 * 60

# Same identifier rule as the migration's CHECK and the other two services:
# parent_field ends up as a key in Go's dynamic INSERT.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class ChildHandler:
    handler: str
    # The handler whose existing row this one hangs off -- what parent_picker
    # calls the "kind" (farm_activity / harvest / batch).
    parent_handler: str
    # The column on the child's table that stores the picked parent id.
    parent_field: str


_cache: tuple[float, dict[str, ChildHandler]] | None = None


def reset_cache() -> None:
    """Tests only: forget the cached registry."""
    global _cache
    _cache = None


async def _child_handlers(session: Session) -> dict[str, ChildHandler]:
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < _TTL_SECONDS:
        return _cache[1]

    rows = await session.execute(
        text(
            """
            SELECT handler, parent_handler, parent_field
            FROM form.handler_registry
            WHERE is_active AND parent_handler IS NOT NULL
            """
        )
    )
    children: dict[str, ChildHandler] = {}
    for row in rows:
        if not _IDENTIFIER.match(row.parent_field or ""):
            logger.error(
                "handler_registry row %s has an invalid parent_field -- ignored", row.handler
            )
            continue
        children[row.handler] = ChildHandler(row.handler, row.parent_handler, row.parent_field)

    _cache = (time.monotonic(), children)
    return children


async def parent_kind_for(session: Session, handler: str) -> str | None:
    """The parent kind a farmer must pick before this handler's questions, or
    None for a standalone handler (the common case)."""
    child = (await _child_handlers(session)).get(handler)
    return child.parent_handler if child else None


async def parent_field_for(session: Session, parent_kind: str) -> str | None:
    """The column a picked parent of this kind is stored under. Every child
    of one parent kind uses the same one (farm_activity_id / harvest_id /
    batch_id), so the kind is enough to find it."""
    for child in (await _child_handlers(session)).values():
        if child.parent_handler == parent_kind:
            return child.parent_field
    return None
