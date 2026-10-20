"""US2-13 (docs-and-plan#242): farm/plot choices narrowed to the farmer's own
rows, and a dependent question's choices narrowed to its parent's answer.

Kotlin sends the global ref.farm_constant / ref.plot_constant lists, so until
now an ordinary farm or plot question offered every farm and plot in the
system. _scope_question is the one place both narrowings happen; these pin
its rules without a database (the real-DB path is in
tests/integration/test_choice_scoping.py).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from src.conversation import service
from src.conversation.models import ConversationAnswer
from src.forms.schemas import FormDetail
from src.line import plot_picker
from src.line.config import line_settings

FARM_A, FARM_B = "farm-a", "farm-b"


def _form() -> tuple[FormDetail, uuid.UUID, uuid.UUID]:
    """A farm question, then a plot question that depends on it -- the
    choices exactly as forms/client.py delivers Kotlin's payload (parentId
    already snake_cased to parent_id, dependsOn to depends_on)."""
    farm_q, plot_q = uuid.uuid4(), uuid.uuid4()
    questions = [
        {
            "question_id": str(farm_q),
            "label": "ฟาร์ม",
            "field_name": "farm_id",
            "input_type": "OPTION",
            "is_mandatory": True,
            "sort_order": 0,
            "choices": [{"id": FARM_A, "name": "ไร่ A"}, {"id": FARM_B, "name": "ไร่ B"}],
        },
        {
            "question_id": str(plot_q),
            "label": "แปลง",
            "field_name": "plot_id",
            "input_type": "OPTION",
            "is_mandatory": True,
            "sort_order": 1,
            "depends_on": "farm_id",
            "choices": [
                {"id": "a1", "name": "แปลง A1", "parent_id": FARM_A},
                {"id": "a2", "name": "แปลง A2", "parent_id": FARM_A},
                {"id": "b1", "name": "แปลง B1", "parent_id": FARM_B},
            ],
        },
    ]
    return FormDetail(task_form_id="tf", sections=[{"questions": questions}]), farm_q, plot_q


def _question(form: FormDetail, field_name: str) -> service.Question:
    return next(q for q in service.questions_from_form(form) if q.field_name == field_name)


def _answer(question_id: uuid.UUID, value: str | None, **extra: object) -> ConversationAnswer:
    answer: dict[str, object] = {"text": "x", **extra}
    if value is not None:
        answer["value"] = value
    return ConversationAnswer(
        conversation_id=uuid.uuid4(), question_id=question_id, answer=answer, source="guided_flow"
    )


async def _scope(
    form: FormDetail,
    field_name: str,
    *,
    answers: list[ConversationAnswer] | None = None,
    owned: set[str] | None = None,
) -> list[str]:
    conversation = MagicMock(conversation_id=uuid.uuid4(), user_id=uuid.uuid4())
    with (
        patch.object(service, "_answered_rows", new=AsyncMock(return_value=answers or [])),
        patch.object(plot_picker, "farmer_owned_ids", new=AsyncMock(return_value=owned)),
    ):
        scoped = await service._scope_question(
            MagicMock(), conversation=conversation, question=_question(form, field_name), form=form
        )
    return [c.id for c in scoped.all_real_choices or []]


class TestChoicesCarryTheirParent:
    def test_parent_id_and_depends_on_are_read_from_kotlins_payload(self) -> None:
        form, _, _ = _form()
        plot = _question(form, "plot_id")
        assert plot.depends_on == "farm_id"
        assert [c.parent_id for c in plot.all_real_choices or []] == [FARM_A, FARM_A, FARM_B]

    def test_an_independent_question_is_unchanged(self) -> None:
        form, _, _ = _form()
        farm = _question(form, "farm_id")
        assert farm.depends_on is None
        assert all(c.parent_id is None for c in farm.all_real_choices or [])


class TestFarmerScoping:
    async def test_only_the_farmers_own_farms_are_offered(self) -> None:
        form, _, _ = _form()
        assert await _scope(form, "farm_id", owned={FARM_A}) == [FARM_A]

    async def test_mode_all_or_an_unscoped_field_narrows_nothing(self) -> None:
        # farmer_owned_ids returns None for PLOT_SCOPE_MODE=all.
        form, _, _ = _form()
        assert await _scope(form, "farm_id", owned=None) == [FARM_A, FARM_B]

    async def test_a_farmer_with_no_farm_is_offered_none_not_everyones(self) -> None:
        form, _, _ = _form()
        assert await _scope(form, "farm_id", owned=set()) == []


class TestParentFiltering:
    async def test_plots_are_filtered_by_the_farm_already_picked(self) -> None:
        form, farm_q, _ = _form()
        answers = [_answer(farm_q, FARM_A)]
        assert await _scope(form, "plot_id", answers=answers) == ["a1", "a2"]

    async def test_no_farm_answer_yet_means_no_parent_filter(self) -> None:
        form, _, _ = _form()
        assert await _scope(form, "plot_id", answers=[]) == ["a1", "a2", "b1"]

    async def test_a_skipped_farm_filters_nothing(self) -> None:
        form, farm_q, _ = _form()
        answers = [_answer(farm_q, None, skipped=True)]
        assert await _scope(form, "plot_id", answers=answers) == ["a1", "a2", "b1"]

    async def test_several_farms_filter_nothing(self) -> None:
        form, farm_q, _ = _form()
        answers = [_answer(farm_q, None, multi=True, values=[FARM_A, FARM_B])]
        assert await _scope(form, "plot_id", answers=answers) == ["a1", "a2", "b1"]

    async def test_farmer_scoping_happens_first_then_the_parent_filter(self) -> None:
        # Owns plots a1 and b1 only; picked farm A -> just a1.
        form, farm_q, _ = _form()
        answers = [_answer(farm_q, FARM_A)]
        assert await _scope(form, "plot_id", answers=answers, owned={"a1", "b1"}) == ["a1"]


class TestScopedQuestionStillRendersCorrectly:
    async def test_pause_prefix_is_kept_and_buttons_match_the_narrowed_list(self) -> None:
        form, farm_q, _ = _form()
        conversation = MagicMock(conversation_id=uuid.uuid4(), user_id=uuid.uuid4())
        with (
            patch.object(
                service, "_answered_rows", new=AsyncMock(return_value=[_answer(farm_q, FARM_B)])
            ),
            patch.object(plot_picker, "farmer_owned_ids", new=AsyncMock(return_value=None)),
        ):
            scoped = await service._scope_question(
                MagicMock(),
                conversation=conversation,
                question=_question(form, "plot_id"),
                form=form,
            )
        assert [c.label for c in scoped.choices or []] == ["⏸️ พักไว้ก่อน", "แปลง B1"]


class TestFarmerOwnedIdsSwitch:
    async def test_mode_all_never_queries(self) -> None:
        session = MagicMock(execute=AsyncMock())
        with patch.object(line_settings, "PLOT_SCOPE_MODE", "all"):
            assert await plot_picker.farmer_owned_ids(session, uuid.uuid4(), "farm_id") is None
        session.execute.assert_not_awaited()

    async def test_other_fields_are_never_scoped(self) -> None:
        session = MagicMock(execute=AsyncMock())
        with patch.object(line_settings, "PLOT_SCOPE_MODE", "farmer"):
            assert (
                await plot_picker.farmer_owned_ids(session, uuid.uuid4(), "fertilizer_id") is None
            )
        session.execute.assert_not_awaited()
