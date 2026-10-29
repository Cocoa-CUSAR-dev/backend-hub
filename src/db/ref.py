"""Dropdown choices: every ref.*_constant table, read through one generic getter.

Owns:
    ref.*

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.

Table and column names here come from form definitions and the field
registry, so they end up in dynamic SQL. Every one is checked against a
strict identifier pattern and quoted before use.
"""

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class Choice:
    id: str
    name: str
    # Only for a dependent field: the parent row this choice belongs to
    # (a plot's farm_id). None when the choice has no link row.
    parent_id: str | None = None


def _quoted(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"not a safe SQL identifier: {name!r}")
    return f'"{name}"'


async def columns_of(session: AsyncSession, table: str) -> list[str]:
    """Column names of one ref table; empty if the table does not exist."""
    result = await session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'ref' AND table_name = :table
            ORDER BY ordinal_position
            """
        ),
        {"table": table},
    )
    return list(result.scalars())


async def choices(
    session: AsyncSession, table: str, id_column: str, name_column: str
) -> list[Choice]:
    """Every row of a ref table as an {id, name} choice."""
    sql = (
        f"SELECT {_quoted(id_column)}::text AS id, {_quoted(name_column)}::text AS name "
        f"FROM ref.{_quoted(table)}"
    )
    result = await session.execute(text(sql))
    return [Choice(id=row.id, name=row.name) for row in result]


async def choice_name(
    session: AsyncSession, table: str, id_column: str, name_column: str, choice_id: str
) -> str | None:
    """The name of one row of a ref table, found by its id as text; None when
    no row has that id (deleted since it was answered, or never existed)."""
    sql = (
        f"SELECT {_quoted(name_column)}::text FROM ref.{_quoted(table)} "
        f"WHERE {_quoted(id_column)}::text = :choice_id LIMIT 1"
    )
    result = await session.execute(text(sql), {"choice_id": choice_id})
    return result.scalar()


async def choices_with_parent(
    session: AsyncSession,
    table: str,
    id_column: str,
    name_column: str,
    *,
    link_table: str,
    child_column: str,
    parent_column: str,
) -> list[Choice]:
    """Like `choices`, plus each choice's parent id taken from link_table
    (e.g. a plot's farm_id from the plot table, joined on the plot id). The
    ref table itself has no parent column. LEFT JOIN, so a choice with no
    link row still appears, just without a parent.
    """
    link_schema, link_name = link_table.split(".", 1)
    sql = (
        f"SELECT c.{_quoted(id_column)}::text AS id, c.{_quoted(name_column)}::text AS name, "
        f"link.{_quoted(parent_column)}::text AS parent_id "
        f"FROM ref.{_quoted(table)} c "
        f"LEFT JOIN {_quoted(link_schema)}.{_quoted(link_name)} link "
        f"ON link.{_quoted(child_column)} = c.{_quoted(id_column)}"
    )
    result = await session.execute(text(sql))
    return [Choice(id=row.id, name=row.name, parent_id=row.parent_id) for row in result]


async def grade_codes(session: AsyncSession) -> list[Choice]:
    """Choices for grade_code. The answer stores the grade's name ("A"), not
    its uuid, so id and name are both grade_name."""
    result = await session.execute(text("SELECT grade_name FROM ref.grade_constant"))
    return [Choice(id=name, name=name) for name in result.scalars()]


@dataclass(frozen=True, slots=True)
class PlaceIds:
    """Where a place sits: its district and province (either may be None)."""

    province_id: UUID | None
    district_id: UUID | None


async def subdistrict_place(session: AsyncSession, subdistrict_id: UUID) -> PlaceIds | None:
    """The district and province a subdistrict is in, or None if it doesn't exist."""
    row = (
        await session.execute(
            text(
                """
                SELECT d.province_id, s.district_id
                FROM ref.subdistrict_constant s
                JOIN ref.district_constant d ON d.district_id = s.district_id
                WHERE s.subdistrict_id = :subdistrict_id
                """
            ),
            {"subdistrict_id": subdistrict_id},
        )
    ).first()
    return PlaceIds(**row._mapping) if row else None


async def province_of_district(session: AsyncSession, district_id: UUID) -> UUID | None:
    found = await session.execute(
        text("SELECT province_id FROM ref.district_constant WHERE district_id = :district_id"),
        {"district_id": district_id},
    )
    province: UUID | None = found.scalar()
    return province
