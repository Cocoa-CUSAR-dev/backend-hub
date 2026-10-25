"""/api/v1/forms -- the researcher's form list, one form, and the task view.

Replaces web-backend's GET /forms, /forms/{formId}, /tasks and /tasks/{taskId}
(FORM-01, FORM-02 in the API migration map). `?include=stats` gives the old
/tasks views; `{id}` is always a form id (docs-and-plan#271). A form's
answers, the review table and corrections are under /{id}/responses
(FORM-12..14, docs-and-plan#275).
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from src.auth.dependencies import CurrentActor, SessionDep
from src.forms import responses, service
from src.forms.schemas import (
    CorrectionIn,
    FormOut,
    FormSummaryOut,
    FormTaskOut,
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
