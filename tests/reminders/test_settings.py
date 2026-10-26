"""reminders.settings against real Postgres: a form's reminder read, turned on
(created or updated), turned off (keeping its time and recipients), and the
recipient pickers. Rolled back after each test. The answers against Kotlin's
are in tests/contract/ (K-17..K-20)."""

from datetime import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.actor import Actor
from src.exceptions import Forbidden, FormNotFound, Invalid
from src.reminders import settings
from src.reminders.schemas import FormReminderIn
from tests.db.conftest import db_session, insert  # noqa: F401 -- the fixture


async def _user(session: AsyncSession, name: str) -> UUID:
    user_id = await insert(session, "auth.user_account", username=name)
    assert user_id is not None
    return user_id


@pytest.fixture
async def researcher(db_session: AsyncSession) -> Actor:  # noqa: F811
    me = await _user(db_session, f"researcher-{uuid4().hex[:6]}")
    return Actor(me, frozenset({"researcher"}), frozenset({"create:form:all"}), "api")


@pytest.fixture
async def form(db_session: AsyncSession) -> tuple[UUID, UUID]:  # noqa: F811
    task = await insert(db_session, "form.task", title="probe")
    form_id = await insert(db_session, "form.task_form", task_id=task, handler="harvest", version=1)
    assert task and form_id
    return form_id, task


def _body(enabled: bool, at: time = time(7, 30), **more: object) -> FormReminderIn:
    return FormReminderIn.model_validate({"enabled": enabled, "timeOfDay": at, **more})


async def test_a_form_without_a_reminder_reads_as_off(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
    form: tuple[UUID, UUID],
) -> None:
    out = await settings.form_reminder(db_session, researcher, form[0])
    assert out.model_dump(by_alias=True) == {
        "scheduleId": None,
        "enabled": False,
        "timeOfDay": None,
        "recipients": [],
    }


async def test_on_creates_it_off_keeps_it_on_again_restores_it(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
    form: tuple[UUID, UUID],
) -> None:
    role = await insert(db_session, "auth.role", role_name=f"probe-{uuid4().hex[:6]}")
    person = await _user(db_session, f"person-{uuid4().hex[:6]}")
    recipients = [{"type": "ROLE", "id": str(role)}, {"type": "USER", "id": str(person)}]

    on = await settings.save_form_reminder(
        db_session, researcher, form[0], _body(True, recipients=recipients)
    )
    off = await settings.save_form_reminder(db_session, researcher, form[0], _body(False, time(9)))
    again = await settings.save_form_reminder(
        db_session, researcher, form[0], _body(True, time(6), recipients=recipients[:1])
    )

    assert on.enabled and on.time_of_day == time(7, 30)
    assert {(r.type, r.label) for r in on.recipients} == {
        ("ROLE", (await db.auth.role_names(db_session, [role]))[role]),
        ("USER", (await db.auth.usernames(db_session, [person]))[person]),
    }
    assert (off.schedule_id, off.enabled, off.time_of_day) == (on.schedule_id, False, time(7, 30))
    assert len(off.recipients) == 2  # switched off, not cleared
    assert (again.schedule_id, again.enabled, again.time_of_day) == (on.schedule_id, True, time(6))
    assert [r.id for r in again.recipients] == [role]
    assert len(await db.notify.list_schedules_for_task(db_session, form[1])) == 1


async def test_turning_off_a_form_that_has_none_changes_nothing(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
    form: tuple[UUID, UUID],
) -> None:
    out = await settings.save_form_reminder(db_session, researcher, form[0], _body(False))
    assert out.schedule_id is None
    assert await db.notify.list_schedules_for_task(db_session, form[1]) == []


async def test_an_active_schedule_is_the_one_shown(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
    form: tuple[UUID, UUID],
) -> None:
    for at, active in ((time(6), False), (time(8), True)):
        await insert(
            db_session, "notify.reminder_schedule", task_id=form[1], cadence="DAILY",
            time_of_day=at, is_active=active, created_by=researcher.user_id,
        )  # fmt: skip
    assert (await settings.form_reminder(db_session, researcher, form[0])).time_of_day == time(8)


async def test_unknown_form_too_many_recipients_and_no_permission(
    db_session: AsyncSession,  # noqa: F811
    researcher: Actor,
    form: tuple[UUID, UUID],
) -> None:
    with pytest.raises(FormNotFound):
        await settings.form_reminder(db_session, researcher, uuid4())
    many = [{"type": "USER", "id": str(uuid4())} for _ in range(201)]
    with pytest.raises(Invalid) as e:
        await settings.save_form_reminder(
            db_session, researcher, form[0], _body(True, recipients=many)
        )
    assert "recipients" in e.value.fields
    reader = Actor(researcher.user_id, frozenset(), frozenset({"read:form:all"}), "api")
    with pytest.raises(Forbidden):
        await settings.form_reminder(db_session, reader, form[0])
    with pytest.raises(Forbidden):
        await settings.user_options(db_session, reader, "")


async def test_role_and_user_pickers(db_session: AsyncSession, researcher: Actor) -> None:  # noqa: F811
    tag = uuid4().hex[:6]
    role = await insert(db_session, "auth.role", role_name=f"a-{tag}")
    picked = await _user(db_session, f"Picker_{tag}")
    other = await _user(db_session, f"pickerX{tag}")
    await insert(db_session, "auth.user_role", user_id=picked, role_id=role)

    roles = await settings.role_options(db_session, researcher)
    found = await settings.user_options(db_session, researcher, f"  PICKER_{tag} ")
    by_like = await settings.user_options(db_session, researcher, f"picker{tag}")

    assert [r.role_name for r in roles] == sorted(r.role_name for r in roles)
    assert [(u.user_id, u.roles) for u in found] == [
        (picked, [f"a-{tag}"])
    ]  # "_" is not a wildcard
    assert by_like == []
    assert other not in {u.user_id for u in found}
    assert (
        len(await settings.user_options(db_session, researcher, "")) <= settings.USER_SEARCH_LIMIT
    )
