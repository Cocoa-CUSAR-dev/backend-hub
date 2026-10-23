"""src/tasks/dissect.py end to end: the registry names a probe table created
inside the test's transaction, and dissect writes the answer into it."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import HandlerNotSupported, Invalid
from src.forms import registry
from src.tasks import dissect
from tests.db.conftest import insert


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    registry.reset_cache()
    dissect.reset_cache()
    yield
    registry.reset_cache()
    dissect.reset_cache()


@pytest.fixture
async def probe(db_session: AsyncSession) -> AsyncSession:
    await db_session.execute(
        text(
            "CREATE TABLE agriculture.probe_activity ("
            " probe_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,"
            " farm_id uuid NOT NULL, amount numeric, done_on date)"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO form.handler_registry (handler, schema_name, table_name)"
            " VALUES ('probe', 'agriculture', 'probe_activity')"
        )
    )
    return db_session


async def test_dissect_writes_only_real_columns(probe: AsyncSession) -> None:
    farm_id = uuid4()

    await dissect.dissect(
        probe,
        "probe",
        {
            "task_id": str(uuid4()),  # in every answer, never a column here
            "farm_id": str(farm_id),
            "amount": 7.5,
            "done_on": "2026-10-23",
            "not_a_column": "ignored",
        },
    )

    row = (
        await probe.execute(text("SELECT farm_id, amount, done_on FROM agriculture.probe_activity"))
    ).one()
    assert (row.farm_id, float(row.amount), str(row.done_on)) == (farm_id, 7.5, "2026-10-23")


async def test_an_answer_matching_no_column_is_invalid(probe: AsyncSession) -> None:
    with pytest.raises(Invalid, match="probe"):
        await dissect.dissect(probe, "probe", {"task_id": "t", "colour": "red"})


async def test_an_unknown_or_inactive_handler_is_not_supported(probe: AsyncSession) -> None:
    await probe.execute(
        text("UPDATE form.handler_registry SET is_active = false WHERE handler = 'probe'")
    )

    with pytest.raises(HandlerNotSupported):
        await dissect.dissect(probe, "probe", {"farm_id": str(uuid4())})
    with pytest.raises(HandlerNotSupported):
        await dissect.handler_table(probe, "no_such_handler")


async def test_handler_table_resolves_from_the_registry(probe: AsyncSession) -> None:
    assert await dissect.handler_table(probe, "probe") == "agriculture.probe_activity"


async def test_dependency_errors_catch_a_plot_from_another_farm(db_session: AsyncSession) -> None:
    await insert(
        db_session,
        "form.field_dependency",
        child_field="plot_id",
        parent_field="farm_id",
        link_table="agriculture.plot",
        link_column="farm_id",
        label_th="แปลงนี้ไม่ได้อยู่ในฟาร์มที่เลือก",
    )
    farm = await insert(db_session, "agriculture.farm", farm_name="F")
    other_farm = await insert(db_session, "agriculture.farm", farm_name="G")
    plot = await insert(db_session, "agriculture.plot", farm_id=farm, plot_name="P")

    ok = await dissect.dependency_errors(db_session, {"farm_id": str(farm), "plot_id": str(plot)})
    wrong = await dissect.dependency_errors(
        db_session, {"farm_id": str(other_farm), "plot_id": str(plot)}
    )
    partial = await dissect.dependency_errors(db_session, {"plot_id": str(plot)})
    listed = await dissect.dependency_errors(
        db_session, {"farm_id": str(other_farm), "plot_id": [str(plot)]}
    )

    assert ok == {}
    assert wrong == {"plot_id": "แปลงนี้ไม่ได้อยู่ในฟาร์มที่เลือก"}
    assert partial == {}  # a missing side is the validator's job
    assert listed == {}  # not a single value: skipped, as in Go
