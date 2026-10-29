"""src/forms/definition.py: the save rules of a form, as web-backend's
FormDefinitionValidator applied them (messages included)."""

from src.db.forms import FieldDependencyRow, HandlerRow
from src.forms.definition import QuestionSpec, problems
from src.forms.schemas import HandlerFieldOut

HARVEST = HandlerRow("harvest", "collection", "harvest", None, None, True)
GRADE = HandlerRow(
    "harvest_grade_detail", "collection", "harvest_grade_detail", "harvest", "harvest_id", True
)
PLOT_BY_FARM = {
    "plot_id": FieldDependencyRow("plot_id", "farm_id", "agriculture.plot", "farm_id", "แปลงในฟาร์ม")
}


def _field(name: str, *types: str, required: bool = False) -> HandlerFieldOut:
    return HandlerFieldOut(
        name=name,
        data_type="uuid",
        required=required,
        references=None,
        depends_on=None,
        allowed_input_types=list(types),
    )


FIELDS = [
    _field("farm_id", "OPTION", required=True),
    _field("hub_id", "OPTION", required=True),
    _field("plot_id", "OPTION"),
    _field("harvest_date", "DATETIME", "DATE"),
    _field("grade_code", "OPTION", "VARCHAR"),
]


def _q(field: str | None, kind: str = "OPTION", section: int = 0, order: int = 0) -> QuestionSpec:
    return QuestionSpec(section, order, f"ถาม {field}", kind, field)


def test_a_complete_form_passes() -> None:
    questions = [_q("farm_id", order=0), _q("hub_id", order=1), _q("harvest_date", "DATE", order=2)]
    assert problems(HARVEST, FIELDS, PLOT_BY_FARM, questions) == []


def test_a_field_that_isnt_a_column_and_a_question_with_no_field_are_allowed() -> None:
    questions = [_q("farm_id"), _q("hub_id", order=1), _q("notes", "VARCHAR", order=2), _q(None)]
    assert problems(HARVEST, FIELDS, {}, questions) == []


def test_an_input_type_the_column_cant_hold() -> None:
    questions = [_q("farm_id", "VARCHAR"), _q("hub_id", order=1)]
    assert problems(HARVEST, FIELDS, {}, questions) == [
        "คำถาม 'ถาม farm_id' (farm_id) ใช้ชนิด VARCHAR ไม่ได้ คอลัมน์นี้รับได้เฉพาะ OPTION"
    ]


def test_a_column_with_no_allowed_type_says_dash() -> None:
    fields = [*FIELDS, _field("extra")]
    questions = [_q("farm_id"), _q("hub_id", order=1), _q("extra", "VARCHAR", order=2)]
    assert problems(HARVEST, fields, {}, questions)[0].endswith("รับได้เฉพาะ -")


def test_a_field_asked_twice() -> None:
    questions = [_q("farm_id"), _q("hub_id", order=1), _q("hub_id", order=2)]
    assert problems(HARVEST, FIELDS, {}, questions) == [
        "ฟิลด์ hub_id ถูกใช้ซ้ำ 2 คำถาม ('ถาม hub_id', 'ถาม hub_id') ต้องมีได้คำถามเดียว"
    ]


def test_every_required_column_must_be_asked() -> None:
    assert problems(HARVEST, FIELDS, {}, [_q("harvest_date", "DATE")]) == [
        "ตาราง collection.harvest บังคับฟิลด์ farm_id, hub_id แต่ฟอร์มยังไม่มีคำถามสำหรับฟิลด์นี้"
    ]


def test_a_dependent_field_needs_its_parent_asked_first() -> None:
    before = [_q("farm_id", order=0), _q("hub_id", order=1), _q("plot_id", order=2)]
    after = [_q("plot_id", order=0), _q("hub_id", order=1), _q("farm_id", order=2)]
    later_section = [
        _q("plot_id", section=0, order=5),
        _q("hub_id", order=1),
        _q("farm_id", section=1),
    ]
    without = [_q("plot_id"), _q("hub_id", order=1)]

    assert problems(HARVEST, FIELDS, PLOT_BY_FARM, before) == []
    message = "คำถาม 'ถาม plot_id' (plot_id) ต้องมีคำถาม 'ถาม farm_id' (farm_id) อยู่ก่อน (แปลงในฟาร์ม)"
    assert problems(HARVEST, FIELDS, PLOT_BY_FARM, after) == [message]
    assert problems(HARVEST, FIELDS, PLOT_BY_FARM, later_section) == [message]
    assert problems(HARVEST, FIELDS, PLOT_BY_FARM, without)[-1] == (
        "คำถาม 'ถาม plot_id' (plot_id) ต้องมีคำถาม farm_id อยู่ก่อน (แปลงในฟาร์ม)"
    )


def test_a_parent_the_chatbots_picker_fills_counts_as_asked() -> None:
    by_harvest = {
        "grade_code": FieldDependencyRow(
            "grade_code", "harvest_id", "collection.harvest", "harvest_id", "เกรดของการเก็บ"
        )
    }
    assert problems(GRADE, FIELDS[3:], by_harvest, [_q("grade_code")]) == []


def test_every_problem_is_reported_at_once() -> None:
    questions = [_q("farm_id", "VARCHAR"), _q("farm_id", order=1)]
    assert len(problems(HARVEST, FIELDS, {}, questions)) == 3  # type, twice, hub_id missing
