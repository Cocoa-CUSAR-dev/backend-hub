"""/api/v1/forms -- the researcher's form list, one form, and the task view.

Replaces web-backend's GET /forms, /forms/{formId}, /tasks and /tasks/{taskId}
(FORM-01, FORM-02 in the API migration map). `?include=stats` gives the old
/tasks views; `{id}` is always a form id (docs-and-plan#271).
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from src.auth.dependencies import CurrentActor, SessionDep
from src.forms import service
from src.forms.schemas import FormOut, FormSummaryOut, FormTaskOut

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
