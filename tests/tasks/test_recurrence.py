"""src/tasks/recurrence.py: the days a round is due and its window (pure, as
web-backend's RecurrenceSchedule tested them), and a whole run against real
Postgres on forms made with forms.builder. Rolled back after each test."""

from datetime import date, datetime, time
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.actor import Actor
from src.db.tasks import RunningRecurrence
from src.forms import builder, detail, registry
from src.forms.schemas import FormCreateIn
from src.tasks import recurrence
from tests.db.conftest import db_session, insert  # noqa: F401 -- the fixture


def _recurrence(**more: Any) -> RunningRecurrence:
    values: dict[str, Any] = {
        "recurrence_id": uuid4(),
        "template_task_id": uuid4(),
        "frequency": "DAILY",
        "weekdays": [],
        "start_date": date(2026, 11, 1),
        "until_date": None,
        "created_by": uuid4(),
        "template_open_at": datetime(2026, 11, 1, 8),
        "template_close_at": datetime(2026, 11, 1, 17),
    }
    return RunningRecurrence(**(values | more))


# ---- the calendar (pure) --------------------------------------------------------------


def test_daily_rounds_never_the_start_day_or_the_future() -> None:
    assert recurrence.due_dates(_recurrence(), date(2026, 11, 3)) == [
        date(2026, 11, 2),
        date(2026, 11, 3),
    ]
    assert recurrence.due_dates(_recurrence(), date(2026, 11, 1)) == []


def test_weekly_rounds_on_iso_weekdays() -> None:
    # 2026-11-02 is a Monday (1), 2026-11-05 a Thursday (4).
    weekly = _recurrence(frequency="WEEKLY", weekdays=[1, 4])
    assert recurrence.due_dates(weekly, date(2026, 11, 7)) == [date(2026, 11, 2), date(2026, 11, 5)]


def test_a_missed_round_is_opened_for_six_days_back_and_never_past_the_end() -> None:
    late = recurrence.due_dates(_recurrence(), date(2026, 11, 20))
    assert (late[0], late[-1], len(late)) == (date(2026, 11, 14), date(2026, 11, 20), 7)
    ended = _recurrence(until_date=date(2026, 11, 3))
    assert recurrence.due_dates(ended, date(2026, 11, 20)) == []
    assert recurrence.due_dates(ended, date(2026, 11, 4)) == [date(2026, 11, 2), date(2026, 11, 3)]


def test_a_round_keeps_the_templates_time_and_length() -> None:
    opens, closes = recurrence.window(
        datetime(2026, 11, 2, 8), datetime(2026, 11, 4, 17), date(2026, 11, 9)
    )
    assert (opens, closes) == (datetime(2026, 11, 9, 8), datetime(2026, 11, 11, 17))


# ---- a whole run (real Postgres) ---------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    registry.reset_cache()
    detail.reset_cache()


@pytest.fixture
async def researcher(db_session: AsyncSession) -> Actor:  # noqa: F811
    me = await insert(db_session, "auth.user_account", username=f"researcher-{uuid4()}")
    assert me is not None
    return Actor(me, frozenset(), frozenset({"create:form:all", "update:form:all"}), "api")


async def _repeating(session: AsyncSession, researcher: Actor, **more: Any) -> tuple[UUID, UUID]:
    """A form repeating daily from 2026-11-01, open 08:00-17:00; (form, task)."""
    body = {
        "title": "รายวัน",
        "openAt": "2026-11-01T08:00:00",
        "closeAt": "2026-11-01T17:00:00",
        "handler": "harvest",
        "sections": [
            {
                "title": "s",
                "sortOrder": 0,
                "questions": [
                    {
                        "label": "วันที่",
                        "inputType": "DATE",
                        "fieldName": "harvest_date",
                        "sortOrder": 0,
                    }
                ],
            }
        ],
        "recurrence": {"frequency": "DAILY"},
    }
    form = await builder.create_form(session, researcher, FormCreateIn.model_validate(body | more))
    task = await db.forms.form_by_id(session, form.form_id)
    assert task is not None
    return form.form_id, task.task_id


async def _rounds(session: AsyncSession, template: UUID) -> list[Any]:
    result = await session.execute(
        text(
            "SELECT t.task_id, t.title, t.open_at, t.close_at, t.period_date, tf.form_id"
            " FROM form.task t JOIN form.task_form tf ON tf.task_id = t.task_id"
            " WHERE t.recurrence_id = (SELECT recurrence_id FROM form.task WHERE task_id = :t)"
            " AND t.task_id <> :t ORDER BY t.period_date"
        ),
        {"t": template},
    )
    return list(result)


async def test_a_run_opens_each_due_round_with_a_copy_of_the_form(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
) -> None:
    # Open 08:00 to 17:00 the next day: at 00:05 on the 3rd both rounds are still open.
    form_id, template = await _repeating(db_session, researcher, closeAt="2026-11-02T17:00:00")
    question = (await detail.load_form(db_session, form_id)).sections[0].questions[0]  # type: ignore[union-attr]
    await insert(
        db_session, "form.question_visibility", question_id=question.question_id, role="farmer"
    )

    result = await recurrence.run(db_session, datetime(2026, 11, 3, 0, 5))

    assert (result.created, result.failed) == (2, 0)
    rounds = await _rounds(db_session, template)
    assert [(r.title, r.period_date, r.open_at, r.close_at) for r in rounds] == [
        ("รายวัน", date(2026, 11, 2), datetime(2026, 11, 2, 8), datetime(2026, 11, 3, 17)),
        ("รายวัน", date(2026, 11, 3), datetime(2026, 11, 3, 8), datetime(2026, 11, 4, 17)),
    ]
    copy = await detail.load_form(db_session, rounds[0].form_id)
    assert copy is not None and copy.form_id != form_id
    copied = copy.sections[0].questions[0]
    assert (copied.label, copied.field_name) == ("วันที่", "harvest_date")
    assert copied.question_id != question.question_id
    seen_by = await db_session.execute(
        text("SELECT role FROM form.question_visibility WHERE question_id = :q"),
        {"q": copied.question_id},
    )
    assert list(seen_by.scalars()) == ["farmer"]

    again = await recurrence.run(db_session, datetime(2026, 11, 3, 12))
    assert (again.created, again.already_existed) == (0, 2)  # safe to run twice


async def test_a_round_already_over_is_skipped(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
) -> None:
    _form, template = await _repeating(db_session, researcher)

    result = await recurrence.run(db_session, datetime(2026, 11, 3, 18))  # the 3rd is over

    assert (result.created, result.skipped_closed) == (0, 2)
    assert await _rounds(db_session, template) == []


async def test_rounds_keep_their_people_and_their_reminders(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
) -> None:
    person = await insert(db_session, "auth.user_account", username=f"p-{uuid4()}")
    _form, template = await _repeating(
        db_session,
        researcher,
        recipients=[{"type": "USER", "id": str(person)}],
        reminder={"enabled": True, "timeOfDay": "07:00:00"},
    )

    await recurrence.run(db_session, datetime(2026, 11, 2, 1))

    (round_,) = await _rounds(db_session, template)
    assert await db.tasks.assignee_ids(db_session, round_.task_id) == [person]
    (schedule,) = await db.notify.list_schedules_for_task(db_session, round_.task_id)
    assert (schedule.time_of_day, schedule.created_by) == (time(7), researcher.user_id)


async def test_recipients_that_name_nobody_any_more_open_no_round(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
) -> None:
    role = await insert(db_session, "auth.role", role_name=f"r-{uuid4().hex[:6]}")
    holder = await insert(db_session, "auth.user_account", username=f"h-{uuid4()}")
    await insert(db_session, "auth.user_role", user_id=holder, role_id=role)
    _form, template = await _repeating(
        db_session, researcher, recipients=[{"type": "ROLE", "id": str(role)}]
    )
    await db_session.execute(text("DELETE FROM auth.user_role WHERE role_id = :r"), {"r": role})

    result = await recurrence.run(db_session, datetime(2026, 11, 2, 1))

    assert (result.created, result.skipped_no_recipients) == (0, 1)
    assert await _rounds(db_session, template) == []


async def test_a_template_without_times_is_counted_as_failed(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
) -> None:
    _form, template = await _repeating(db_session, researcher)
    await db_session.execute(
        text("UPDATE form.task SET close_at = NULL WHERE task_id = :t"), {"t": template}
    )

    assert (await recurrence.run(db_session, datetime(2026, 11, 2, 1))).failed == 1
