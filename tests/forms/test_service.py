"""forms.service with the db layer faked: who may read, not-found, the stats
view and its paging rules. The SQL is covered in tests/db/, the whole route
against Kotlin's answers in tests/contract/ (K-10, K-11, K-21, K-22)."""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src import db
from src.actor import Actor
from src.db.forms import FormListRow, TaskForm
from src.db.tasks import TaskInfo
from src.exceptions import Forbidden, Invalid, NotFound
from src.forms import service
from src.forms.schemas import FormOut, FormSummaryOut, FormTaskOut

RESEARCHER = frozenset({"read:form:all", "read:task:all"})
FARMER = frozenset({"read:form:assigned", "read:response:own"})
SESSION: Any = object()  # never touched: every db call is faked


def _actor(perms: frozenset[str], user_id: UUID | None = None) -> Actor:
    return Actor(user_id=user_id or uuid4(), roles=frozenset(), perms=perms, channel="api")


def _task(task_id: UUID | None = None) -> TaskInfo:
    return TaskInfo(
        task_id=task_id or uuid4(),
        title="บันทึกยอดส่งโกโก้",
        description="",
        task_type="FORM",
        open_at=datetime(2026, 9, 1),
        close_at=None,
    )


def _form(task_id: UUID) -> TaskForm:
    return TaskForm(
        task_id=task_id, form_id=uuid4(), version=1, handler="harvest", is_multiple_submit=False
    )


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {
        "forms_with_sections": AsyncMock(return_value=[]),
        "form_by_id": AsyncMock(return_value=None),
        "forms_of_tasks": AsyncMock(return_value={}),
        "all_tasks": AsyncMock(return_value=[]),
        "task_info": AsyncMock(return_value=None),
        "task_visible_to": AsyncMock(return_value=True),
        "load_form": AsyncMock(return_value=None),
    }
    for name in ("forms_with_sections", "form_by_id", "forms_of_tasks"):
        monkeypatch.setattr(db.forms, name, fakes[name])
    for name in ("all_tasks", "task_info", "task_visible_to"):
        monkeypatch.setattr(db.tasks, name, fakes[name])
    monkeypatch.setattr(service, "load_form", fakes["load_form"])
    return fakes


# ---- list_for -------------------------------------------------------------------


async def test_list_gives_every_form_with_a_section(fake_db: dict[str, AsyncMock]) -> None:
    row = FormListRow(
        form_id=uuid4(),
        task_id=uuid4(),
        title="t",
        content="",
        handler="harvest",
        is_multiple_submit=False,
        description=None,
        created_at=datetime(2026, 8, 25, 3),
    )
    fake_db["forms_with_sections"].return_value = [row]

    out = await service.list_for(SESSION, _actor(RESEARCHER))

    assert out == [FormSummaryOut.model_validate(row, from_attributes=True)]
    fake_db["all_tasks"].assert_not_awaited()


async def test_stats_lists_every_task_with_its_form_id(fake_db: dict[str, AsyncMock]) -> None:
    with_form, without = _task(), _task()
    form = _form(with_form.task_id)
    fake_db["all_tasks"].return_value = [with_form, without]
    fake_db["forms_of_tasks"].return_value = {with_form.task_id: form}

    out = await service.list_for(SESSION, _actor(RESEARCHER), include_stats=True, page=2, size=10)

    assert [o.form_id for o in out if isinstance(o, FormTaskOut)] == [form.form_id, None]
    fake_db["all_tasks"].assert_awaited_once_with(SESSION, limit=10, offset=20)


async def test_admins_get_the_task_view_too(fake_db: dict[str, AsyncMock]) -> None:
    # K-21 asked for read:task:all (researchers only); FORM-01 asks for read:form:all.
    await service.list_for(SESSION, _actor(frozenset({"read:form:all"})), include_stats=True)
    fake_db["all_tasks"].assert_awaited_once()


@pytest.mark.parametrize("include_stats", [False, True])
async def test_list_is_for_researchers(fake_db: dict[str, AsyncMock], include_stats: bool) -> None:
    with pytest.raises(Forbidden):
        await service.list_for(SESSION, _actor(FARMER), include_stats=include_stats)


@pytest.mark.parametrize(
    ("page", "size", "field"), [(-1, 50, "page"), (0, 0, "size"), (0, 501, "size")]
)
async def test_stats_paging_out_of_range_is_invalid(
    fake_db: dict[str, AsyncMock], page: int, size: int, field: str
) -> None:
    with pytest.raises(Invalid) as e:
        await service.list_for(
            SESSION, _actor(RESEARCHER), include_stats=True, page=page, size=size
        )
    assert field in e.value.fields
    fake_db["all_tasks"].assert_not_awaited()


# ---- get --------------------------------------------------------------------------


def _form_out(form_id: UUID) -> FormOut:
    return FormOut(
        form_id=form_id, title="t", description=None, is_multiple_submit=False, sections=[]
    )


async def test_researcher_reads_any_form(fake_db: dict[str, AsyncMock]) -> None:
    form = _form(uuid4())
    fake_db["form_by_id"].return_value = form
    fake_db["load_form"].return_value = _form_out(form.form_id)

    out = await service.get(SESSION, _actor(RESEARCHER), form.form_id)

    assert isinstance(out, FormOut) and out.form_id == form.form_id
    fake_db["task_visible_to"].assert_not_awaited()


async def test_farmer_reads_a_form_of_their_task(fake_db: dict[str, AsyncMock]) -> None:
    user_id, form = uuid4(), _form(uuid4())
    fake_db["form_by_id"].return_value = form
    fake_db["load_form"].return_value = _form_out(form.form_id)

    await service.get(SESSION, _actor(FARMER, user_id), form.form_id)

    fake_db["task_visible_to"].assert_awaited_once_with(SESSION, user_id, form.task_id)


async def test_farmer_cant_see_a_form_assigned_to_someone_else(
    fake_db: dict[str, AsyncMock],
) -> None:
    fake_db["form_by_id"].return_value = _form(uuid4())
    fake_db["task_visible_to"].return_value = False

    with pytest.raises(NotFound):
        await service.get(SESSION, _actor(FARMER), uuid4())
    fake_db["load_form"].assert_not_awaited()


async def test_unknown_form_is_not_found(fake_db: dict[str, AsyncMock]) -> None:
    with pytest.raises(NotFound):
        await service.get(SESSION, _actor(RESEARCHER), uuid4())


async def test_no_form_permission_is_forbidden(fake_db: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden):
        await service.get(SESSION, _actor(frozenset({"read:profile:own"})), uuid4())


async def test_stats_gives_the_forms_task(fake_db: dict[str, AsyncMock]) -> None:
    task = _task()
    form = _form(task.task_id)
    fake_db["form_by_id"].return_value = form
    fake_db["task_info"].return_value = task

    out = await service.get(SESSION, _actor(RESEARCHER), form.form_id, include_stats=True)

    assert out == FormTaskOut(
        form_id=form.form_id,
        task_id=task.task_id,
        title=task.title,
        description=task.description,
        task_type="FORM",
        open_at=task.open_at,
        close_at=None,
    )
    fake_db["load_form"].assert_not_awaited()


async def test_stats_is_for_researchers(fake_db: dict[str, AsyncMock]) -> None:
    with pytest.raises(Forbidden):
        await service.get(SESSION, _actor(FARMER), uuid4(), include_stats=True)
    fake_db["form_by_id"].assert_not_awaited()
