"""src/forms/validation.py: Go's answer_validator cases carried over, plus
the per-field validation rules (V16) the mobile path never had."""

from datetime import date, datetime
from typing import Any
from uuid import uuid4

import pytest

from src import clock
from src.forms.schemas import ChoiceOut, FormOut, QuestionOut, SectionOut
from src.forms.validation import REQUIRED, answer_errors, checked_fields


def q(field: str | None, input_type: str, mandatory: bool = True, **extra: Any) -> QuestionOut:
    values: dict[str, Any] = {
        "question_id": uuid4(),
        "section_id": uuid4(),
        "label": field,
        "input_type": input_type,
        "description": None,
        "field_name": field,
        "default_value": None,
        "is_mandatory": mandatory,
        "is_active": True,
        "carry_forward": False,
        "sort_order": 1,
        "choices": None,
        "validation_rule": None,
        **extra,
    }
    return QuestionOut.model_validate(values)


def form(*questions: QuestionOut, section_active: bool = True) -> FormOut:
    section = SectionOut(
        section_id=uuid4(), title="S", description=None, sort_order=1,
        is_active=section_active, questions=list(questions),
    )  # fmt: skip
    return FormOut(
        form_id=uuid4(), title="F", description=None, is_multiple_submit=False, sections=[section]
    )


GRADES = [ChoiceOut(id="A", name="Grade A"), ChoiceOut(id="B", name="Grade B")]


# --- carried over from answer_validator_test.go --------------------------------


def test_all_valid() -> None:
    f = form(
        q("note", "VARCHAR"),
        q("quantity_kg", "INT"),
        q("weight", "FLOAT", mandatory=False),
        q("harvest_date", "DATE"),
        q("logged_at", "DATETIME", mandatory=False),
        q("is_organic", "BOOLEAN"),
        q("grade", "OPTION", choices=GRADES),
    )
    answer = {
        "note": "เก็บเกี่ยวเสร็จแล้ว",
        "quantity_kg": 42.0,
        "weight": "12.5",
        "harvest_date": "2026-01-05",
        "logged_at": "2026-01-05T10:00:00Z",
        "is_organic": True,
        "grade": "A",
    }
    assert answer_errors(f, answer) == {}


def test_missing_and_blank_mandatory_fields() -> None:
    f = form(q("harvest_date", "DATE"), q("note", "VARCHAR"))
    assert answer_errors(f, {"note": "   "}) == {"harvest_date": REQUIRED, "note": REQUIRED}


def test_optional_field_absent_is_fine() -> None:
    assert answer_errors(form(q("note", "VARCHAR", mandatory=False)), {}) == {}


def test_wrong_types() -> None:
    f = form(
        q("quantity_kg", "INT"), q("weight", "FLOAT"), q("harvest_date", "DATE"),
        q("is_organic", "BOOLEAN"), q("note", "VARCHAR"),
    )  # fmt: skip
    errors = answer_errors(
        f,
        {
            "quantity_kg": "not-a-number",
            "weight": "also-not",
            "harvest_date": "05/01/2026",
            "is_organic": "maybe",
            "note": 12,
        },
    )
    assert errors == {
        "quantity_kg": "ต้องเป็นจำนวนเต็ม",
        "weight": "ต้องเป็นตัวเลข",
        "harvest_date": "รูปแบบวันที่ไม่ถูกต้อง",
        "is_organic": "ต้องเป็นค่า true/false",
        "note": "ต้องเป็นข้อความ",
    }


def test_int_rejects_fractional_values() -> None:
    assert "quantity_kg" in answer_errors(form(q("quantity_kg", "INT")), {"quantity_kg": 4.5})


def test_option_must_be_one_of_the_choices() -> None:
    f = form(q("grade", "OPTION", choices=GRADES))
    assert answer_errors(f, {"grade": "Z"}) == {"grade": "ตัวเลือกไม่ถูกต้อง"}


def test_option_matches_a_numeric_id_written_as_number_or_text() -> None:
    f = form(q("grade", "OPTION", choices=[ChoiceOut(id="5", name="Grade 5")]))
    assert answer_errors(f, {"grade": "5"}) == {}
    assert answer_errors(f, {"grade": 5}) == {}
    assert answer_errors(f, {"grade": 5.0}) == {}


def test_geodata_is_a_list_of_lat_lng_points() -> None:
    f = form(q("plot_boundary", "GEODATA"))
    assert answer_errors(f, {"plot_boundary": [{"lat": 13.75, "lng": 100.5}]}) == {}
    assert answer_errors(f, {"plot_boundary": [{"lat": "x", "lng": 100.5}]}) == {
        "plot_boundary": "ข้อมูลพิกัดไม่ถูกต้อง"
    }
    assert answer_errors(f, {"plot_boundary": []}) == {"plot_boundary": REQUIRED}


def test_inactive_questions_and_sections_are_skipped() -> None:
    hidden = form(q("hidden", "VARCHAR", is_active=False))
    hidden_section = form(q("in_hidden_section", "VARCHAR"), section_active=False)
    assert answer_errors(hidden, {}) == {}
    assert answer_errors(hidden_section, {}) == {}


def test_question_without_field_name_and_unknown_keys_are_ignored() -> None:
    f = form(q(None, "VARCHAR"), q("note", "VARCHAR"))
    assert answer_errors(f, {"note": "ok", "task_id": "t1", "unexpected": "v"}) == {}


@pytest.mark.parametrize("text", ["1", "t", "TRUE", "true", "0", "f", "False"])
def test_boolean_accepts_gos_parse_bool_spellings(text: str) -> None:
    assert answer_errors(form(q("ok", "BOOLEAN")), {"ok": text}) == {}


# --- validation rules (V16) ------------------------------------------------------


def rule(**values: Any) -> dict[str, Any]:
    return {"errorMessage": "bad", **values}


def test_number_ranges() -> None:
    f = form(
        q("temp", "FLOAT", validation_rule=rule(type="FLOAT", min=0, max=60)),
        q("fans", "INT", validation_rule=rule(type="INT", min=0, max=50, integerOnly=True)),
    )
    assert answer_errors(f, {"temp": "35.5", "fans": 4}) == {}
    assert answer_errors(f, {"temp": 61, "fans": -1}) == {"temp": "bad", "fans": "bad"}


def test_text_length() -> None:
    f = form(q("note", "VARCHAR", validation_rule=rule(type="VARCHAR", maxLength=5)))
    assert answer_errors(f, {"note": "12345"}) == {}
    assert answer_errors(f, {"note": "123456"}) == {"note": "bad"}


def test_no_future_dates_in_bangkok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "bangkok_today", lambda: date(2026, 10, 24))
    monkeypatch.setattr(
        clock, "bangkok_now", lambda: datetime(2026, 10, 24, 9, 0, tzinfo=clock.BANGKOK)
    )
    f = form(
        q("day", "DATE", validation_rule=rule(type="DATE", maxDate="today")),
        q("at", "DATETIME", validation_rule=rule(type="DATETIME", maxDate="today")),
    )
    assert answer_errors(f, {"day": "2026-10-24", "at": "2026-10-24T08:59:00"}) == {}
    assert answer_errors(f, {"day": "2026-10-25", "at": "2026-10-24T09:30:00"}) == {
        "day": "bad",
        "at": "bad",
    }


def test_coordinates_on_the_globe() -> None:
    f = form(q("where", "GEODATA", validation_rule=rule(type="GEODATA", validLatLng=True)))
    assert answer_errors(f, {"where": [{"lat": 18.8, "lng": 98.9}]}) == {}
    assert answer_errors(f, {"where": [{"lat": 95, "lng": 98.9}]}) == {"where": "bad"}


def test_type_error_wins_over_the_rule_and_a_missing_message_has_a_default() -> None:
    f = form(
        q("temp", "FLOAT", validation_rule=rule(type="FLOAT", max=10)),
        q("level", "INT", validation_rule={"type": "INT", "max": 3}),
    )
    errors = answer_errors(f, {"temp": "hot", "level": 9})
    assert errors == {"temp": "ต้องเป็นตัวเลข", "level": "รูปแบบคำตอบไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง"}


def test_optional_empty_field_skips_its_rule() -> None:
    f = form(
        q("note", "VARCHAR", mandatory=False, validation_rule=rule(type="VARCHAR", maxLength=1))
    )
    assert answer_errors(f, {"note": ""}) == {}


def test_checked_fields_are_the_active_questions_with_a_field_name() -> None:
    f = form(q("note", "VARCHAR"), q(None, "VARCHAR"), q("hidden", "VARCHAR", is_active=False))
    assert checked_fields(f) == {"note"}
    assert checked_fields(form(q("x", "VARCHAR"), section_active=False)) == set()
