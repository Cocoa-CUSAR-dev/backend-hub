from datetime import date, datetime, time
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    model_serializer,
)
from pydantic.alias_generators import to_camel

from src import clock


class FormDetail(BaseModel):
    """Loosely typed on purpose -- this is the shape AFTER
    forms.detail.load_chat_form() has turned a FormOut's camelCase keys into
    snake_case (and form_id into task_form_id). Each dict in
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


# ---- the form builder's handler pickers and the Data Map (FORM-06, FORM-07) ----------
# Same keys as web-backend's Handler.Field and Handler.Catalog.


class HandlerFieldOut(_CamelOut):
    """One column of a handler's table, as the form builder sees it."""

    name: str
    data_type: str
    required: bool  # the INSERT fails without it
    references: str | None  # "schema.table" of its foreign key
    depends_on: str | None  # the field its choices are filtered by
    allowed_input_types: list[str]  # first one is the builder's default


class CatalogFieldOut(HandlerFieldOut):
    used_by_forms: int  # how many of the handler's forms ask it


class HandlerFormsOut(_CamelOut):
    count: int
    latest_title: str | None
    latest_open_at: datetime | None


class CatalogEntryOut(_CamelOut):
    handler: str
    label_th: str
    description_th: str
    category: str
    frequency: str
    table: str
    parent_handler: str | None
    parent_field: str | None
    fields: list[CatalogFieldOut]
    forms: HandlerFormsOut
    response_count: int


class DependencyOut(_CamelOut):
    child_field: str
    parent_field: str
    link_table: str
    link_column: str
    label_th: str


class RegistrationTableOut(_CamelOut):
    table: str
    label_th: str


class CatalogOut(_CamelOut):
    """Everything the Data Map page shows."""

    handlers: list[CatalogEntryOut]
    dependencies: list[DependencyOut]
    registration_tables: list[RegistrationTableOut]
    field_labels: dict[str, str]


# ---- the form builder's requests (FORM-03, FORM-04, FORM-05) --------------------------
# The bodies web-backend's Form.Request.Create / Update / Edit took, same keys
# and defaults. Times are Bangkok wall-clock with no zone, as Kotlin stored them.


class _CamelIn(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def _wall_clock(value: datetime) -> datetime:
    """A time with a zone becomes Bangkok wall-clock: the columns have none."""
    if value.tzinfo is None:
        return value
    return value.astimezone(clock.BANGKOK).replace(tzinfo=None)


WallClock = Annotated[datetime, AfterValidator(_wall_clock)]


class RecipientIn(_CamelIn):
    """ROLE = everyone holding that role (id is a role_id); USER = one person."""

    type: Literal["ROLE", "USER"]
    id: UUID


class ReminderIn(_CamelIn):
    """A daily reminder set up with the form; enabled=false means none."""

    enabled: bool
    time_of_day: time
    recipients: list[RecipientIn] = []


class RecurrenceIn(_CamelIn):
    """The task being created is round one. weekdays are ISO (1 = Monday ...
    7 = Sunday) and only mean something for WEEKLY; until_date is the last
    day a round can be for, None for no end."""

    frequency: Literal["DAILY", "WEEKLY"]
    weekdays: list[int] | None = None
    until_date: date | None = None


class QuestionCreateIn(_CamelIn):
    label: str
    description: str | None = None
    input_type: str
    field_name: str | None = None
    is_mandatory: bool = False
    carry_forward: bool = False
    sort_order: int
    default_value: Any = None


class SectionCreateIn(_CamelIn):
    title: str
    description: str | None = None
    sort_order: int
    questions: list[QuestionCreateIn]


class FormCreateIn(_CamelIn):
    title: str
    description: str | None = None
    task_type: str = "FORM"
    open_at: WallClock
    close_at: WallClock
    handler: str
    is_multiple_submit: bool = False
    sections: list[SectionCreateIn]
    reminder: ReminderIn | None = None
    recurrence: RecurrenceIn | None = None
    # Who the task is for. Empty = everyone, as before.
    recipients: list[RecipientIn] = []


class QuestionUpdateIn(_CamelIn):
    """No question_id = a new question."""

    question_id: UUID | None = None
    label: str
    description: str | None = None
    input_type: str
    field_name: str | None = None
    is_mandatory: bool = False
    is_active: bool = True
    carry_forward: bool | None = None  # None = keep what is stored
    sort_order: int
    default_value: Any = None


class SectionUpdateIn(_CamelIn):
    """No section_id = a new section. A section left out is deleted."""

    section_id: UUID | None = None
    title: str
    description: str | None = None
    is_active: bool = True
    sort_order: int
    questions: list[QuestionUpdateIn]


class FormUpdateIn(_CamelIn):
    description: str | None = None
    # None = keep it: a client that doesn't know the field can't switch it off.
    is_multiple_submit: bool | None = None
    sections: list[SectionUpdateIn]


class QuestionEditIn(_CamelIn):
    question_id: UUID
    description: str | None = None
    is_active: bool | None = None
    is_mandatory: bool | None = None


class SectionEditIn(_CamelIn):
    section_id: UUID
    description: str | None = None
    is_active: bool
    questions: list[QuestionEditIn] = []


class FormEditIn(_CamelIn):
    """Switch sections and questions on or off, and questions required or not;
    nothing else changes (the descriptions are accepted and not written, as
    in Kotlin)."""

    description: str | None = None
    sections: list[SectionEditIn] = []
