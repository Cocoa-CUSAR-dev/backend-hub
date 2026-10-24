from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SerializerFunctionWrapHandler, model_serializer
from pydantic.alias_generators import to_camel


class FormDetail(BaseModel):
    """Loosely typed on purpose -- this is the shape AFTER forms/client.py's
    get_form() has already unwrapped Kotlin's {value, error} envelope and
    converted its camelCase keys to snake_case. Each dict in
    sections[]["questions"] carries question_id/label/field_name/
    is_mandatory/sort_order (and, for OPTION-type questions, a "choices"
    list of {id, name} -- not yet consumed by anything, since choice-based
    questions aren't handled in the guided flow yet).
    """

    model_config = {"extra": "allow"}

    task_form_id: str
    # form.task_form.is_multiple_submit, reaching us via Kotlin's
    # Form.Detail. Declared rather than left to extra="allow" so it is typed
    # and, more importantly, so the default is explicit: a web-backend that
    # predates the field simply omits it, and False is the safe reading --
    # one submission per task, exactly as before.
    is_multiple_submit: bool = False
    sections: list[dict[str, Any]] = []


# --- Form definition as the app reads it (docs-and-plan#261) ---------------
#
# camelCase on the wire: the mobile app was built on Kotlin's Form.Detail
# JSON (formId, sections[].questions[].fieldName, ...) and parses those keys
# directly, so the merged backend keeps them byte-for-byte.


class _CamelOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ChoiceOut(_CamelOut):
    id: str
    name: str
    # Only on a dependent question's choices; omitted otherwise (Kotlin's
    # JsonInclude.NON_NULL), so other payloads stay unchanged.
    parent_id: str | None = None

    @model_serializer(mode="wrap")
    def _omit_missing_parent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.parent_id is None:
            data.pop("parentId", None)
            data.pop("parent_id", None)
        return data


class QuestionOut(_CamelOut):
    question_id: UUID
    section_id: UUID
    label: str | None
    input_type: str | None
    description: str | None
    field_name: str | None
    default_value: Any
    is_mandatory: bool
    is_active: bool
    carry_forward: bool
    sort_order: int | None
    # A list only for OPTION questions; null for every other input type.
    choices: list[ChoiceOut] | None
    validation_rule: dict[str, Any] | None
    # The parent field this question's choices are filtered by (plot_id ->
    # "farm_id"); omitted when there is none.
    depends_on: str | None = None

    @model_serializer(mode="wrap")
    def _omit_missing_dependency(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.depends_on is None:
            data.pop("dependsOn", None)
            data.pop("depends_on", None)
        return data


class SectionOut(_CamelOut):
    section_id: UUID
    title: str | None
    description: str | None
    sort_order: int | None
    is_active: bool
    questions: list[QuestionOut]


class FormOut(_CamelOut):
    form_id: UUID
    title: str | None
    description: str | None
    is_multiple_submit: bool
    sections: list[SectionOut]


# --- The researcher's form list and task view (FORM-01, FORM-02) -----------
#
# Same keys as web-backend's Form.Entity and Task.Entity, which the web app
# reads today; times are Bangkok wall-clock with no zone, as Kotlin wrote them.


class FormSummaryOut(_CamelOut):
    """One row of GET /api/v1/forms."""

    form_id: UUID
    task_id: UUID
    title: str | None
    content: str | None
    handler: str | None
    is_multiple_submit: bool
    description: str | None
    created_at: datetime | None


class FormTaskOut(_CamelOut):
    """A form's task, for the researcher's task list (`?include=stats`).

    Kotlin's GET /tasks rows plus `formId`, which the web app passes to
    /forms/{id} from there on. `formId` is null for a task with no form.
    """

    form_id: UUID | None
    task_id: UUID
    title: str | None
    description: str | None
    task_type: str | None
    open_at: datetime | None
    close_at: datetime | None
