"""forms.autofill.sanitize: the rules ported from mobile-backend's
autofill_sanitizer_test.go, plus the Python-side value shapes."""

from typing import Any
from uuid import uuid4

from src.forms.autofill import OfferedQuestion, offered_questions, sanitize
from src.forms.schemas import ChoiceOut, FormOut, QuestionOut, SectionOut

# What form.handler_registry's parent_field holds today (database V30).
PARENTS = frozenset({"farm_activity_id", "harvest_id", "batch_id"})
NOTE = OfferedQuestion("note", "VARCHAR")


def _option(field: str, *ids: str) -> OfferedQuestion:
    return OfferedQuestion(field, "OPTION", ids)


def test_task_id_and_parent_fields_are_always_dropped() -> None:
    answer = {
        "task_id": "t-1",
        "farm_activity_id": "fa-1",
        "harvest_id": "h-1",
        "batch_id": "b-1",
        "note": "hello",
    }

    assert sanitize(answer, [NOTE], PARENTS) == {"note": "hello"}


def test_free_text_passes_through_unchanged() -> None:
    assert sanitize({"note": "ปุ๋ยอินทรีย์"}, [NOTE], PARENTS) == {"note": "ปุ๋ยอินทรีย์"}


def test_an_option_still_on_the_form_is_kept_and_a_stale_one_dropped() -> None:
    questions = [_option("fertilizer_id", "current-id")]

    assert sanitize({"fertilizer_id": "current-id"}, questions, PARENTS) == {
        "fertilizer_id": "current-id"
    }
    assert sanitize({"fertilizer_id": "stale-id"}, questions, PARENTS) == {}


def test_option_ids_match_the_way_go_printed_them() -> None:
    questions = [_option("grade", "5"), _option("flag", "true")]

    kept = sanitize({"grade": 5.0, "flag": True}, questions, PARENTS)

    assert kept == {"grade": 5.0, "flag": True}


def test_boolean_values_must_be_true_or_false() -> None:
    questions = [OfferedQuestion("is_quality_damage", "BOOLEAN")]

    assert sanitize({"is_quality_damage": "true"}, questions, PARENTS) == {
        "is_quality_damage": "true"
    }
    assert sanitize({"is_quality_damage": False}, questions, PARENTS) == {
        "is_quality_damage": False
    }
    assert sanitize({"is_quality_damage": "maybe"}, questions, PARENTS) == {}


def test_a_field_not_on_the_current_form_passes_through() -> None:
    assert sanitize({"retired_field": "x"}, [NOTE], PARENTS) == {"retired_field": "x"}


def test_a_mixed_answer_only_loses_what_it_should() -> None:
    answer = {"task_id": "t-1", "batch_id": "b-1", "note": "ok", "fertilizer_id": "stale-id"}
    questions = [NOTE, _option("fertilizer_id", "current-id")]

    assert sanitize(answer, questions, PARENTS) == {"note": "ok"}


def test_geodata_is_dropped_whatever_the_case_of_its_type() -> None:
    answer: dict[str, Any] = {"geo_location": [{"lat": 13.7, "lng": 100.5}], "note": "ฝนตก"}

    assert sanitize(answer, [OfferedQuestion("geo_location", "GEODATA"), NOTE], PARENTS) == {
        "note": "ฝนตก"
    }
    assert sanitize(answer, [OfferedQuestion("geo_location", "geodata")], PARENTS) == {
        "note": "ฝนตก"
    }


def test_upload_is_dropped_even_off_the_form() -> None:
    answer = {"upload": "https://storage.example/old.jpg", "note": "ใส่ปุ๋ย"}

    assert sanitize(answer, [OfferedQuestion("upload", "VARCHAR"), NOTE], PARENTS) == {
        "note": "ใส่ปุ๋ย"
    }
    assert sanitize({"upload": "https://storage.example/old.jpg"}, [], PARENTS) == {}


def test_parent_fields_come_from_the_caller() -> None:
    answer = {"inspection_id": "i-1", "note": "hello"}

    assert sanitize(answer, [NOTE], {"inspection_id"}) == {"note": "hello"}


def _question(field: str | None, kind: str, active: bool = True, **extra: Any) -> QuestionOut:
    return QuestionOut(
        question_id=uuid4(),
        section_id=uuid4(),
        label=field,
        input_type=kind,
        description=None,
        field_name=field,
        default_value=None,
        is_mandatory=False,
        is_active=active,
        carry_forward=False,
        sort_order=0,
        choices=extra.get("choices"),
        validation_rule=None,
    )


def _section(active: bool, *questions: QuestionOut) -> SectionOut:
    return SectionOut(
        section_id=uuid4(),
        title="S",
        description=None,
        sort_order=0,
        is_active=active,
        questions=list(questions),
    )


def test_offered_questions_are_the_visible_named_ones() -> None:
    form = FormOut(
        form_id=uuid4(),
        title="F",
        description=None,
        is_multiple_submit=False,
        sections=[
            _section(
                True,
                _question("colour_id", "OPTION", choices=[ChoiceOut(id="red", name="แดง")]),
                _question("hidden", "VARCHAR", active=False),
                _question(None, "VARCHAR"),
            ),
            _section(False, _question("in_hidden_section", "VARCHAR")),
        ],
    )

    assert offered_questions(form) == [OfferedQuestion("colour_id", "OPTION", ("red",))]
