"""tasks.service with the db layer faked: permissions, not-found, paging
fallbacks and the status ladder. The SQL itself is covered in tests/db/."""

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src import db
from src.actor import Actor
from src.db.forms import TaskForm
from src.db.tasks import ResponseRow, TaskRow
from src.exceptions import Forbidden, NotFound
from src.forms.schemas import FormOut
from src.tasks import service

FARMER_PERMS = frozenset({"read:form:assigned", "read:response:own", "read:profile:own"})
SESSION: Any = object()  # never touched: every db call is faked


def _actor(perms: frozenset[str] = FARMER_PERMS, user_id: UUID | None = None) -> Actor:
    return Actor(
        user_id=user_id or uuid4(), roles=frozenset({"farmer"}), perms=perms, channel="mobile"
    )


def _task(answered: bool = False, close_at: datetime | None = None) -> TaskRow:
    return TaskRow(
        task_id=uuid4(),
        title="Harvest",
        description="",
        open_at=datetime(2026, 10, 1, 8),
        close_at=close_at or datetime(2026, 10, 31, 17),
        answered=answered,
    )


def _task_form(task_id: UUID, multiple: bool = False) -> TaskForm:
    return TaskForm(
        task_id=task_id, form_id=uuid4(), version=2, handler="harvest", is_multiple_submit=multiple
    )


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {
        "tasks_for_user": AsyncMock(return_value=[]),
        "latest_answer": AsyncMock(return_value=None),
        "responses_of_user": AsyncMock(return_value=[]),
        "forms_of_tasks": AsyncMock(return_value={}),
        "form_of_task": AsyncMock(return_value=None),
        "load_form": AsyncMock(return_value=None),
    }
    for name in ("tasks_for_user", "latest_answer", "responses_of_user"):
        monkeypatch.setattr(db.tasks, name, fakes[name])
    for name in ("forms_of_tasks", "form_of_task"):
        monkeypatch.setattr(db.forms, name, fakes[name])
    monkeypatch.setattr(service, "load_form", fakes["load_form"])
    return fakes


# --- status_of ---------------------------------------------------------------

NOW = datetime(2026, 10, 15, 12)
BEFORE_CLOSE = datetime(2026, 10, 15, 18)
AFTER_CLOSE = datetime(2026, 10, 15, 11)


@pytest.mark.parametrize(
    ("multiple", "answered", "close_at", "expected"),
    [
        (False, False, BEFORE_CLOSE, "NOT_STARTED"),
        (False, True, BEFORE_CLOSE, "COMPLETED"),
        (False, False, AFTER_CLOSE, "OVERDUE"),
        # An answered single-submit task stays COMPLETED after close.
        (False, True, AFTER_CLOSE, "COMPLETED"),
        (True, False, BEFORE_CLOSE, "NOT_STARTED"),
        # Multi-submit is never COMPLETED: more rows can still be filed.
        (True, True, BEFORE_CLOSE, "IN_PROGRESS"),
        (True, True, AFTER_CLOSE, "OVERDUE"),
        (True, False, AFTER_CLOSE, "OVERDUE"),
    ],
)
def test_status_ladder(multiple: bool, answered: bool, close_at: datetime, expected: str) -> None:
    task = _task(answered=answered, close_at=close_at)
    assert service.status_of(task, _task_form(task.task_id, multiple), NOW) == expected


def test_status_of_a_task_without_form_or_close_time() -> None:
    task = TaskRow(uuid4(), "t", None, None, None, answered=False)
    assert service.status_of(task, None, NOW) == "NOT_STARTED"


# --- list_for ------------------------------------------------------------------


async def test_list_for_joins_each_task_with_its_form(fake_db: dict[str, AsyncMock]) -> None:
    with_form = _task(answered=True)
    without_form = _task()
    form = _task_form(with_form.task_id, multiple=True)
    fake_db["tasks_for_user"].return_value = [with_form, without_form]
    fake_db["forms_of_tasks"].return_value = {with_form.task_id: form}
    me = _actor()

    out = await service.list_for(SESSION, me, open_on=date(2026, 10, 1))

    assert [(t.task_id, t.task_form_id, t.handler, t.is_multiple_submit) for t in out] == [
        (with_form.task_id, form.form_id, "harvest", True),
        (without_form.task_id, None, None, False),
    ]
    assert [t.status for t in out] == ["IN_PROGRESS", "NOT_STARTED"]
    fake_db["tasks_for_user"].assert_awaited_once_with(
        SESSION, me.user_id, open_on=date(2026, 10, 1), limit=50, offset=0
    )
    fake_db["forms_of_tasks"].assert_awaited_once_with(
        SESSION, [with_form.task_id, without_form.task_id]
    )


async def test_list_for_compares_close_at_with_bangkok_time(
    fake_db: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 10:00 UTC is 17:00 in Bangkok: a task closing at 16:00 Bangkok time is
    # already overdue, even though 16:00 has not happened yet in UTC.
    from src import clock

    monkeypatch.setattr(clock, "bangkok_now", lambda: clock.to_bangkok(datetime(2026, 10, 15, 10)))
    task = _task(close_at=datetime(2026, 10, 15, 16))
    fake_db["tasks_for_user"].return_value = [task]

    out = await service.list_for(SESSION, _actor())

    assert out[0].status == "OVERDUE"


@pytest.mark.parametrize(
    ("page", "page_size", "limit", "offset"),
    [
        (2, 20, 20, 40),
        (-1, 20, 20, 0),  # negative page -> first page
        (0, 0, 50, 0),  # no size -> default
        (0, -5, 50, 0),
        (1, 10_000, 500, 500),  # capped
    ],
)
async def test_list_for_paging_falls_back_like_go(
    fake_db: dict[str, AsyncMock], page: int, page_size: int, limit: int, offset: int
) -> None:
    await service.list_for(SESSION, _actor(), page=page, page_size=page_size)

    kwargs = fake_db["tasks_for_user"].await_args.kwargs
    assert (kwargs["limit"], kwargs["offset"]) == (limit, offset)


async def test_list_for_lets_researchers_in_with_read_form_all(
    fake_db: dict[str, AsyncMock],
) -> None:
    await service.list_for(SESSION, _actor(frozenset({"read:form:all"})))
    fake_db["tasks_for_user"].assert_awaited_once()


async def test_list_for_forbidden_without_form_permission(fake_db: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="read:form:assigned"):
        await service.list_for(SESSION, _actor(frozenset({"read:profile:own"})))
    fake_db["tasks_for_user"].assert_not_awaited()


async def test_list_for_needs_a_person(fake_db: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="user"):
        await service.list_for(SESSION, Actor.system())


# --- get -------------------------------------------------------------------------


async def test_get_returns_the_bare_latest_answer(fake_db: dict[str, AsyncMock]) -> None:
    fake_db["latest_answer"].return_value = {"grade": "A"}
    me = _actor()
    task_id = uuid4()

    assert await service.get(SESSION, me, task_id) == {"grade": "A"}
    fake_db["latest_answer"].assert_awaited_once_with(SESSION, me.user_id, task_id)


async def test_get_not_found_before_any_answer(fake_db: dict[str, AsyncMock]) -> None:
    with pytest.raises(NotFound):
        await service.get(SESSION, _actor(), uuid4())


async def test_get_forbidden_without_read_response_own(fake_db: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden, match="read:response:own"):
        await service.get(SESSION, _actor(frozenset({"read:form:assigned"})), uuid4())
    fake_db["latest_answer"].assert_not_awaited()


# --- get_form -----------------------------------------------------------------------


async def test_get_form_returns_the_form_and_its_version(fake_db: dict[str, AsyncMock]) -> None:
    task_id = uuid4()
    task_form = _task_form(task_id)
    form = FormOut(
        form_id=task_form.form_id,
        title="F",
        description=None,
        is_multiple_submit=False,
        sections=[],
    )
    fake_db["form_of_task"].return_value = task_form
    fake_db["load_form"].return_value = form

    out = await service.get_form(SESSION, _actor(), task_id)

    assert (out.form, out.version) == (form, 2)
    fake_db["load_form"].assert_awaited_once_with(SESSION, task_form.form_id)


async def test_get_form_not_found_for_a_task_without_a_form(
    fake_db: dict[str, AsyncMock],
) -> None:
    with pytest.raises(NotFound):
        await service.get_form(SESSION, _actor(), uuid4())
    fake_db["load_form"].assert_not_awaited()


async def test_get_form_not_found_when_the_form_row_is_gone(
    fake_db: dict[str, AsyncMock],
) -> None:
    task_id = uuid4()
    fake_db["form_of_task"].return_value = _task_form(task_id)

    with pytest.raises(NotFound):
        await service.get_form(SESSION, _actor(), task_id)


async def test_get_form_forbidden_without_form_permission(fake_db: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden):
        await service.get_form(SESSION, _actor(frozenset({"read:response:own"})), uuid4())
    fake_db["form_of_task"].assert_not_awaited()


# --- my_responses ----------------------------------------------------------------------


async def test_my_responses_lists_rows_and_is_empty_when_none(
    fake_db: dict[str, AsyncMock],
) -> None:
    assert await service.my_responses(SESSION, _actor(), uuid4()) == []

    row = ResponseRow(
        response_id=uuid4(),
        task_form_id=None,
        submitted_at=datetime(2026, 10, 1, 2),
        status="SUBMITTED",
        answer={"grade": "A"},
    )
    fake_db["responses_of_user"].return_value = [row]

    [out] = await service.my_responses(SESSION, _actor(), uuid4())

    assert (out.response_id, out.answer, out.submitted_at) == (
        row.response_id,
        {"grade": "A"},
        row.submitted_at,
    )


async def test_my_responses_forbidden_without_read_response_own(
    fake_db: dict[str, AsyncMock],
) -> None:
    with pytest.raises(Forbidden):
        await service.my_responses(SESSION, _actor(frozenset({"read:form:assigned"})), uuid4())
    fake_db["responses_of_user"].assert_not_awaited()
