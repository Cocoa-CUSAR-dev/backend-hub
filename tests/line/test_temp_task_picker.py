import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.actor import Actor
from src.exceptions import Forbidden
from src.line.temp_task_picker import START_KEYWORDS, list_pending_tasks
from src.tasks.schemas import TaskOut


def test_start_keywords_are_case_normalized_before_matching() -> None:
    # router.py matches via message.text.strip().lower() in START_KEYWORDS --
    # this only holds if every keyword here is already lowercase itself.
    assert all(keyword == keyword.lower() for keyword in START_KEYWORDS)
    assert "เริ่ม" in START_KEYWORDS
    assert "start" in START_KEYWORDS


def _farmer() -> Actor:
    return Actor(
        user_id=uuid.uuid4(),
        roles=frozenset({"farmer"}),
        perms=frozenset({"read:form:assigned", "read:response:own"}),
        channel="line",
    )


def _task(
    title: str = "งานทดสอบ", *, status: str = "NOT_STARTED", form: bool = True, multi: bool = False
) -> TaskOut:
    return TaskOut(
        task_id=uuid.uuid4(),
        title=title,
        description=None,
        open_at=datetime(2026, 9, 1),
        close_at=None,
        task_form_id=uuid.uuid4() if form else None,
        handler="notes" if form else None,
        is_multiple_submit=multi,
        status=status,
    )


def _patched(pages: list[list[TaskOut]], open_ids: set[uuid.UUID]):  # type: ignore[no-untyped-def]
    return (
        patch("src.tasks.service.list_for", new=AsyncMock(side_effect=[*pages, []])),
        patch("src.db.chat.open_task_ids", new=AsyncMock(return_value=open_ids)),
    )


async def test_list_pending_tasks_maps_a_task_into_a_pending_task() -> None:
    task = _task()
    tasks_p, chat_p = _patched([[task]], {task.task_id})

    with tasks_p, chat_p:
        tasks = await list_pending_tasks(AsyncMock(), _farmer())

    assert len(tasks) == 1
    assert tasks[0].task_id == task.task_id
    assert tasks[0].task_form_id == task.task_form_id
    assert tasks[0].title == "งานทดสอบ"
    assert tasks[0].handler == "notes"
    assert tasks[0].has_conversation is True


async def test_it_lists_for_the_actor_it_was_given() -> None:
    actor = _farmer()
    tasks_p, chat_p = _patched([[]], set())

    with tasks_p as list_for, chat_p:
        await list_pending_tasks(AsyncMock(), actor)

    assert list_for.await_args.args[1] is actor


async def test_a_completed_task_is_dropped_but_a_multiple_submit_one_stays() -> None:
    done = _task("done", status="COMPLETED")
    multi = _task("multi", status="IN_PROGRESS", multi=True)
    overdue = _task("overdue", status="OVERDUE")
    fresh = _task("fresh")
    tasks_p, chat_p = _patched([[done, multi, overdue, fresh]], set())

    with tasks_p, chat_p:
        tasks = await list_pending_tasks(AsyncMock(), _farmer())

    assert [t.title for t in tasks] == ["multi", "overdue", "fresh"]


async def test_a_task_without_a_form_is_not_listed() -> None:
    tasks_p, chat_p = _patched([[_task(form=False)]], set())

    with tasks_p, chat_p:
        assert await list_pending_tasks(AsyncMock(), _farmer()) == []


async def test_the_list_is_capped_at_the_quick_reply_limit() -> None:
    page = [_task(f"t{i}") for i in range(20)]
    tasks_p, chat_p = _patched([page], set())

    with tasks_p, chat_p:
        tasks = await list_pending_tasks(AsyncMock(), _farmer())

    assert [t.title for t in tasks] == [f"t{i}" for i in range(13)]


async def test_it_keeps_reading_pages_until_enough_tasks_pass_the_filter() -> None:
    answered_page = [_task(f"old{i}", status="COMPLETED") for i in range(50)]
    open_page = [_task("new")]
    tasks_p, chat_p = _patched([answered_page, open_page], set())

    with tasks_p as list_for, chat_p:
        tasks = await list_pending_tasks(AsyncMock(), _farmer())

    assert [t.title for t in tasks] == ["new"]
    assert [call.kwargs["page"] for call in list_for.await_args_list] == [0, 1]


async def test_an_actor_who_may_not_read_forms_is_turned_away() -> None:
    nobody = Actor(user_id=uuid.uuid4(), roles=frozenset(), perms=frozenset(), channel="line")

    with pytest.raises(Forbidden):
        await list_pending_tasks(AsyncMock(), nobody)


async def test_a_system_actor_has_no_list_of_its_own() -> None:
    with pytest.raises(Forbidden):
        await list_pending_tasks(AsyncMock(), Actor.system())
