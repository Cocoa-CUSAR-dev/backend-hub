import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.chat import reuse, service
from src.chat.constants import AnswerSource
from src.forms.schemas import FormDetail


def _form_with(*question_dicts: dict[str, object]) -> list[service.Question]:
    return service.questions_from_form(
        FormDetail(task_form_id="tf-reuse", sections=[{"questions": list(question_dicts)}])
    )


def _varchar(field_name: str, *, mandatory: bool = True, sort_order: int = 0) -> dict[str, object]:
    return {
        "question_id": str(uuid.uuid4()),
        "label": field_name,
        "field_name": field_name,
        "input_type": "VARCHAR",
        "is_mandatory": mandatory,
        "sort_order": sort_order,
    }


def _option(field_name: str, choice_ids: list[str], *, sort_order: int = 0) -> dict[str, object]:
    return {
        "question_id": str(uuid.uuid4()),
        "label": field_name,
        "field_name": field_name,
        "input_type": "OPTION",
        "is_mandatory": True,
        "sort_order": sort_order,
        "choices": [{"id": cid, "name": f"label-{cid}"} for cid in choice_ids],
    }


def _boolean(field_name: str, *, sort_order: int = 0) -> dict[str, object]:
    return {
        "question_id": str(uuid.uuid4()),
        "label": field_name,
        "field_name": field_name,
        "input_type": "BOOLEAN",
        "is_mandatory": True,
        "sort_order": sort_order,
    }


async def _sanitize(raw: dict[str, Any], questions: list[service.Question]) -> dict[str, Any]:
    with patch(
        "src.tasks.autofill.registry.current",
        AsyncMock(return_value=SimpleNamespace(parent_fields={"farm_activity_id"})),
    ):
        return await reuse.sanitize_for_autofill(MagicMock(), raw, questions)


class TestLastAnswer:
    async def test_returns_the_answer(self) -> None:
        actor = MagicMock(can=MagicMock(return_value=True))
        found = SimpleNamespace(answer={"note": "old"})
        with (
            patch("src.chat.reuse.load_actor", AsyncMock(return_value=actor)),
            patch("src.chat.reuse.autofill.last_answer", AsyncMock(return_value=found)),
        ):
            assert await reuse.last_answer(MagicMock(), uuid.uuid4(), "notes") == {"note": "old"}

    async def test_no_earlier_answer_is_no_offer(self) -> None:
        actor = MagicMock(can=MagicMock(return_value=True))
        with (
            patch("src.chat.reuse.load_actor", AsyncMock(return_value=actor)),
            patch("src.chat.reuse.autofill.last_answer", AsyncMock(return_value=None)),
        ):
            assert await reuse.last_answer(MagicMock(), uuid.uuid4(), "notes") is None

    async def test_without_the_permission_is_no_offer_not_an_error(self) -> None:
        actor = MagicMock(can=MagicMock(return_value=False))
        reader = AsyncMock()
        with (
            patch("src.chat.reuse.load_actor", AsyncMock(return_value=actor)),
            patch("src.chat.reuse.autofill.last_answer", reader),
        ):
            assert await reuse.last_answer(MagicMock(), uuid.uuid4(), "notes") is None
        reader.assert_not_awaited()


class TestSanitizeForAutofill:
    """The chatbot's questions reach the shared rules correctly."""

    async def test_plain_text_is_kept(self) -> None:
        assert await _sanitize({"note": "hello"}, _form_with(_varchar("note"))) == {"note": "hello"}

    async def test_task_id_and_parent_ids_are_dropped(self) -> None:
        raw = {"note": "hello", "task_id": "t-1", "farm_activity_id": "f-1"}
        assert await _sanitize(raw, _form_with(_varchar("note"))) == {"note": "hello"}

    async def test_a_current_choice_is_kept_and_a_stale_one_dropped(self) -> None:
        keep = str(uuid.uuid4())
        questions = _form_with(_option("fertilizer_id", [keep]))
        assert await _sanitize({"fertilizer_id": keep}, questions) == {"fertilizer_id": keep}
        assert await _sanitize({"fertilizer_id": "stale"}, questions) == {}

    async def test_pause_and_skip_are_not_real_choices(self) -> None:
        questions = _form_with(_option("fertilizer_id", [str(uuid.uuid4())]))
        assert await _sanitize({"fertilizer_id": "__pause__"}, questions) == {}
        assert await _sanitize({"fertilizer_id": "__skip__"}, questions) == {}

    async def test_a_boolean_keeps_true_and_false_only(self) -> None:
        questions = _form_with(_boolean("is_quality_damage"))
        assert await _sanitize({"is_quality_damage": "true"}, questions) == {
            "is_quality_damage": "true"
        }
        assert await _sanitize({"is_quality_damage": "maybe"}, questions) == {}


class TestBuildAnswerRows:
    def test_free_text_field_becomes_a_text_only_answer(self) -> None:
        questions = _form_with(_varchar("note"))
        conversation_id = uuid.uuid4()

        rows = reuse.build_answer_rows(conversation_id, {"note": "hello"}, questions)

        assert len(rows) == 1
        row = rows[0]
        assert row.conversation_id == conversation_id
        assert row.question_id == questions[0].question_id
        assert row.answer == {"text": "hello"}
        assert row.source == AnswerSource.GUIDED_FLOW

    def test_option_field_resolves_id_to_its_current_label(self) -> None:
        choice_id = str(uuid.uuid4())
        questions = _form_with(_option("fertilizer_id", [choice_id]))

        rows = reuse.build_answer_rows(uuid.uuid4(), {"fertilizer_id": choice_id}, questions)

        assert len(rows) == 1
        assert rows[0].answer == {"text": f"label-{choice_id}", "value": choice_id}

    def test_field_with_no_matching_question_is_skipped(self) -> None:
        questions = _form_with(_varchar("note"))

        rows = reuse.build_answer_rows(uuid.uuid4(), {"retired_field": "x"}, questions)

        assert rows == []

    def test_multiple_fields_each_get_their_own_row(self) -> None:
        choice_id = str(uuid.uuid4())
        questions = _form_with(
            _varchar("note", sort_order=0),
            _option("fertilizer_id", [choice_id], sort_order=1),
        )

        rows = reuse.build_answer_rows(
            uuid.uuid4(), {"note": "ok", "fertilizer_id": choice_id}, questions
        )

        assert len(rows) == 2
        question_ids = {row.question_id for row in rows}
        assert question_ids == {q.question_id for q in questions}


class TestFormatAutofillPreview:
    """The offer message itself shows this -- a farmer should see what's
    being offered before agreeing, not just find out after tapping "ใช้
    ข้อมูลเดิม" (live feedback: a blind yes/no prompt left no way to make an
    informed choice).
    """

    def test_shows_label_and_value_per_field(self) -> None:
        questions = _form_with(_varchar("note", sort_order=0))

        preview = reuse.format_autofill_preview({"note": "สวัสดี"}, questions)

        assert preview == "- note: สวัสดี"

    def test_orders_by_sort_order_not_dict_order(self) -> None:
        questions = _form_with(_varchar("first", sort_order=0), _varchar("second", sort_order=1))
        # Insert "second" first in the dict -- preview must still lead with
        # "first", matching sort_order rather than iteration order.
        sanitized = {"second": "b", "first": "a"}

        preview = reuse.format_autofill_preview(sanitized, questions)

        assert preview == "- first: a\n- second: b"

    def test_option_value_shows_its_current_label_not_the_raw_id(self) -> None:
        choice_id = str(uuid.uuid4())
        questions = _form_with(_option("fertilizer_id", [choice_id]))

        preview = reuse.format_autofill_preview({"fertilizer_id": choice_id}, questions)

        assert preview == f"- fertilizer_id: label-{choice_id}"

    def test_field_with_no_matching_question_is_skipped(self) -> None:
        questions = _form_with(_varchar("note"))

        preview = reuse.format_autofill_preview({"retired_field": "x"}, questions)

        assert preview == ""


class TestSanitizeThenBuildIntegration:
    async def test_full_pipeline_from_raw_answer_to_answer_rows(self) -> None:
        keep_option_id = str(uuid.uuid4())
        questions = _form_with(
            _varchar("note", sort_order=0),
            _option("fertilizer_id", [keep_option_id], sort_order=1),
        )
        sanitized = await _sanitize(
            {
                "task_id": str(uuid.uuid4()),
                "farm_activity_id": str(uuid.uuid4()),
                "note": "sprayed at dawn",
                "fertilizer_id": "stale",
            },
            questions,
        )
        rows = reuse.build_answer_rows(uuid.uuid4(), sanitized, questions)

        assert len(rows) == 1
        assert rows[0].answer == {"text": "sprayed at dawn"}


class TestGeodataIsNeverReused:
    """ "ตำแหน่งปัจจุบัน" means where the farmer is now -- a previous visit's
    location must never be offered or prefilled.
    """

    @staticmethod
    def _gis() -> dict[str, object]:
        return {
            "question_id": str(uuid.uuid4()),
            "label": "ตำแหน่งปัจจุบัน",
            "field_name": "gis",
            "input_type": "GEODATA",
            "is_mandatory": False,
            "sort_order": 1,
        }

    async def test_previous_location_is_dropped_from_the_offer(self) -> None:
        questions = _form_with(_varchar("note"), self._gis())
        result = await _sanitize({"note": "ใส่ปุ๋ย", "gis": [{"lat": 13.75, "lng": 100.5}]}, questions)

        assert result == {"note": "ใส่ปุ๋ย"}
        assert "ตำแหน่ง" not in reuse.format_autofill_preview(result, questions)
        rows = reuse.build_answer_rows(uuid.uuid4(), result, questions)
        assert [row.answer for row in rows] == [{"text": "ใส่ปุ๋ย"}]
