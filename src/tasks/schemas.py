from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, PlainSerializer

from src.forms.schemas import FormOut


class TaskListItem(BaseModel):
    """One row of Go's GET /service/tasks response (queryTasksForUser,
    mobile-backend#176) -- the service-key twin of GetTasks used by the
    chatbot's LIFF to-do list (docs-and-plan#176). `status` is one of
    NOT_STARTED / IN_PROGRESS / OVERDUE / COMPLETED, computed by Go from
    close_at and whether a form.response row exists -- see that endpoint's
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
    chat.conversation row exists before trusting it.

    Also reused as the LLM's structured-output schema for extraction
    (ADR 0004) -- one schema definition, two consumers, per ADR 0003's
    Pydantic choice.
    """

    user_id: str
    task_id: str
    answer: dict[str, Any]


# --- Merged backend: GET /api/v1/tasks... (docs-and-plan#261) --------------


def _as_go_time(moment: datetime) -> str:
    """Timestamps go out the way Go sent them: RFC 3339 with a "Z". Columns
    without a zone are labelled as-is (open_at/close_at hold Bangkok
    wall-clock, submitted_at holds UTC); the mobile app was built on exactly
    this output, so the merge does not change it.
    """
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.isoformat() + "Z"


GoTime = Annotated[datetime, PlainSerializer(_as_go_time, return_type=str)]


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
