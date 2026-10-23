"""src/db/handler_rows.py against real Postgres, on a probe table created
inside the test's transaction (rolled back with it)."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from tests.db.conftest import insert

PROBE = "agriculture.probe_activity"


@pytest.fixture
async def probe(db_session: AsyncSession) -> AsyncSession:
    await db_session.execute(
        text(
            "CREATE TABLE agriculture.probe_activity ("
            " probe_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,"
            " farm_id uuid NOT NULL, amount numeric, done_on date, is_organic boolean,"
            " visits integer, extra jsonb, note varchar(20))"
        )
    )
    return db_session


async def test_column_types_come_from_the_catalog(probe: AsyncSession) -> None:
    types = await db.handler_rows.column_types(probe, PROBE)

    assert types["farm_id"] == "uuid"
    assert types["amount"] == "numeric"
    assert types["note"] == "character varying(20)"
    assert await db.handler_rows.column_types(probe, "agriculture.no_such_table") == {}


async def test_insert_row_casts_json_answers_to_the_column_types(probe: AsyncSession) -> None:
    types = await db.handler_rows.column_types(probe, PROBE)
    farm_id = uuid4()

    await db.handler_rows.insert_row(
        probe,
        PROBE,
        {
            "farm_id": str(farm_id),
            "amount": 12.5,
            "done_on": "2026-10-23",
            "is_organic": True,
            "visits": 3,
            "extra": {"by": "LINE"},
            "note": None,
        },
        types,
    )

    row = (await probe.execute(text("SELECT * FROM agriculture.probe_activity"))).one()
    assert row.farm_id == farm_id
    assert (float(row.amount), str(row.done_on), row.is_organic, row.visits) == (
        12.5,
        "2026-10-23",
        True,
        3,
    )
    assert row.extra == {"by": "LINE"}
    assert row.note is None


async def test_names_are_checked_before_they_reach_sql(probe: AsyncSession) -> None:
    with pytest.raises(ValueError, match="identifier"):
        await db.handler_rows.column_types(probe, "agriculture.probe_activity; DROP")
    with pytest.raises(ValueError, match="identifier"):
        await db.handler_rows.insert_row(
            probe, PROBE, {"farm_id; --": "x"}, {"farm_id; --": "uuid"}
        )


async def test_link_exists(db_session: AsyncSession) -> None:
    farm = await insert(db_session, "agriculture.farm", farm_name="F")
    other_farm = await insert(db_session, "agriculture.farm", farm_name="G")
    plot = await insert(db_session, "agriculture.plot", farm_id=farm, plot_name="P")

    async def linked(plot_id: object, farm_id: object) -> bool:
        return await db.handler_rows.link_exists(
            db_session,
            "agriculture.plot",
            child_column="plot_id",
            child_value=str(plot_id),
            parent_column="farm_id",
            parent_value=str(farm_id),
        )

    assert await linked(plot, farm) is True
    assert await linked(plot, other_farm) is False
    assert await linked(uuid4(), farm) is False
