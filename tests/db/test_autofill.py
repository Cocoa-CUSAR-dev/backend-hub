"""tasks.autofill against real Postgres, ported from mobile-backend's
autofill_farmer_handler_db_test.go: when an offer is made, what it holds,
and that the chatbot's last_answer + sanitize give the same offer."""

from collections.abc import Iterator
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.actor import Actor
from src.exceptions import Forbidden, NotFound
from src.forms import detail, registry
from src.forms.autofill import offered_questions
from src.tasks import autofill
from tests.db.conftest import insert

READER = frozenset({"read:response:own"})
EARLIER = datetime(2026, 10, 1, 3)
LATER = datetime(2026, 10, 2, 3)


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    for module in (registry, detail):
        module.reset_cache()
    yield
    for module in (registry, detail):
        module.reset_cache()


async def _user(session: AsyncSession) -> Actor:
    user_id = await insert(session, "auth.user_account", username=f"farmer-{uuid4()}")
    assert isinstance(user_id, UUID)
    return Actor(user_id=user_id, roles=frozenset({"farmer"}), perms=READER, channel="api")


@pytest.fixture
async def probe(db_session: AsyncSession) -> AsyncSession:
    """Handlers 'probe' (a parent) and 'probe_child' (parent field probe_id),
    and a ref table giving colour_id its choices: red and green."""
    for sql in (
        "CREATE TABLE ref.colour_constant (colour_id varchar PRIMARY KEY, colour_name text)",
        "INSERT INTO ref.colour_constant VALUES ('red', 'แดง'), ('green', 'เขียว')",
        "INSERT INTO form.handler_registry"
        " (handler, schema_name, table_name, parent_handler, parent_field) VALUES"
        " ('probe', 'agriculture', 'farm', NULL, NULL),"
        " ('probe_child', 'agriculture', 'farm', 'probe', 'probe_id')",
    ):
        await db_session.execute(text(sql))
    return db_session


async def _task(session: AsyncSession, handler: str = "probe", *, multiple: bool = False) -> UUID:
    """A task whose form asks colour (OPTION), note, location (GEODATA) and a photo."""
    task_id = await insert(session, "form.task", title="T", open_at=datetime(2026, 10, 1, 8))
    form_id = await insert(
        session,
        "form.task_form",
        task_id=task_id,
        title="F",
        handler=handler,
        is_multiple_submit=multiple,
    )
    section = await insert(session, "form.section", form_id=form_id, title="S", sort_order=1)
    for order, (field, kind) in enumerate(
        [("colour_id", "OPTION"), ("note", "VARCHAR"), ("geo", "GEODATA"), ("upload", "VARCHAR")]
    ):
        await insert(
            session,
            "form.question",
            section_id=section,
            field_name=field,
            input_type=kind,
            label=field,
            sort_order=order,
        )
    assert isinstance(task_id, UUID)
    return task_id


async def _answer(
    session: AsyncSession,
    who: Actor,
    task_id: UUID,
    answer: dict[str, Any],
    at: datetime = EARLIER,
    status: str = "COMPLETED",
) -> None:
    form = await db.forms.form_of_task(session, task_id)
    assert form is not None and who.user_id is not None
    await db.tasks.insert_response(
        session,
        response_id=uuid4(),
        task_id=task_id,
        task_form_id=form.form_id,
        user_id=who.user_id,
        answer={**answer, "task_id": str(task_id)},
        submitted_at=at,
        status=status,
    )


async def test_offers_my_last_answer_from_another_task(probe: AsyncSession) -> None:
    me = await _user(probe)
    await _answer(probe, me, await _task(probe), {"colour_id": "green", "note": "old"}, EARLIER)
    await _answer(probe, me, await _task(probe), {"colour_id": "red", "note": "new"}, LATER)

    offer = await autofill.autofill(probe, me, await _task(probe))

    assert offer is not None
    assert (offer.submitted_at, offer.answer) == (LATER, {"colour_id": "red", "note": "new"})


async def test_location_photo_and_stale_choices_are_never_offered(probe: AsyncSession) -> None:
    me = await _user(probe)
    await _answer(
        probe,
        me,
        await _task(probe),
        {
            "colour_id": "purple",  # no longer a choice
            "note": "kept",
            "geo": [{"lat": 13.7, "lng": 100.5}],
            "upload": "https://storage.example/old.jpg",
            "probe_id": str(uuid4()),  # a parent id
        },
    )

    offer = await autofill.autofill(probe, me, await _task(probe))

    assert offer is not None and offer.answer == {"note": "kept"}


async def test_no_history_is_no_offer(probe: AsyncSession) -> None:
    assert await autofill.autofill(probe, await _user(probe), await _task(probe)) is None


async def test_only_completed_answers_count(probe: AsyncSession) -> None:
    me = await _user(probe)
    await _answer(probe, me, await _task(probe), {"note": "draft"}, status="IN_PROGRESS")

    assert await autofill.autofill(probe, me, await _task(probe)) is None


async def test_someone_elses_answers_are_not_offered_to_me(probe: AsyncSession) -> None:
    me, neighbour = await _user(probe), await _user(probe)
    await _answer(probe, neighbour, await _task(probe), {"note": "theirs"})

    assert await autofill.autofill(probe, me, await _task(probe)) is None


async def test_a_child_handler_form_gets_no_offer(probe: AsyncSession) -> None:
    me = await _user(probe)
    await _answer(probe, me, await _task(probe, "probe_child"), {"note": "old"})

    assert await autofill.autofill(probe, me, await _task(probe, "probe_child")) is None


async def test_a_single_submit_task_i_answered_gets_no_offer(probe: AsyncSession) -> None:
    me, neighbour = await _user(probe), await _user(probe)
    await _answer(probe, me, await _task(probe), {"note": "old"})
    task = await _task(probe)
    await _answer(probe, neighbour, task, {"note": "theirs"})

    assert await autofill.autofill(probe, me, task) is not None  # their answer isn't mine
    await _answer(probe, me, task, {"note": "mine"}, LATER)
    assert await autofill.autofill(probe, me, task) is None  # opening it is an edit now


async def test_a_multi_submit_task_is_offered_even_after_a_row(probe: AsyncSession) -> None:
    me = await _user(probe)
    task = await _task(probe, multiple=True)
    await _answer(probe, me, task, {"note": "first row"})

    offer = await autofill.autofill(probe, me, task)

    assert offer is not None and offer.answer == {"note": "first row"}


async def test_everything_sanitized_away_is_no_offer(probe: AsyncSession) -> None:
    me = await _user(probe)
    await _answer(probe, me, await _task(probe), {"geo": [{"lat": 1, "lng": 2}], "upload": "x"})

    assert await autofill.autofill(probe, me, await _task(probe)) is None


async def test_a_task_i_cannot_see_is_not_found(probe: AsyncSession) -> None:
    me, other = await _user(probe), await _user(probe)
    task = await _task(probe)
    await insert(probe, "form.assignment", task_id=task, user_id=other.user_id)

    with pytest.raises(NotFound):
        await autofill.autofill(probe, me, task)
    with pytest.raises(NotFound):
        await autofill.autofill(probe, me, uuid4())


async def test_the_chatbot_path_gives_the_same_offer(probe: AsyncSession) -> None:
    me = await _user(probe)
    raw = {"colour_id": "red", "note": "n", "upload": "x", "probe_id": str(uuid4())}
    await _answer(probe, me, await _task(probe), raw)
    task = await _task(probe)
    form = await detail.load_form(probe, (await db.forms.form_of_task(probe, task)).form_id)  # type: ignore[union-attr]
    assert form is not None

    last = await autofill.last_answer(probe, me, "probe")
    assert last is not None and last.answer == {**raw, "task_id": last.answer["task_id"]}
    chat_offer = await autofill.sanitize(probe, last.answer, offered_questions(form))
    app_offer = await autofill.autofill(probe, me, task)

    assert app_offer is not None and chat_offer == app_offer.answer == {
        "colour_id": "red",
        "note": "n",
    }
    assert await autofill.last_answer(probe, me, "no_such_handler") is None


async def test_reading_needs_read_response_own(probe: AsyncSession) -> None:
    nobody = Actor(user_id=uuid4(), roles=frozenset(), perms=frozenset(), channel="api")

    with pytest.raises(Forbidden):
        await autofill.autofill(probe, nobody, uuid4())
    with pytest.raises(Forbidden):
        await autofill.last_answer(probe, nobody, "probe")
