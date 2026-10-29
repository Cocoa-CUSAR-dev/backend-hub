"""/api/v1/forms -- the researcher's form list, one form, and the task view.

Replaces web-backend's GET /forms, /forms/{formId}, /tasks and /tasks/{taskId}
(FORM-01, FORM-02 in the API migration map). `?include=stats` gives the old
/tasks views; `{id}` is always a form id (docs-and-plan#271). The form
builder's handler pickers and the Data Map catalog are under /handlers
(FORM-06, FORM-07, docs-and-plan#272); creating, rewriting and switching a
form are POST /forms, PUT /forms/{id} and PUT /forms/{id}/edit (FORM-03..05,
docs-and-plan#273).
A form's answers, the review table and corrections are under /{id}/responses
(FORM-12..14, docs-and-plan#275).
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status

from src.auth.dependencies import CurrentActor, SessionDep
from src.forms import builder, handlers, responses, service
from src.forms.schemas import (
    CatalogOut,
    CorrectionIn,
    FormCreateIn,
    FormEditIn,
    FormOut,
    FormSummaryOut,
    FormTaskOut,
    FormUpdateIn,
    HandlerFieldOut,
    QuestionAnswersOut,
    ResponseOut,
    ReviewFieldOut,
    SubmissionOut,
)

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


# The /handlers routes come before /{form_id}: FastAPI matches in order, and
# "handlers" would otherwise be read (and refused) as a form id.


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
    "/{form_id}",
    summary="One form's definition; with include=stats its task instead",
    response_model=FormOut | FormTaskOut,
)
async def get(
    form_id: UUID, actor: CurrentActor, session: SessionDep, include: Include = None
) -> FormOut | FormTaskOut:
    return await service.get(session, actor, form_id, include_stats=include == "stats")


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


@router.get(
    "/{form_id}/responses",
    summary="Every answer to a form, by question; with view=review, by submission (FORM-12)",
    response_model=list[QuestionAnswersOut] | list[SubmissionOut],
)
async def form_responses(
    form_id: UUID,
    actor: CurrentActor,
    session: SessionDep,
    view: Annotated[
        Literal["review"] | None,
        Query(description="`review`: one row per submission, field by field, newest first"),
    ] = None,
    ai_only: Annotated[
        bool, Query(alias="aiOnly", description="With view=review: only AI-extracted answers")
    ] = False,
    page: int = 0,
    size: int = responses.DEFAULT_PAGE_SIZE,
) -> list[QuestionAnswersOut] | list[SubmissionOut]:
    if view == "review":
        return await responses.review(
            session, actor, form_id, ai_only=ai_only, page=page, size=size
        )
    return await responses.answers_by_question(session, actor, form_id, page=page, size=size)


@router.get(
    "/{form_id}/responses/{response_id}",
    summary="One answer to a form, as stored (FORM-13)",
    response_model=ResponseOut,
)
async def form_response(
    form_id: UUID, response_id: UUID, actor: CurrentActor, session: SessionDep
) -> ResponseOut:
    return await responses.one_response(session, actor, form_id, response_id)


@router.patch(
    "/{form_id}/responses/{response_id}/fields/{field_name}",
    summary="Correct one field of an answer (FORM-14)",
    response_model=ReviewFieldOut,
)
async def correct_field(
    form_id: UUID,
    response_id: UUID,
    field_name: str,
    body: CorrectionIn,
    actor: CurrentActor,
    session: SessionDep,
) -> ReviewFieldOut:
    return await responses.correct(session, actor, form_id, response_id, field_name, body)
