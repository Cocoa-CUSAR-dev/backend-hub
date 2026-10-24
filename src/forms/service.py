"""Reading forms as a researcher: the form list, one form, and the task view.

Ported from web-backend's FormController (GET /forms, GET /forms/{id}) and
TaskController (GET /tasks, GET /tasks/{id}), which the After sheet merges
into FORM-01 and FORM-02 (docs-and-plan#271):

- GET /forms             every form that has a section
- GET /forms?include=stats  every task, paged, as the researcher's task list
- GET /forms/{id}        one form's definition (load_form)
- GET /forms/{id}?include=stats  that form's task

`{id}` is always a form id. Kotlin's task routes took a task id; the rows of
the stats list carry `formId` for that.
"""

from uuid import UUID

from src import db
from src.actor import Actor, require
from src.database import Session
from src.db.forms import TaskForm
from src.db.tasks import TaskInfo
from src.exceptions import Forbidden, Invalid, NotFound
from src.forms.detail import load_form
from src.forms.schemas import FormOut, FormSummaryOut, FormTaskOut

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def _task_out(task: TaskInfo, form: TaskForm | None) -> FormTaskOut:
    return FormTaskOut(
        form_id=form.form_id if form else None,
        task_id=task.task_id,
        title=task.title,
        description=task.description,
        task_type=task.task_type,
        open_at=task.open_at,
        close_at=task.close_at,
    )


async def list_for(
    session: Session,
    actor: Actor,
    *,
    include_stats: bool = False,
    page: int = 0,
    size: int = DEFAULT_PAGE_SIZE,
) -> list[FormSummaryOut] | list[FormTaskOut]:
    """Every form with a section, newest first; with `include_stats`, every
    task instead (a task with no form too), paged, newest opening first.

    Unlike the farmer's task list, a page outside the range is refused, as
    web-backend's PageRequest did: page from 0, size 1..500.
    """
    require(actor, "read:form:all")
    if not include_stats:
        rows = await db.forms.forms_with_sections(session)
        return [FormSummaryOut.model_validate(row, from_attributes=True) for row in rows]

    errors = {}
    if page < 0:
        errors["page"] = "page must be 0 or more"
    if not 1 <= size <= MAX_PAGE_SIZE:
        errors["size"] = f"size must be between 1 and {MAX_PAGE_SIZE}"
    if errors:
        raise Invalid(fields=errors)
    tasks = await db.tasks.all_tasks(session, limit=size, offset=page * size)
    forms = await db.forms.forms_of_tasks(session, [task.task_id for task in tasks])
    return [_task_out(task, forms.get(task.task_id)) for task in tasks]


async def get(
    session: Session, actor: Actor, form_id: UUID, *, include_stats: bool = False
) -> FormOut | FormTaskOut:
    """One form's definition, or with `include_stats` that form's task.

    Researchers (read:form:all) see every form. A farmer, hub collector or
    processor (read:form:assigned) sees a form only when its task is for
    them, the rule of their task list; otherwise it is not found
    (web-backend served any form to any of them). The task view is for
    researchers only.
    """
    if include_stats:
        require(actor, "read:form:all")
    elif not actor.can("read:form:all"):
        require(actor, "read:form:assigned")

    form = await db.forms.form_by_id(session, form_id)
    if form is None:
        raise NotFound("Form not found")

    if include_stats:
        task = await db.tasks.task_info(session, form.task_id)
        if task is None:
            raise NotFound("Form not found")
        return _task_out(task, form)

    if not actor.can("read:form:all"):
        if actor.user_id is None:
            raise Forbidden("A user is required")
        if not await db.tasks.task_visible_to(session, actor.user_id, form.task_id):
            raise NotFound("Form not found")
    detail = await load_form(session, form_id)
    if detail is None:
        raise NotFound("Form not found")
    return detail
