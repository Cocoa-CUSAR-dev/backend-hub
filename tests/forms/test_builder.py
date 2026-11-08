"""forms.builder against real Postgres: what create / update / edit write, and
what they refuse. Rolled back after each test (tests/db/conftest.py). The
answers against Kotlin's are in tests/contract/ (K-12, K-13, K-14)."""

from collections.abc import AsyncIterator
from datetime import date, datetime, time
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.actor import Actor
from src.exceptions import Forbidden, FormNotFound, Invalid
from src.forms import builder, detail, registry
from src.forms.schemas import FormCreateIn, FormEditIn, FormUpdateIn
from tests.db.conftest import db_session, insert  # noqa: F401 -- the fixture

PERMS = frozenset({"create:form:all", "update:form:all", "read:form:all"})


@pytest.fixture(autouse=True)
def _fresh_registry() -> AsyncIterator[None]:
    registry.reset_cache()
    detail.reset_cache()
    yield
    registry.reset_cache()
    detail.reset_cache()


@pytest.fixture
async def researcher(db_session: AsyncSession) -> Actor:  # noqa: F811
    user_id = await insert(db_session, "auth.user_account", username=f"researcher-{uuid4()}")
    assert user_id is not None
    return Actor(user_id=user_id, roles=frozenset({"researcher"}), perms=PERMS, channel="api")


@pytest.fixture
async def probe(db_session: AsyncSession) -> AsyncSession:  # noqa: F811
    """A handler of our own whose table has a NOT NULL column and a dependency."""
    await db_session.execute(
        text(
            "CREATE TABLE agriculture.probe_record ("
            " probe_record_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,"
            " farm_id uuid NOT NULL, plot_id uuid, amount numeric, note varchar)"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO form.handler_registry (handler, schema_name, table_name, is_active)"
            " VALUES ('probe_record', 'agriculture', 'probe_record', true)"
        )
    )
    await insert(
        db_session, "form.field_dependency", child_field="plot_id", parent_field="farm_id",
        link_table="agriculture.plot", link_column="farm_id", label_th="แปลงในฟาร์ม",
    )  # fmt: skip
    return db_session


def _question(field: str | None, kind: str = "OPTION", order: int = 0, **more: Any) -> dict:
    return {
        "label": f"ถาม {field}",
        "inputType": kind,
        "fieldName": field,
        "sortOrder": order,
        **more,
    }


def _create(**more: Any) -> FormCreateIn:
    body = {
        "title": "ฟอร์มทดสอบ",
        "description": "จากเทสต์",
        "openAt": "2026-11-01T00:00:00",
        "closeAt": "2026-11-30T16:59:59",
        "handler": "probe_record",
        "sections": [
            {
                "title": "ส่วนแรก",
                "sortOrder": 0,
                "questions": [_question("farm_id", order=0), _question("amount", "FLOAT", 1)],
            }
        ],
    }
    return FormCreateIn.model_validate(body | more)


async def _row(session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await session.execute(text(sql), params)).first()


AMOUNT_ONLY = _question("amount", "FLOAT")
NOBODY = UUID("00000000-0000-4000-8000-000000000000")


# ---- create ---------------------------------------------------------------------------


async def test_create_writes_the_task_form_sections_and_questions(
    probe: AsyncSession, researcher: Actor
) -> None:
    form = await builder.create_form(probe, researcher, _create())

    stored = await db.forms.form_by_id(probe, form.form_id)
    assert stored is not None and stored.handler == "probe_record"
    task = await _row(
        probe,
        "SELECT title, description, task_type, open_at, close_at, recurrence_id"
        " FROM form.task WHERE task_id = :t",
        t=stored.task_id,
    )
    assert tuple(task) == (
        "ฟอร์มทดสอบ", "จากเทสต์", "FORM", datetime(2026, 11, 1), datetime(2026, 11, 30, 16, 59, 59),
        None,
    )  # fmt: skip
    assert [s.title for s in form.sections] == ["ส่วนแรก"]
    assert [(q.field_name, q.input_type, q.is_active) for q in form.sections[0].questions] == [
        ("farm_id", "OPTION", True),
        ("amount", "FLOAT", True),
    ]
    assert (
        await _row(probe, "SELECT 1 FROM form.assignment WHERE task_id = :t", t=stored.task_id)
        is None
    )


async def test_create_with_recipients_a_weekly_repeat_and_a_reminder(
    probe: AsyncSession, researcher: Actor
) -> None:
    role = await insert(probe, "auth.role", role_name=f"probe-{uuid4().hex[:6]}")
    member = await insert(probe, "auth.user_account", username=f"m-{uuid4()}")
    named = await insert(probe, "auth.user_account", username=f"n-{uuid4()}")
    await insert(probe, "auth.user_role", user_id=member, role_id=role)
    recipients = [{"type": "ROLE", "id": str(role)}, {"type": "USER", "id": str(named)}]

    form = await builder.create_form(
        probe,
        researcher,
        _create(
            recipients=[*recipients, recipients[1]],  # a repeated rule is saved once
            recurrence={"frequency": "WEEKLY", "weekdays": [4, 1, 4], "untilDate": "2026-12-31"},
            reminder={"enabled": True, "timeOfDay": "07:30:00", "recipients": recipients[:1]},
        ),
    )

    task_id = (await db.forms.form_by_id(probe, form.form_id)).task_id  # type: ignore[union-attr]
    rules = (
        await probe.execute(
            text(
                "SELECT recipient_type, role_id, user_id FROM form.task_recipient"
                " WHERE task_id = :t ORDER BY recipient_type"
            ),
            {"t": task_id},
        )
    ).all()
    assert [tuple(r) for r in rules] == [("ROLE", role, None), ("USER", None, named)]
    assigned = await db.tasks.assignee_ids(probe, task_id)
    assert set(assigned) == {member, named}
    recurrence = await _row(
        probe,
        "SELECT r.frequency, r.weekdays, r.start_date, r.until_date, t.period_date"
        " FROM form.task t JOIN form.task_recurrence r ON r.recurrence_id = t.recurrence_id"
        " WHERE t.task_id = :t AND r.template_task_id = :t",
        t=task_id,
    )
    assert tuple(recurrence) == (
        "WEEKLY", [1, 4], date(2026, 11, 1), date(2026, 12, 31), date(2026, 11, 1),
    )  # fmt: skip
    (schedule,) = await db.notify.list_schedules_for_task(probe, task_id)
    assert (schedule.time_of_day, schedule.created_by) == (time(7, 30), researcher.user_id)


async def test_a_daily_repeat_keeps_no_weekdays(probe: AsyncSession, researcher: Actor) -> None:
    form = await builder.create_form(
        probe, researcher, _create(recurrence={"frequency": "DAILY", "weekdays": [1]})
    )
    task_id = (await db.forms.form_by_id(probe, form.form_id)).task_id  # type: ignore[union-attr]
    row = await _row(
        probe,
        "SELECT weekdays, until_date FROM form.task_recurrence WHERE template_task_id = :t",
        t=task_id,
    )
    assert tuple(row) == (None, None)


async def test_a_reminder_that_fails_doesnt_undo_the_form(
    probe: AsyncSession, researcher: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_a: object, **_k: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(builder.reminders, "create_schedule", broken)

    form = await builder.create_form(
        probe, researcher, _create(reminder={"enabled": True, "timeOfDay": "07:30:00"})
    )

    assert await db.forms.form_by_id(probe, form.form_id) is not None


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"sections": []}, "sections"),
        ({"sections": [{"title": "ว่าง", "sortOrder": 0, "questions": []}]}, "sections"),
        ({"title": "  "}, "title"),
        ({"handler": "no_such_handler"}, "handler"),
        # farm_id is NOT NULL: a form that never asks it can never be saved
        (
            {"sections": [{"title": "s", "sortOrder": 0, "questions": [AMOUNT_ONLY]}]},
            "sections",
        ),
        # plot_id depends on farm_id, which comes after it
        (
            {"sections": [{"title": "s", "sortOrder": 0, "questions": [
                _question("plot_id", order=0), _question("farm_id", order=1)]}]},
            "sections",
        ),
        ({"closeAt": "2026-10-01T00:00:00", "recurrence": {"frequency": "DAILY"}}, "closeAt"),
        ({"recurrence": {"frequency": "WEEKLY", "weekdays": []}}, "recurrence"),
        ({"recurrence": {"frequency": "WEEKLY", "weekdays": [0, 8]}}, "recurrence"),
        ({"recurrence": {"frequency": "DAILY", "untilDate": "2026-10-31"}}, "recurrence"),
        ({"recipients": [{"type": "USER", "id": str(NOBODY)}]}, "recipients"),
    ],
)  # fmt: skip
async def test_create_refuses_a_form_that_cant_work(
    probe: AsyncSession, researcher: Actor, change: dict, field: str
) -> None:
    with pytest.raises(Invalid) as e:
        await builder.create_form(probe, researcher, _create(**change))
    assert field in e.value.fields


async def test_recipients_that_name_nobody_are_refused(
    probe: AsyncSession, researcher: Actor
) -> None:
    empty_role = await insert(probe, "auth.role", role_name=f"empty-{uuid4().hex[:6]}")
    with pytest.raises(Invalid) as e:
        await builder.create_form(
            probe, researcher, _create(recipients=[{"type": "ROLE", "id": str(empty_role)}])
        )
    assert e.value.detail == "The chosen recipients do not match any user"


async def test_every_definition_problem_is_reported_at_once(
    probe: AsyncSession, researcher: Actor
) -> None:
    questions = [_question("farm_id", "VARCHAR"), _question("note", "OPTION", 1)]
    with pytest.raises(Invalid) as e:
        await builder.create_form(
            probe,
            researcher,
            _create(sections=[{"title": "s", "sortOrder": 0, "questions": questions}]),
        )
    assert len(e.value.fields["sections"].splitlines()) == 2


async def test_creating_needs_create_form_all(probe: AsyncSession, researcher: Actor) -> None:
    reader = Actor(researcher.user_id, frozenset(), frozenset({"update:form:all"}), "api")
    with pytest.raises(Forbidden):
        await builder.create_form(probe, reader, _create())


# ---- update ---------------------------------------------------------------------------


async def _existing(session: AsyncSession, researcher: Actor) -> Any:
    return await builder.create_form(
        session,
        researcher,
        _create(
            isMultipleSubmit=True,
            sections=[
                {
                    "title": "หนึ่ง",
                    "sortOrder": 0,
                    "questions": [
                        _question("farm_id", order=0, carryForward=True),
                        _question("amount", "FLOAT", 1),
                    ],
                },
                {"title": "สอง", "sortOrder": 1, "questions": [_question("note", "VARCHAR")]},
            ],
        ),
    )


async def test_update_rewrites_adds_and_deletes(probe: AsyncSession, researcher: Actor) -> None:
    form = await _existing(probe, researcher)
    first, second = form.sections
    farm, amount = first.questions

    updated = await builder.update_form(
        probe,
        researcher,
        form.form_id,
        FormUpdateIn.model_validate(
            {
                "description": None,
                "sections": [
                    {
                        "sectionId": str(first.section_id),
                        "title": "หนึ่ง (แก้)",
                        "sortOrder": 0,
                        "questions": [
                            {**_question("farm_id"), "questionId": str(farm.question_id),
                             "label": "ฟาร์มที่ไป"},
                            _question("note", "VARCHAR", 1),  # new
                        ],  # amount left out: deleted
                    },
                    {"sectionId": None, "title": "ใหม่", "sortOrder": 1,
                     "questions": [_question("plot_id", order=0)]},
                ],  # the second section left out: deleted
            }
        ),
    )  # fmt: skip

    assert [s.title for s in updated.sections] == ["หนึ่ง (แก้)", "ใหม่"]
    kept = updated.sections[0].questions
    assert [(q.question_id == farm.question_id, q.label) for q in kept] == [
        (True, "ฟาร์มที่ไป"),
        (False, "ถาม note"),
    ]
    assert kept[0].carry_forward is True  # not sent: kept
    assert amount.question_id not in {q.question_id for s in updated.sections for q in s.questions}
    assert second.section_id not in {s.section_id for s in updated.sections}
    stored = await _row(
        probe,
        "SELECT description, is_multiple_submit FROM form.task_form WHERE form_id = :f",
        f=form.form_id,
    )
    assert tuple(stored) == (None, True)  # description cleared; the flag not sent, so kept


async def test_update_checks_only_what_stays_active(probe: AsyncSession, researcher: Actor) -> None:
    form = await _existing(probe, researcher)
    sections = [
        {
            "title": "s",
            "sortOrder": 0,
            "questions": [_question("farm_id", isActive=False), _question("amount", "FLOAT", 1)],
        }
    ]
    with pytest.raises(Invalid) as e:  # farm_id switched off: the required column isn't asked
        await builder.update_form(
            probe, researcher, form.form_id, FormUpdateIn.model_validate({"sections": sections})
        )
    assert "farm_id" in e.value.fields["sections"]


async def test_update_of_an_unknown_form_is_not_found(
    probe: AsyncSession, researcher: Actor
) -> None:
    body = FormUpdateIn.model_validate({"sections": []})
    with pytest.raises(FormNotFound):
        await builder.update_form(probe, researcher, uuid4(), body)


# ---- edit -----------------------------------------------------------------------------


async def test_edit_switches_only_this_forms_parts(probe: AsyncSession, researcher: Actor) -> None:
    form = await _existing(probe, researcher)
    other = await _existing(probe, researcher)
    farm, amount = form.sections[0].questions
    foreign = other.sections[0].questions[0]

    answer = await builder.edit_form(
        probe,
        researcher,
        form.form_id,
        FormEditIn.model_validate(
            {
                "sections": [
                    {
                        "sectionId": str(form.sections[1].section_id),
                        "isActive": False,
                        "questions": [],
                    },
                    {
                        "sectionId": str(form.sections[0].section_id),
                        "isActive": True,
                        "questions": [
                            {"questionId": str(amount.question_id), "isMandatory": True},
                            {"questionId": str(foreign.question_id), "isActive": False},
                        ],
                    },
                ]
            }
        ),
    )

    assert answer == builder.EDITED
    after = await detail.load_form(probe, form.form_id)
    assert after is not None
    assert [s.is_active for s in after.sections] == [True, False]
    edited = {q.question_id: q for q in after.sections[0].questions}
    assert (edited[amount.question_id].is_mandatory, edited[amount.question_id].is_active) == (
        True,
        True,  # not sent: kept
    )
    assert edited[farm.question_id].is_active is True
    untouched = await detail.load_form(probe, other.form_id)
    assert untouched is not None and untouched.sections[0].questions[0].is_active is True


async def test_edit_of_an_unknown_form_is_not_found(probe: AsyncSession, researcher: Actor) -> None:
    with pytest.raises(FormNotFound):
        await builder.edit_form(probe, researcher, uuid4(), FormEditIn.model_validate({}))
