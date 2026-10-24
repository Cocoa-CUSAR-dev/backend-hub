"""tasks.answers end to end against real Postgres: a task whose form writes
into a probe handler table created inside the test's transaction."""

from collections.abc import Iterator
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.actor import Actor
from src.exceptions import Forbidden, HandlerNotSupported, Invalid, NotFound
from src.forms import detail, registry
from src.tasks import answers, dissect
from tests.db.conftest import insert

FARMER = frozenset({"create:response:own", "update:response:own", "read:response:own"})


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    for module in (registry, dissect, detail):
        module.reset_cache()
    yield
    for module in (registry, dissect, detail):
        module.reset_cache()


async def _farmer(session: AsyncSession) -> Actor:
    user_id = await insert(session, "auth.user_account", username=f"farmer-{uuid4()}")
    assert isinstance(user_id, UUID)
    return Actor(user_id=user_id, roles=frozenset({"farmer"}), perms=FARMER, channel="api")


@pytest.fixture
async def task(db_session: AsyncSession) -> UUID:
    """A task with a form whose handler writes into agriculture.probe_answers:
    a required farm (a real farm id), a weight 0-500 and an optional note."""
    await db_session.execute(
        text(
            "CREATE TABLE agriculture.probe_answers ("
            " probe_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,"
            " farm_id uuid NOT NULL REFERENCES agriculture.farm (farm_id),"
            " weight_kg numeric, note text)"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO form.handler_registry (handler, schema_name, table_name)"
            " VALUES ('probe_answers', 'agriculture', 'probe_answers')"
        )
    )
    task_id = await insert(
        db_session, "form.task", title="ชั่งน้ำหนัก", open_at=datetime(2026, 10, 1, 8)
    )
    form_id = await insert(
        db_session, "form.task_form", task_id=task_id, title="F", handler="probe_answers"
    )
    section = await insert(db_session, "form.section", form_id=form_id, title="S", sort_order=1)
    for order, (field, kind, required, label) in enumerate(
        [
            ("farm_id", "VARCHAR", True, "ฟาร์ม"),
            ("weight_kg", "FLOAT", True, "น้ำหนัก (กก.)"),
            ("note", "VARCHAR", False, "หมายเหตุ"),
        ]
    ):
        await insert(
            db_session,
            "form.question",
            section_id=section,
            field_name=field,
            input_type=kind,
            is_mandatory=required,
            label=label,
            sort_order=order,
        )
    await db_session.execute(
        text(
            "INSERT INTO form.field_validation_rule (field_name, validation_rule)"
            ' VALUES (\'weight_kg\', \'{"type": "FLOAT", "min": 0, "max": 500,'
            ' "errorMessage": "น้ำหนัก 0-500 กก."}\')'
        )
    )
    assert isinstance(task_id, UUID)
    return task_id


async def _farm(session: AsyncSession) -> str:
    return str(await insert(session, "agriculture.farm", farm_name="F"))


async def _rows(session: AsyncSession, sql: str) -> list[Any]:
    return list((await session.execute(text(sql))).all())


async def test_submit_saves_the_answer_and_its_handler_row(
    db_session: AsyncSession, task: UUID
) -> None:
    me = await _farmer(db_session)
    farm = await _farm(db_session)

    out = await answers.submit(db_session, me, task, {"farm_id": farm, "weight_kg": "12.5"})

    [response] = await _rows(
        db_session, "SELECT response_id, user_id, answer, status FROM form.response"
    )
    assert (response.response_id, response.user_id, response.status) == (
        out.response_id,
        me.user_id,
        "COMPLETED",
    )
    assert response.answer == {"farm_id": farm, "weight_kg": "12.5", "task_id": str(task)}
    [row] = await _rows(
        db_session, "SELECT farm_id, weight_kg, note FROM agriculture.probe_answers"
    )
    assert (str(row.farm_id), float(row.weight_kg), row.note) == (farm, 12.5, None)
    assert out.message == "ส่งงานเรียบร้อยแล้ว"


async def test_an_invalid_answer_writes_nothing(db_session: AsyncSession, task: UUID) -> None:
    me = await _farmer(db_session)

    with pytest.raises(Invalid) as caught:
        await answers.submit(db_session, me, task, {"weight_kg": 900})

    assert caught.value.fields == {"farm_id": "จำเป็นต้องกรอก", "weight_kg": "น้ำหนัก 0-500 กก."}
    assert await _rows(db_session, "SELECT 1 FROM form.response") == []


async def test_a_farm_that_does_not_exist_is_named_by_its_label(
    db_session: AsyncSession, task: UUID
) -> None:
    me = await _farmer(db_session)

    with pytest.raises(Invalid) as caught:
        await answers.submit(db_session, me, task, {"farm_id": str(uuid4()), "weight_kg": 1})

    assert caught.value.fields == {"farm_id": "'ฟาร์ม' ค่าที่เลือกไม่มีอยู่ในระบบแล้ว กรุณาเลือกใหม่"}
    # The response insert rolled back with the handler row.
    assert await _rows(db_session, "SELECT 1 FROM form.response") == []


async def test_a_form_type_that_cannot_be_saved_yet_writes_nothing(
    db_session: AsyncSession, task: UUID
) -> None:
    me = await _farmer(db_session)
    await db_session.execute(text("UPDATE form.task_form SET handler = 'not_built_yet'"))

    with pytest.raises(HandlerNotSupported):
        await answers.submit(db_session, me, task, {"farm_id": await _farm(db_session)})

    assert await _rows(db_session, "SELECT 1 FROM form.response") == []


async def test_tasks_assigned_to_others_or_without_a_form_are_not_found(
    db_session: AsyncSession, task: UUID
) -> None:
    me = await _farmer(db_session)
    other = await _farmer(db_session)
    await insert(db_session, "form.assignment", task_id=task, user_id=other.user_id)
    no_form = await insert(db_session, "form.task", title="no form")

    with pytest.raises(NotFound):
        await answers.submit(db_session, me, task, {})
    with pytest.raises(NotFound):
        await answers.submit(db_session, other, no_form, {})  # type: ignore[arg-type]


async def test_edit_my_latest_or_a_given_answer_but_never_someone_elses(
    db_session: AsyncSession, task: UUID
) -> None:
    me = await _farmer(db_session)
    other = await _farmer(db_session)
    farm = await _farm(db_session)
    first = await answers.submit(db_session, me, task, {"farm_id": farm, "weight_kg": 1})
    theirs = await answers.submit(db_session, other, task, {"farm_id": farm, "weight_kg": 2})

    latest = await answers.edit(db_session, me, task, {"farm_id": farm, "weight_kg": 3})
    given = await answers.edit(
        db_session, me, task, {"farm_id": farm, "weight_kg": 4}, first.response_id
    )

    assert latest.response_id == given.response_id == first.response_id
    [mine] = await _rows(
        db_session,
        f"SELECT answer FROM form.response WHERE response_id = '{first.response_id}'",
    )
    assert mine.answer["weight_kg"] == 4
    with pytest.raises(NotFound):
        await answers.edit(
            db_session, me, task, {"farm_id": farm, "weight_kg": 5}, theirs.response_id
        )
    with pytest.raises(Invalid):
        await answers.edit(db_session, me, task, {"farm_id": farm, "weight_kg": -1})


async def test_editing_with_nothing_submitted_is_not_found(
    db_session: AsyncSession, task: UUID
) -> None:
    me = await _farmer(db_session)
    with pytest.raises(NotFound):
        await answers.edit(
            db_session, me, task, {"farm_id": await _farm(db_session), "weight_kg": 1}
        )


async def test_permissions(db_session: AsyncSession, task: UUID) -> None:
    reader = Actor(
        user_id=uuid4(), roles=frozenset(), perms=frozenset({"read:response:own"}), channel="api"
    )
    with pytest.raises(Forbidden, match="create:response:own"):
        await answers.submit(db_session, reader, task, {})
    with pytest.raises(Forbidden, match="update:response:own"):
        await answers.edit(db_session, reader, task, {})
    with pytest.raises(Forbidden, match="user"):
        await answers.submit(db_session, Actor.system(), task, {})


async def test_a_column_the_form_does_not_ask_for_is_not_written(
    db_session: AsyncSession, task: UUID
) -> None:
    me = await _farmer(db_session)
    sneaky_id = uuid4()

    await answers.submit(
        db_session,
        me,
        task,
        {"farm_id": await _farm(db_session), "weight_kg": 1, "probe_id": str(sneaky_id)},
    )

    [row] = await _rows(db_session, "SELECT probe_id FROM agriculture.probe_answers")
    assert row.probe_id != sneaky_id
