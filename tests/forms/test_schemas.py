from uuid import uuid4

from src.forms.schemas import ChoiceOut, FormDetail, FormOut, QuestionOut, SectionOut


def test_form_detail_defaults_to_no_sections() -> None:
    form = FormDetail(task_form_id="tf-1")
    assert form.sections == []


def test_form_detail_allows_extra_fields() -> None:
    # extra="allow" -- this mirrors whatever Kotlin's real response happens
    # to include beyond task_form_id/sections, without validation rejecting it.
    form = FormDetail.model_validate({"task_form_id": "tf-1", "title": "จดกิจกรรม"})
    assert form.model_extra is not None
    assert form.model_extra["title"] == "จดกิจกรรม"


def _question(**overrides: object) -> QuestionOut:
    values: dict[str, object] = {
        "question_id": uuid4(),
        "section_id": uuid4(),
        "label": "แปลง",
        "input_type": "OPTION",
        "description": None,
        "field_name": "plot_id",
        "default_value": None,
        "is_mandatory": True,
        "is_active": True,
        "carry_forward": False,
        "sort_order": 1,
        "choices": [ChoiceOut(id="p1", name="A")],
        "validation_rule": None,
        **overrides,
    }
    return QuestionOut.model_validate(values)


def test_form_out_speaks_camel_case_like_kotlin_did() -> None:
    question = _question()
    form = FormOut(
        form_id=uuid4(),
        title="F",
        description=None,
        is_multiple_submit=True,
        sections=[
            SectionOut(
                section_id=question.section_id,
                title="S",
                description=None,
                sort_order=1,
                is_active=True,
                questions=[question],
            )
        ],
    )

    data = form.model_dump(mode="json", by_alias=True)

    assert set(data) == {"formId", "title", "description", "isMultipleSubmit", "sections"}
    q = data["sections"][0]["questions"][0]
    assert {"questionId", "fieldName", "inputType", "isMandatory", "carryForward"} <= set(q)
    assert q["description"] is None  # plain nulls stay in the payload
    assert q["choices"] == [{"id": "p1", "name": "A"}]


def test_parent_id_and_depends_on_are_left_out_when_missing() -> None:
    plain = _question().model_dump(mode="json", by_alias=True)
    dependent = _question(
        depends_on="farm_id", choices=[ChoiceOut(id="p1", name="A", parent_id="f1")]
    ).model_dump(mode="json", by_alias=True)

    assert "dependsOn" not in plain
    assert "parentId" not in plain["choices"][0]
    assert dependent["dependsOn"] == "farm_id"
    assert dependent["choices"] == [{"id": "p1", "name": "A", "parentId": "f1"}]


def test_omission_also_works_without_aliases() -> None:
    data = _question().model_dump(mode="json")
    assert "depends_on" not in data
    assert "parent_id" not in data["choices"][0]
