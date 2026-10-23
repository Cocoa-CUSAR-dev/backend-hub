"""Generic SQL for the handler tables that form answers are written to.

Which table a form writes to comes from the handler registry at run time
(agriculture.*, collection.*, processing.* ...), so these functions take the
table and column names as arguments, like _profiles.py does for the role
profile tables. Every name is checked against a strict identifier pattern
and double-quoted before it is put in SQL; values are always bind
parameters.
"""

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _quoted(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"not a safe SQL identifier: {name!r}")
    return f'"{name}"'


def _qualified(table: str) -> str:
    schema, _, name = table.partition(".")
    return f"{_quoted(schema)}.{_quoted(name)}"


async def column_types(session: AsyncSession, table: str) -> dict[str, str]:
    """column name -> its SQL type ("uuid", "numeric", "date", ...) for one
    "schema.table"; empty if the table does not exist."""
    result = await session.execute(
        text(
            """
            SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type
            FROM pg_attribute a
            WHERE a.attrelid = to_regclass(:table) AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """
        ),
        {"table": _qualified(table)},
    )
    return {row.name: row.type for row in result}


def _as_text(value: Any) -> str | None:
    """Answers arrive as JSON values; each is sent as text and cast by
    Postgres to the column's own type, as Go's untyped INSERT relied on."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


async def insert_row(
    session: AsyncSession, table: str, values: dict[str, Any], types: dict[str, str]
) -> None:
    """INSERT one row into a handler table. `types` comes from column_types:
    every key of `values` must be one of its columns."""
    columns = list(values)
    names = ", ".join(_quoted(column) for column in columns)
    # Each value goes in as text, then Postgres casts it to the column's type
    # (asyncpg would otherwise expect a matching Python type per column). The
    # types come from the catalog, never from the caller's input.
    params = ", ".join(
        f"CAST(CAST(:p{i} AS text) AS {types[column]})" for i, column in enumerate(columns)
    )
    await session.execute(
        text(f"INSERT INTO {_qualified(table)} ({names}) VALUES ({params})"),
        {f"p{i}": _as_text(values[column]) for i, column in enumerate(columns)},
    )


async def link_exists(
    session: AsyncSession,
    table: str,
    *,
    child_column: str,
    child_value: str,
    parent_column: str,
    parent_value: str,
) -> bool:
    """Whether `table` has a row linking this child to this parent, compared
    as text (e.g. a plot of that farm in agriculture.plot)."""
    found = await session.execute(
        text(
            f"SELECT EXISTS (SELECT 1 FROM {_qualified(table)}"
            f" WHERE {_quoted(child_column)}::text = :child"
            f" AND {_quoted(parent_column)}::text = :parent)"
        ),
        {"child": child_value, "parent": parent_value},
    )
    return bool(found.scalar())
