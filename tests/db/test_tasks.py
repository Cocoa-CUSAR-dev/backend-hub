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


async def test_insert_find_and_update_my_response(db_session: AsyncSession) -> None:
    me = await _user(db_session)
    other = await _user(db_session)
    task = await _task(db_session, "t", datetime(2026, 10, 1, 8))
    other_task = await _task(db_session, "u", datetime(2026, 10, 1, 8))
    form_id, response_id = uuid4(), uuid4()

    await db.tasks.insert_response(
        db_session,
        response_id=response_id,
        task_id=task,
        task_form_id=form_id,
        user_id=me,
        answer={"grade": "A", "note": "ไทย"},
        submitted_at=datetime(2026, 10, 1, 2),
        status="COMPLETED",
    )

    assert await db.tasks.latest_response_id(db_session, me, task) == response_id
    assert await db.tasks.latest_response_id(db_session, other, task) is None
    [row] = await db.tasks.responses_of_user(db_session, me, task)
    assert (row.answer, row.task_form_id, row.status) == (
        {"grade": "A", "note": "ไทย"},
        form_id,
        "COMPLETED",
    )

    async def update(user: UUID, on_task: UUID) -> bool:
        return await db.tasks.update_response(
            db_session,
            response_id=response_id,
            task_id=on_task,
            user_id=user,
            answer={"grade": "B"},
            submitted_at=datetime(2026, 10, 2, 2),
        )

    assert await update(other, task) is False  # not theirs
    assert await update(me, other_task) is False  # not that task's
    assert await update(me, task) is True
    assert await db.tasks.latest_answer(db_session, me, task) == {"grade": "B"}


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


# ---- the researcher's task view (FORM-01 / FORM-02 ?include=stats) -------------


async def test_all_tasks_newest_opening_first_no_open_at_first(db_session: AsyncSession) -> None:
    old = await _task(db_session, "old", datetime(2026, 6, 1), task_type="FORM")
    new = await _task(db_session, "new", datetime(2026, 10, 1), task_type="FORM")
    undated = await insert(db_session, "form.task", title="undated", task_type="TASK")

    rows = await db.tasks.all_tasks(db_session, limit=500, offset=0)

    ids = [r.task_id for r in rows if r.task_id in {old, new, undated}]
    assert ids == [undated, new, old]  # Postgres sorts NULL first in DESC, as Kotlin got


async def test_all_tasks_pages_through_in_a_stable_order(db_session: AsyncSession) -> None:
    same_time = datetime(2031, 1, 1)
    made = {await _task(db_session, f"t{i}", same_time) for i in range(3)}

    first = await db.tasks.all_tasks(db_session, limit=2, offset=0)
    second = await db.tasks.all_tasks(db_session, limit=2, offset=2)

    seen = [r.task_id for r in first + second if r.task_id in made]
    assert len(seen) == len(set(seen)) == 3


async def test_task_info_has_the_type_and_dates(db_session: AsyncSession) -> None:
    task_id = await _task(db_session, "Harvest", datetime(2026, 9, 1), task_type="FORM")

    info = await db.tasks.task_info(db_session, task_id)

    assert info is not None
    assert (info.title, info.task_type, info.open_at) == ("Harvest", "FORM", datetime(2026, 9, 1))
    assert await db.tasks.task_info(db_session, uuid4()) is None


# --- history and diary (ME-03..05) -------------------------------------------


async def test_submission_days_are_bangkok_days_newest_first_paged(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    task = await _task(db_session, "probe", datetime(2026, 10, 1))
    # UTC times: 17:30 UTC on the 4th is already the 5th in Bangkok (+7).
    for moment in (
        datetime(2026, 10, 4, 17, 30),
        datetime(2026, 10, 5, 2, 0),
        datetime(2026, 10, 4, 16, 59),
        datetime(2026, 10, 12, 3, 0),
    ):
        await _answer(db_session, task, user, moment)

    days = await db.tasks.submission_days(db_session, user, limit=50, offset=0)
    first = await db.tasks.submission_days(db_session, user, limit=1, offset=0)
    second = await db.tasks.submission_days(db_session, user, limit=1, offset=1)

    assert days == [date(2026, 10, 12), date(2026, 10, 5), date(2026, 10, 4)]
    assert (first, second) == ([date(2026, 10, 12)], [date(2026, 10, 5)])


async def test_answers_between_are_mine_and_oldest_first(db_session: AsyncSession) -> None:
    me, other = await _user(db_session), await _user(db_session)
    task = await _task(db_session, "probe", datetime(2026, 10, 1))
    await _answer(db_session, task, me, datetime(2026, 10, 5, 3), n=2)
    await _answer(db_session, task, me, datetime(2026, 10, 5, 1), n=1)
    await _answer(db_session, task, other, datetime(2026, 10, 5, 2), n=9)
    await _answer(db_session, task, me, datetime(2026, 10, 6, 1), n=3)

    rows = await db.tasks.answers_between(
        db_session, me, datetime(2026, 10, 5), datetime(2026, 10, 6)
    )

    assert [r.answer for r in rows] == [{"n": 1}, {"n": 2}]


async def test_diary_entry_is_written_once_per_day_and_replaced(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    day = date(2026, 10, 5)
    assert await db.tasks.diary_entry(db_session, user, day) is None

    await db.tasks.save_diary_entry(db_session, user, day, "เช้า", datetime(2026, 10, 5, 1))
    await db.tasks.save_diary_entry(db_session, user, day, "เย็น", datetime(2026, 10, 5, 9))

    entry = await db.tasks.diary_entry(db_session, user, day)
    assert entry is not None
    assert (entry.diary_text, entry.updated_at) == ("เย็น", datetime(2026, 10, 5, 9))
    assert await db.tasks.diary_entry(db_session, user, date(2026, 10, 6)) is None
