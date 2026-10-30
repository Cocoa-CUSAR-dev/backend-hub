"""forms.detail.load_chat_form: the chatbot reads a form through the same
`load_form` as the app and the web, in the snake_case shape its conversation
code expects (what the HTTP call to web-backend used to hand it).
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.exceptions import FormNotFound
from src.forms.detail import _camel_to_snake, load_chat_form, snake_keys
from src.forms.schemas import FormOut

SESSION = object()
FORM_ID = uuid.uuid4()
SECTION_ID = uuid.uuid4()
QUESTION_ID = uuid.uuid4()
CHOICE_ID = str(uuid.uuid4())


def _form() -> FormOut:
    """A form the way the database read builds it: one text question with a
    validation rule (stored with camelCase keys) and one dependent OPTION."""
    return FormOut.model_validate(
        {
            "formId": str(FORM_ID),
            "title": "จดกิจกรรมในสวน",
            "description": None,
            "isMultipleSubmit": True,
            "sections": [
                {
                    "sectionId": str(SECTION_ID),
                    "title": None,
                    "description": None,
                    "sortOrder": 1,
                    "isActive": True,
                    "questions": [
                        {
                            "questionId": str(QUESTION_ID),
                            "sectionId": str(SECTION_ID),
                            "label": "น้ำหนัก",
                            "inputType": "DECIMAL",
                            "description": None,
                            "fieldName": "weight_kg",
                            "defaultValue": None,
                            "isMandatory": True,
                            "isActive": True,
                            "carryForward": False,
                            "sortOrder": 1,
                            "choices": None,
                            "validationRule": {"errorMessage": "ตัวเลขเท่านั้น", "maxLength": 6},
                        },
                        {
                            "questionId": str(uuid.uuid4()),
                            "sectionId": str(SECTION_ID),
                            "label": "แปลง",
                            "inputType": "OPTION",
                            "description": None,
                            "fieldName": "plot_id",
                            "defaultValue": None,
                            "isMandatory": True,
                            "isActive": True,
                            "carryForward": True,
                            "sortOrder": 2,
                            "choices": [{"id": CHOICE_ID, "name": "แปลง 1", "parentId": "farm-1"}],
                            "validationRule": None,
                            "dependsOn": "farm_id",
                        },
                    ],
                }
            ],
        }
    )


def test_camel_to_snake_converts_common_cases() -> None:
    assert _camel_to_snake("fieldName") == "field_name"
    assert _camel_to_snake("isMandatory") == "is_mandatory"
    assert _camel_to_snake("questionId") == "question_id"
    assert _camel_to_snake("formId") == "form_id"
    assert _camel_to_snake("already_snake") == "already_snake"
    assert _camel_to_snake("id") == "id"


def test_snake_keys_recurses_into_nested_sections_and_questions() -> None:
    raw = {
        "formId": "f1",
        "sections": [
            {
                "sectionId": "s1",
                "questions": [
                    {"questionId": "q1", "isMandatory": True, "fieldName": "farm_id"},
                ],
            }
        ],
    }

    converted = snake_keys(raw)

    assert converted["form_id"] == "f1"
    question = converted["sections"][0]["questions"][0]
    assert question["question_id"] == "q1"
    assert question["is_mandatory"] is True
    assert question["field_name"] == "farm_id"


async def test_the_form_comes_back_in_the_shape_the_conversation_reads() -> None:
    with patch("src.forms.detail.load_form", AsyncMock(return_value=_form())) as load_form:
        detail = await load_chat_form(SESSION, FORM_ID)  # type: ignore[arg-type]

    load_form.assert_awaited_once_with(SESSION, FORM_ID)
    # The form's id is called task_form_id everywhere in the chat:
    assert detail.task_form_id == str(FORM_ID)
    assert "form_id" not in detail.model_dump()
    assert detail.is_multiple_submit is True
    text_question, option_question = detail.sections[0]["questions"]
    assert text_question["question_id"] == str(QUESTION_ID)
    assert text_question["field_name"] == "weight_kg"
    assert text_question["is_mandatory"] is True
    assert text_question["sort_order"] == 1
    # The rule's own keys are snake_cased too: validation.py reads max_length.
    assert text_question["validation_rule"] == {"error_message": "ตัวเลขเท่านั้น", "max_length": 6}
    assert option_question["choices"] == [
        {"id": CHOICE_ID, "name": "แปลง 1", "parent_id": "farm-1"}
    ]
    assert option_question["depends_on"] == "farm_id"
    assert option_question["carry_forward"] is True


async def test_an_id_given_as_text_is_read_as_the_form_id() -> None:
    """The conversation holds ids as text; postbacks carry them as text too."""
    with patch("src.forms.detail.load_form", AsyncMock(return_value=_form())) as load_form:
        await load_chat_form(SESSION, str(FORM_ID))  # type: ignore[arg-type]

    load_form.assert_awaited_once_with(SESSION, FORM_ID)


async def test_a_form_that_does_not_exist_is_form_not_found() -> None:
    with (
        patch("src.forms.detail.load_form", AsyncMock(return_value=None)),
        pytest.raises(FormNotFound),
    ):
        await load_chat_form(SESSION, FORM_ID)  # type: ignore[arg-type]


async def test_something_that_is_not_an_id_is_form_not_found_without_a_database_read() -> None:
    """A postback is user-controlled text: it must not become a 500 or a query."""
    with (
        patch("src.forms.detail.load_form", AsyncMock()) as load_form,
        pytest.raises(FormNotFound),
    ):
        await load_chat_form(SESSION, "not-an-id")  # type: ignore[arg-type]

    load_form.assert_not_awaited()
