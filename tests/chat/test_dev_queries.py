import json
import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.chat.dev_queries import (
    _load_choices,
    list_testable_forms,
    load_form_detail,
    resolve_test_user_id,
)
from src.db.auth import UserRow
from src.db.forms import FormRow, TaskForm
from src.db.ref import Choice
from src.db.tasks import TaskBrief
from src.exceptions import ConversationNotFound, FormNotFound


def _task_form(task_id: uuid.UUID, handler: str) -> TaskForm:
    return TaskForm(
        task_id=task_id,
        form_id=uuid.uuid4(),
        version=1,
        handler=handler,
        is_multiple_submit=False,
    )


async def test_list_testable_forms_maps_forms_and_task_titles_into_summaries() -> None:
    task_id = uuid.uuid4()
    form = _task_form(task_id, "harvest")
    with (
        patch("src.db.forms.forms_with_handlers", new=AsyncMock(return_value=[form])) as read,
        patch(
            "src.db.tasks.task_briefs",
            new=AsyncMock(return_value={task_id: TaskBrief("งานทดสอบ", datetime(2026, 9, 1))}),
        ),
    ):
        forms = await list_testable_forms(MagicMock())

    assert read.await_args.args[1] == (
        "harvest",
        "farm_activity",
        "farm_pest_disease_record",
        "processing_record",
    )
    assert len(forms) == 1
    assert (forms[0].task_id, forms[0].task_form_id) == (task_id, form.form_id)
    assert forms[0].title == "งานทดสอบ"
    assert forms[0].handler == "harvest"


async def test_list_testable_forms_orders_by_handler_then_opening_time() -> None:
    early, late, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    forms = [
        _task_form(late, "harvest"),
        _task_form(other, "farm_activity"),
        _task_form(early, "harvest"),
    ]
    briefs = {
        early: TaskBrief("early", datetime(2026, 9, 1)),
        late: TaskBrief("late", datetime(2026, 9, 2)),
        other: TaskBrief("other", None),
    }
    with (
        patch("src.db.forms.forms_with_handlers", new=AsyncMock(return_value=forms)),
        patch("src.db.tasks.task_briefs", new=AsyncMock(return_value=briefs)),
    ):
        listed = await list_testable_forms(MagicMock())

    assert [f.title for f in listed] == ["other", "early", "late"]


async def test_list_testable_forms_skips_a_form_whose_task_is_gone() -> None:
    form = _task_form(uuid.uuid4(), "harvest")
    with (
        patch("src.db.forms.forms_with_handlers", new=AsyncMock(return_value=[form])),
        patch("src.db.tasks.task_briefs", new=AsyncMock(return_value={})),
    ):
        assert await list_testable_forms(MagicMock()) == []


async def test_load_choices_rejects_a_field_name_not_shaped_like_an_id() -> None:
    with patch("src.db.ref.choices", new=AsyncMock()) as read:
        result = await _load_choices(MagicMock(), "not_an_id_field")

    assert result == []
    read.assert_not_called()  # rejected before any SQL runs


async def test_load_choices_reads_the_ref_table_named_after_the_field() -> None:
    with patch(
        "src.db.ref.choices",
        new=AsyncMock(return_value=[Choice(id="f-2", name="ไร่ B"), Choice(id="f-1", name="สวน A")]),
    ) as read:
        result = await _load_choices(MagicMock(), "farm_id")

    assert read.await_args.args[1:] == ("farm_constant", "farm_id", "farm_name")
    assert result == [{"id": "f-1", "name": "สวน A"}, {"id": "f-2", "name": "ไร่ B"}]


async def test_load_choices_is_capped_at_the_quick_reply_limit() -> None:
    many = [Choice(id=str(i), name=f"n{i:02d}") for i in range(30)]
    with patch("src.db.ref.choices", new=AsyncMock(return_value=many)):
        result = await _load_choices(MagicMock(), "farm_id")

    assert [c["name"] for c in result] == [f"n{i:02d}" for i in range(13)]


def _question_row(**overrides: Any) -> FormRow:
    fields: dict[str, Any] = {
        "form_id": uuid.uuid4(),
        "title": None,
        "description": None,
        "is_multiple_submit": False,
        "section_id": uuid.uuid4(),
        "section_title": None,
        "section_description": None,
        "section_sort_order": 0,
        "section_is_active": True,
        "question_id": uuid.uuid4(),
        "label": "จำนวนพัดลม",
        "input_type": "VARCHAR",
        "question_description": None,
        "field_name": "fan_count",
        "default_value": None,
        "is_mandatory": True,
        "question_is_active": True,
        "carry_forward": False,
        "question_sort_order": 0,
        "validation_rule": None,
    }
    fields.update(overrides)
    return FormRow(**fields)


async def test_load_form_detail_raises_form_not_found_when_no_questions_exist() -> None:
    # a form with a section but no questions comes back as one row with every question_* None
    empty = _question_row(question_id=None, label=None, field_name=None, input_type=None)
    with (
        patch("src.db.forms.form_rows", new=AsyncMock(return_value=[empty])),
        pytest.raises(FormNotFound),
    ):
        await load_form_detail(MagicMock(), uuid.uuid4())

    with (
        patch("src.db.forms.form_rows", new=AsyncMock(return_value=[])),
        pytest.raises(FormNotFound),
    ):
        await load_form_detail(MagicMock(), uuid.uuid4())


async def test_load_form_detail_attaches_choices_to_option_questions() -> None:
    row = _question_row(label="ฟาร์ม", field_name="farm_id", input_type="OPTION")
    with (
        patch("src.db.forms.form_rows", new=AsyncMock(return_value=[row])),
        patch(
            "src.db.ref.choices",
            new=AsyncMock(return_value=[Choice(id="f-1", name="สวนทดสอบ")]),
        ),
    ):
        form = await load_form_detail(MagicMock(), uuid.uuid4())

    question = form.sections[0]["questions"][0]
    assert question["choices"] == [{"id": "f-1", "name": "สวนทดสอบ"}]


async def test_load_form_detail_orders_questions_by_sort_order() -> None:
    second = _question_row(field_name="b", question_sort_order=2)
    first = _question_row(field_name="a", question_sort_order=1)
    with patch("src.db.forms.form_rows", new=AsyncMock(return_value=[second, first])):
        form = await load_form_detail(MagicMock(), uuid.uuid4())

    assert [q["field_name"] for q in form.sections[0]["questions"]] == ["a", "b"]


async def test_load_form_detail_converts_a_jsonb_validation_rule_to_snake_case() -> None:
    """The driver normally decodes jsonb to a dict -- this is that path."""
    row = _question_row(validation_rule={"type": "INT", "maxLength": 50, "errorMessage": "0-50"})
    with patch("src.db.forms.form_rows", new=AsyncMock(return_value=[row])):
        form = await load_form_detail(MagicMock(), uuid.uuid4())

    rule = form.sections[0]["questions"][0]["validation_rule"]
    assert rule == {"type": "INT", "max_length": 50, "error_message": "0-50"}


async def test_load_form_detail_parses_a_raw_json_string_validation_rule() -> None:
    """Fallback path if jsonb ever arrives as raw text instead of an
    already-decoded dict.
    """
    row = _question_row(validation_rule=json.dumps({"type": "INT", "maxLength": 50}))
    with patch("src.db.forms.form_rows", new=AsyncMock(return_value=[row])):
        form = await load_form_detail(MagicMock(), uuid.uuid4())

    rule = form.sections[0]["questions"][0]["validation_rule"]
    assert rule == {"type": "INT", "max_length": 50}


async def test_resolve_test_user_id_returns_the_seed_users_id() -> None:
    user = UserRow(
        user_id=uuid.uuid4(),
        username="farmer01",
        password_hash=None,
        is_requires_password_reset=False,
        is_password_reset=False,
    )
    with patch("src.db.auth.user_by_username", new=AsyncMock(return_value=user)) as read:
        assert await resolve_test_user_id(MagicMock()) == user.user_id

    assert read.await_args.args[1] == "farmer01"


async def test_resolve_test_user_id_raises_when_seed_user_is_missing() -> None:
    with (
        patch("src.db.auth.user_by_username", new=AsyncMock(return_value=None)),
        pytest.raises(ConversationNotFound),
    ):
        await resolve_test_user_id(MagicMock())
