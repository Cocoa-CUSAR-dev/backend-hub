import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

from src.db.forms import TaskForm
from src.db.tasks import TaskRow
from src.line.temp_task_picker import START_KEYWORDS, list_pending_tasks


def test_start_keywords_are_case_normalized_before_matching() -> None:
    # router.py matches via message.text.strip().lower() in START_KEYWORDS --
    # this only holds if every keyword here is already lowercase itself.
    assert all(keyword == keyword.lower() for keyword in START_KEYWORDS)
    assert "เริ่ม" in START_KEYWORDS
    assert "start" in START_KEYWORDS


def _task(title: str = "งานทดสอบ", *, answered: bool = False) -> TaskRow:
    return TaskRow(
        task_id=uuid.uuid4(),
        title=title,
        description=None,
        open_at=datetime(2026, 9, 1),
        close_at=None,
        answered=answered,
    )


def _form(task: TaskRow, *, multiple: bool = False, handler: str | None = "notes") -> TaskForm:
    return TaskForm(
        task_id=task.task_id,
        form_id=uuid.uuid4(),
        version=1,
        handler=handler,
        is_multiple_submit=multiple,
    )


def _patched(pages: list[list[TaskRow]], forms: list[TaskForm], open_ids: set[uuid.UUID]):  # type: ignore[no-untyped-def]
    by_task = {f.task_id: f for f in forms}
    return (
        patch("src.db.tasks.tasks_for_user", new=AsyncMock(side_effect=[*pages, []])),
        patch(
            "src.db.forms.forms_of_tasks",
            new=AsyncMock(side_effect=lambda _s, ids: {i: by_task[i] for i in ids if i in by_task}),
        ),
        patch("src.db.chat.open_task_ids", new=AsyncMock(return_value=open_ids)),
    )


async def test_list_pending_tasks_maps_a_task_and_its_form_into_a_pending_task() -> None:
    task = _task()
    form = _form(task)
    tasks_p, forms_p, chat_p = _patched([[task]], [form], {task.task_id})

    with tasks_p, forms_p, chat_p:
        tasks = await list_pending_tasks(AsyncMock(), uuid.uuid4())

    assert len(tasks) == 1
    assert tasks[0].task_id == task.task_id
    assert tasks[0].task_form_id == form.form_id
    assert tasks[0].title == "งานทดสอบ"
    assert tasks[0].handler == "notes"
    assert tasks[0].has_conversation is True


async def test_an_answered_task_is_dropped_unless_its_form_takes_several_submissions() -> None:
    single, multiple = _task("single", answered=True), _task("multiple", answered=True)
    fresh = _task("fresh")
    tasks_p, forms_p, chat_p = _patched(
        [[single, multiple, fresh]],
        [_form(single), _form(multiple, multiple=True), _form(fresh)],
        set(),
    )

    with tasks_p, forms_p, chat_p:
        tasks = await list_pending_tasks(AsyncMock(), uuid.uuid4())

    assert [t.title for t in tasks] == ["multiple", "fresh"]


async def test_a_task_without_a_form_is_not_listed() -> None:
    task = _task()
    tasks_p, forms_p, chat_p = _patched([[task]], [], set())

    with tasks_p, forms_p, chat_p:
        assert await list_pending_tasks(AsyncMock(), uuid.uuid4()) == []


async def test_the_list_is_capped_at_the_quick_reply_limit() -> None:
    page = [_task(f"t{i}") for i in range(20)]
    tasks_p, forms_p, chat_p = _patched([page], [_form(t) for t in page], set())

    with tasks_p, forms_p, chat_p:
        tasks = await list_pending_tasks(AsyncMock(), uuid.uuid4())

    assert [t.title for t in tasks] == [f"t{i}" for i in range(13)]


async def test_it_keeps_reading_pages_until_enough_tasks_pass_the_filter() -> None:
    answered_page = [_task(f"old{i}", answered=True) for i in range(50)]
    open_page = [_task("new")]
    tasks_p, forms_p, chat_p = _patched(
        [answered_page, open_page],
        [_form(t) for t in answered_page + open_page],
        set(),
    )

    with tasks_p, forms_p, chat_p:
        tasks = await list_pending_tasks(AsyncMock(), uuid.uuid4())

    assert [t.title for t in tasks] == ["new"]
