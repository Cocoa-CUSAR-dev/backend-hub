"""/api/v1/forms -- the researcher's form list, one form, and the task view.

Replaces web-backend's GET /forms, /forms/{formId}, /tasks and /tasks/{taskId}
(FORM-01, FORM-02 in the API migration map). `?include=stats` gives the old
/tasks views; `{id}` is always a form id (docs-and-plan#271). The form
builder's handler pickers and the Data Map catalog are under /handlers
(FORM-06, FORM-07, docs-and-plan#272); creating, rewriting and switching a
form are POST /forms, PUT /forms/{id} and PUT /forms/{id}/edit (FORM-03..05,
docs-and-plan#273). A form's daily reminder and the recipient pickers are
/{id}/reminder and /reminder-recipients (FORM-08..11, docs-and-plan#274).
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status

from src.auth.dependencies import CurrentActor, SessionDep
from src.forms import builder, handlers, service
from src.forms.schemas import (
    CatalogOut,
    FormCreateIn,
    FormEditIn,
    FormOut,
    FormSummaryOut,
    FormTaskOut,
    FormUpdateIn,
    HandlerFieldOut,
)
from src.reminders import settings as reminder_settings
from src.reminders.schemas import FormReminderIn, FormReminderOut, RoleOptionOut, UserOptionOut

router = APIRouter(prefix="/api/v1/forms", tags=["forms"])

Include = Annotated[
    Literal["stats"] | None,
    Query(description="`stats`: the researcher's task view (task dates and type) instead"),
]


@router.get(
    "",
    summary="Every form, newest first; with include=stats every task, paged",
    response_model=list[FormSummaryOut] | list[FormTaskOut],
)
async def list_for(
    actor: CurrentActor,
    session: SessionDep,
    include: Include = None,
    page: int = 0,
    size: int = service.DEFAULT_PAGE_SIZE,
) -> list[FormSummaryOut] | list[FormTaskOut]:
    return await service.list_for(
        session, actor, include_stats=include == "stats", page=page, size=size
    )


# The /handlers and /reminder-recipients routes come before /{form_id}: FastAPI
# matches in order, and "handlers" or "reminder-recipients" would otherwise be
# read (and refused) as a form id.


@router.get("/handlers", summary="The handlers a form can write to (FORM-06)")
async def handler_names(actor: CurrentActor, session: SessionDep) -> list[str]:
    return await handlers.handler_names(session, actor)


@router.get(
    "/handlers/catalog",
    summary="Every handler with its columns and counts, for the Data Map",
    response_model=CatalogOut,
)
async def handler_catalog(actor: CurrentActor, session: SessionDep) -> CatalogOut:
    return await handlers.catalog(session, actor)


@router.get(
    "/handlers/{handler}/fields",
    summary="One handler's columns, as the form builder needs them (FORM-07)",
    response_model=list[HandlerFieldOut],
)
async def handler_fields(
    handler: str, actor: CurrentActor, session: SessionDep
) -> list[HandlerFieldOut]:
    return await handlers.handler_fields(session, actor, handler)


@router.get(
    "/reminder-recipients/roles",
    summary="Every role a reminder can be aimed at (FORM-10)",
    response_model=list[RoleOptionOut],
)
async def reminder_role_options(actor: CurrentActor, session: SessionDep) -> list[RoleOptionOut]:
    return await reminder_settings.role_options(session, actor)


@router.get(
    "/reminder-recipients/users",
    summary="People a reminder can be aimed at, by username (FORM-11)",
    response_model=list[UserOptionOut],
)
async def reminder_user_options(
    actor: CurrentActor, session: SessionDep, q: str = ""
) -> list[UserOptionOut]:
    return await reminder_settings.user_options(session, actor, q)


@router.get(
    "/{form_id}",
    summary="One form's definition; with include=stats its task instead",
    response_model=FormOut | FormTaskOut,
)
async def get(
    form_id: UUID, actor: CurrentActor, session: SessionDep, include: Include = None
) -> FormOut | FormTaskOut:
    return await service.get(session, actor, form_id, include_stats=include == "stats")


@router.get(
    "/{form_id}/reminder",
    summary="A form's daily reminder: on or off, its time and who it's for (FORM-08)",
    response_model=FormReminderOut,
)
async def form_reminder(form_id: UUID, actor: CurrentActor, session: SessionDep) -> FormReminderOut:
    return await reminder_settings.form_reminder(session, actor, form_id)


@router.put(
    "/{form_id}/reminder",
    summary="Turn a form's daily reminder on or off (FORM-09)",
    response_model=FormReminderOut,
)
async def save_form_reminder(
    form_id: UUID, body: FormReminderIn, actor: CurrentActor, session: SessionDep
) -> FormReminderOut:
    return await reminder_settings.save_form_reminder(session, actor, form_id, body)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a form and its task, with who it's for, its repeat and reminder (FORM-03)",
    response_model=FormOut,
)
async def create_form(body: FormCreateIn, actor: CurrentActor, session: SessionDep) -> FormOut:
    return await builder.create_form(session, actor, body)


@router.put(
    "/{form_id}",
    summary="Rewrite a form's sections and questions (FORM-04)",
    response_model=FormOut,
)
async def update_form(
    form_id: UUID, body: FormUpdateIn, actor: CurrentActor, session: SessionDep
) -> FormOut:
    return await builder.update_form(session, actor, form_id, body)


@router.put(
    "/{form_id}/edit",
    summary="Switch a form's sections and questions on or off (FORM-05)",
)
async def edit_form(
    form_id: UUID, body: FormEditIn, actor: CurrentActor, session: SessionDep
) -> str:
    return await builder.edit_form(session, actor, form_id, body)
