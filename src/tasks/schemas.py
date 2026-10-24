from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from src.forms.schemas import FormOut
from src.go_json import GoTime


class TaskListItem(BaseModel):
    """One row of Go's GET /service/tasks response (queryTasksForUser,
    mobile-backend#176) -- the service-key twin of GetTasks used by the
    chatbot's LIFF to-do list (docs-and-plan#176). `status` is one of
    NOT_STARTED / IN_PROGRESS / OVERDUE / COMPLETED, computed by Go from
    close_at and whether the user has answered -- see that endpoint's
    own docstring for the exact ladder (multi-submit forms never reach
    COMPLETED there, by design).
    """

    task_id: UUID
    task_form_id: UUID
    title: str
    description: str | None = None
    open_at: datetime | None = None
    close_at: datetime | None = None
    handler: str
    is_multiple_submit: bool = False
    status: str


class TaskSubmission(BaseModel):
    """Matches Go's POST /service/tasks (ADR 0001's write side, via the
    chatbot's own service-account credential -- see tasks/client.py).
    user_id is explicit here because there's no farmer session for Go to
    derive it from on this path; Go independently verifies a matching
    conversation exists before trusting it.

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

    Will replace TaskListItem above once the chatbot calls tasks.service
    directly instead of Go over HTTP (docs-and-plan#259).
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
