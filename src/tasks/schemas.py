from typing import Any
from uuid import UUID

from pydantic import BaseModel

from src.forms.schemas import FormOut
from src.go_json import GoTime


class TaskSubmission(BaseModel):
    """What the chatbot hands over to save: whose answer, to which task, and
    the answer keyed by field name. src/chat/submit.py turns it into a
    `tasks.answers.submit` call.

    Also reused as the LLM's structured-output schema for extraction
    (ADR 0004) -- one schema definition, two consumers, per ADR 0003's
    Pydantic choice.
    """

    user_id: str
    task_id: str
    answer: dict[str, Any]


# --- Merged backend: GET /api/v1/tasks... (docs-and-plan#261) --------------
#
# Times go out as Go wrote them ("...Z"), see src/go_json.py: open_at and
# close_at hold Bangkok wall-clock, submitted_at holds UTC.


class TaskOut(BaseModel):
    """One task in the farmer's task list, with its status for that farmer.
    The app, the LINE picker and LIFF all read the list through this shape.
    """

    task_id: UUID
    title: str | None
    description: str | None
    open_at: GoTime | None
    close_at: GoTime | None
    task_form_id: UUID | None
    handler: str | None
    is_multiple_submit: bool
    # NOT_STARTED / IN_PROGRESS / COMPLETED / OVERDUE
    status: str


class TaskFormOut(BaseModel):
    """The form to fill for a task. `version` lets the app drop its cached
    copy when the form changes."""

    form: FormOut
    version: int


class ResponseOut(BaseModel):
    """One of the caller's answers to a task. response_id is what an edit
    targets when a task holds several answers."""

    response_id: UUID
    task_form_id: UUID | None
    submitted_at: GoTime | None
    status: str | None
    answer: dict[str, Any] | None


class AnswerIn(BaseModel):
    """A farmer's answer to a task's form: field_name -> value, as the form's
    questions name them (the same object Go's POST/PUT /tasks took as
    `answer`; the task and response ids are in the URL now)."""

    answer: dict[str, Any]


class AnswerSavedOut(BaseModel):
    message: str
    # Which response was written, so the caller can edit it later.
    response_id: UUID
