"""Reading tasks: the task list, one task's answer, its form, all its answers.

One function per action, shared by every door: the mobile app's
/api/v1/tasks routes now, LINE and LIFF once the chatbot calls these instead
of Go over HTTP (docs-and-plan#259). Ported from mobile-backend's
form_handler.go (GetTasks, GetTaskResponse, GetTaskForm, GetTaskResponses);
same rows, same order, same statuses.

Permissions are the keys the farmer role already has, so nothing new needs
granting: reading a task or its form needs read:form:assigned (or
read:form:all, which researchers have), and reading answers needs
read:response:own. Go checked only for a valid login here; every role that
can actually fill a form has these.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from src import clock, db
from src.actor import Actor, require
from src.db.forms import TaskForm
from src.db.tasks import TaskRow
from src.exceptions import Forbidden, NotFound
from src.forms.detail import load_form
from src.tasks.schemas import ResponseOut, TaskFormOut, TaskOut

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def _caller(actor: Actor) -> UUID:
    """These are all "my ..." reads, so there has to be a person."""
    if actor.user_id is None:
        raise Forbidden("A user is required")
    return actor.user_id


def _require_form_reader(actor: Actor) -> None:
    if not actor.can("read:form:all"):
        require(actor, "read:form:assigned")


def status_of(task: TaskRow, form: TaskForm | None, bangkok_now: datetime) -> str:
    """The task's status for one user.

    A single-submit task is COMPLETED once answered, otherwise OVERDUE after
    close_at. A multi-submit task is never COMPLETED (the farmer files
    several rows against it), so it is IN_PROGRESS once answered until
    close_at, then OVERDUE. close_at is Bangkok wall-clock, so `bangkok_now`
    is naive Bangkok time (docs-and-plan#254).
    """
    overdue = task.close_at is not None and bangkok_now > task.close_at
    if form is not None and form.is_multiple_submit:
        if overdue:
            return "OVERDUE"
        return "IN_PROGRESS" if task.answered else "NOT_STARTED"
    if task.answered:
        return "COMPLETED"
    return "OVERDUE" if overdue else "NOT_STARTED"


async def list_for(
    session: db.Session,
    actor: Actor,
    *,
    open_on: date | None = None,
    page: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[TaskOut]:
    """The caller's tasks, newest first, each with its status for the caller.

    Out-of-range paging falls back the way Go did instead of failing: a
    negative page means the first, a size outside 1..500 means 50 or 500.
    """
    _require_form_reader(actor)
    user_id = _caller(actor)
    page = max(page, 0)
    if page_size <= 0:
        page_size = DEFAULT_PAGE_SIZE
    page_size = min(page_size, MAX_PAGE_SIZE)

    tasks = await db.tasks.tasks_for_user(
        session, user_id, open_on=open_on, limit=page_size, offset=page * page_size
    )
    forms = await db.forms.forms_of_tasks(session, [task.task_id for task in tasks])
    now = clock.bangkok_now().replace(tzinfo=None)
    out = []
    for task in tasks:
        form = forms.get(task.task_id)
        out.append(
            TaskOut(
                task_id=task.task_id,
                title=task.title,
                description=task.description,
                open_at=task.open_at,
                close_at=task.close_at,
                task_form_id=form.form_id if form else None,
                handler=form.handler if form else None,
                is_multiple_submit=form.is_multiple_submit if form else False,
                status=status_of(task, form, now),
            )
        )
    return out


async def get(session: db.Session, actor: Actor, task_id: UUID) -> dict[str, Any]:
    """The caller's most recent answer to this task, as a bare answer object
    (the shape the mobile app parses)."""
    require(actor, "read:response:own")
    answer = await db.tasks.latest_answer(session, _caller(actor), task_id)
    if answer is None:
        raise NotFound("No answer to this task yet")
    return answer


async def get_form(session: db.Session, actor: Actor, task_id: UUID) -> TaskFormOut:
    """The form to fill for this task, plus its version for the app's cache."""
    _require_form_reader(actor)
    task_form = await db.forms.form_of_task(session, task_id)
    if task_form is None:
        raise NotFound("No form for this task")
    form = await load_form(session, task_form.form_id)
    if form is None:
        raise NotFound("No form for this task")
    return TaskFormOut(form=form, version=task_form.version)


async def my_responses(session: db.Session, actor: Actor, task_id: UUID) -> list[ResponseOut]:
    """Every answer the caller filed against this task, newest first. Empty,
    not an error, when there are none."""
    require(actor, "read:response:own")
    rows = await db.tasks.responses_of_user(session, _caller(actor), task_id)
    return [ResponseOut.model_validate(row, from_attributes=True) for row in rows]
