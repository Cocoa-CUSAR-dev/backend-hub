import json
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from tests.db.conftest import insert


async def _user(session: AsyncSession) -> UUID:
    user_id = await insert(session, "auth.user_account", username=f"farmer-{uuid4()}")
    assert user_id is not None
    return user_id


async def _task(session: AsyncSession, title: str, open_at: datetime, **extra: object) -> UUID:
    task_id = await insert(
        session,
        "form.task",
        title=title,
        open_at=open_at,
        close_at=datetime(2026, 12, 31),
        **extra,
    )
    assert task_id is not None
    return task_id


async def _answer(
    session: AsyncSession, task_id: UUID, user_id: UUID, submitted_at: datetime, **answer: object
) -> UUID:
    response_id = await insert(
        session,
        "form.response",
        task_log_id=task_id,
        user_id=user_id,
        submitted_at=submitted_at,
        answer=json.dumps(answer),
        status="SUBMITTED",
    )
    assert response_id is not None
    return response_id


async def _list(session: AsyncSession, user_id: UUID, **kwargs: object) -> list[db.tasks.TaskRow]:
    args: dict[str, object] = {"open_on": None, "limit": 50, "offset": 0, **kwargs}
    return await db.tasks.tasks_for_user(session, user_id, **args)  # type: ignore[arg-type]


async def test_tasks_come_newest_first_with_whether_i_answered(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    older = await _task(db_session, "older", datetime(2026, 10, 1, 8))
    newer = await _task(db_session, "newer", datetime(2026, 10, 2, 8))
    await _answer(db_session, older, me, datetime(2026, 10, 1, 9))

    rows = await _list(db_session, me)

    assert [(r.task_id, r.answered) for r in rows] == [(newer, False), (older, True)]


async def test_someone_elses_answer_does_not_count_as_mine(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    other = await _user(db_session)
    task = await _task(db_session, "t", datetime(2026, 10, 1, 8))
    await _answer(db_session, task, other, datetime(2026, 10, 1, 9))

    assert [r.answered for r in await _list(db_session, me)] == [False]


async def test_assigned_task_is_only_for_the_people_assigned(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    other = await _user(db_session)
    open_to_all = await _task(db_session, "everyone", datetime(2026, 10, 1, 8))
    mine = await _task(db_session, "mine", datetime(2026, 10, 2, 8))
    theirs = await _task(db_session, "theirs", datetime(2026, 10, 3, 8))
    await insert(db_session, "form.assignment", task_id=mine, user_id=me)
    await insert(db_session, "form.assignment", task_id=theirs, user_id=other)

    ids = {r.task_id for r in await _list(db_session, me)}

    assert ids == {open_to_all, mine}
    assert theirs not in ids


async def test_task_visible_to_follows_the_same_assignment_rule(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    other = await _user(db_session)
    open_to_all = await _task(db_session, "everyone", datetime(2026, 10, 1, 8))
    mine = await _task(db_session, "mine", datetime(2026, 10, 2, 8))
    theirs = await _task(db_session, "theirs", datetime(2026, 10, 3, 8))
    await insert(db_session, "form.assignment", task_id=mine, user_id=me)
    await insert(db_session, "form.assignment", task_id=theirs, user_id=other)

    visible = {
        task: await db.tasks.task_visible_to(db_session, me, task)
        for task in (open_to_all, mine, theirs, uuid4())
    }

    assert list(visible.values()) == [True, True, False, False]


async def test_open_on_keeps_only_that_day(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    await _task(db_session, "first", datetime(2026, 10, 1, 8))
    second = await _task(db_session, "second", datetime(2026, 10, 2, 23, 30))

    rows = await _list(db_session, me, open_on=date(2026, 10, 2))

    assert [r.task_id for r in rows] == [second]


async def test_limit_and_offset_page_through_in_a_stable_order(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    same_time = datetime(2026, 10, 1, 8)
    ids = sorted([await _task(db_session, f"t{i}", same_time) for i in range(3)])

    first = await _list(db_session, me, limit=2, offset=0)
    second = await _list(db_session, me, limit=2, offset=2)

    # Ties on open_at are broken by task_id, so no row repeats or goes missing.
    assert [r.task_id for r in first + second] == ids


async def test_latest_answer_is_the_most_recent_one(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    task = await _task(db_session, "t", datetime(2026, 10, 1, 8))
    await _answer(db_session, task, me, datetime(2026, 10, 1, 9), grade="A")
    await _answer(db_session, task, me, datetime(2026, 10, 1, 10), grade="B")

    assert await db.tasks.latest_answer(db_session, me, task) == {"grade": "B"}


async def test_latest_answer_is_none_before_any_answer(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    task = await _task(db_session, "t", datetime(2026, 10, 1, 8))

    assert await db.tasks.latest_answer(db_session, me, task) is None


async def test_responses_of_user_lists_only_mine_newest_first(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    other = await _user(db_session)
    task = await _task(db_session, "t", datetime(2026, 10, 1, 8))
    first = await _answer(db_session, task, me, datetime(2026, 10, 1, 9), grade="A")
    second = await _answer(db_session, task, me, datetime(2026, 10, 1, 10), grade="B")
    await _answer(db_session, task, other, datetime(2026, 10, 1, 11), grade="C")

    rows = await db.tasks.responses_of_user(db_session, me, task)

    assert [r.response_id for r in rows] == [second, first]
    assert rows[0].answer == {"grade": "B"}
    assert rows[0].submitted_at == datetime(2026, 10, 1, 10)
    assert rows[0].status == "SUBMITTED"


async def test_task_briefs_cover_only_tasks_that_exist(db_session: AsyncSession) -> None:
    opens = datetime(2026, 10, 1, 8)
    titled = await _task(db_session, "harvest", opens)
    untitled = await insert(db_session, "form.task", title=None)
    assert untitled is not None

    briefs = await db.tasks.task_briefs(db_session, [titled, untitled, uuid4()])

    assert briefs == {
        titled: db.tasks.TaskBrief(title="harvest", open_at=opens),
        untitled: db.tasks.TaskBrief(title=None, open_at=None),
    }
    assert await db.tasks.task_briefs(db_session, []) == {}
