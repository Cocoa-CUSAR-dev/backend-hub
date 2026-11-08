"""/api/v1/tasks -- the farmer's tasks, their forms and my answers to them.

Replaces mobile-backend's GET /tasks, /tasks/:taskId, /tasks/:taskId/form,
/tasks/:taskId/responses, /tasks/:taskId/autofill, POST /tasks and PUT /tasks
(TASK-01..07 in the API migration map).
"""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from src.auth.dependencies import CurrentActor, SessionDep
from src.tasks import answers, autofill, service
from src.tasks.schemas import (
    AnswerIn,
    AnswerSavedOut,
    AutofillOut,
    ResponseOut,
    TaskFormOut,
    TaskOut,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("", summary="My tasks, newest first, each with my status on it")
async def list_for(
    actor: CurrentActor,
    session: SessionDep,
    open_on: Annotated[
        date | None, Query(alias="date", description="Only tasks opening on this day")
    ] = None,
    page: int = 0,
    page_size: int = service.DEFAULT_PAGE_SIZE,
) -> list[TaskOut]:
    return await service.list_for(session, actor, open_on=open_on, page=page, page_size=page_size)


@router.get("/{task_id}", summary="My latest answer to this task (the bare answer object)")
async def get(task_id: UUID, actor: CurrentActor, session: SessionDep) -> dict[str, Any]:
    return await service.get(session, actor, task_id)


@router.get("/{task_id}/form", summary="The form to fill for this task, and its version")
async def get_form(task_id: UUID, actor: CurrentActor, session: SessionDep) -> TaskFormOut:
    return await service.get_form(session, actor, task_id)


@router.get(
    "/{task_id}/autofill",
    response_model=AutofillOut,
    responses={status.HTTP_204_NO_CONTENT: {"description": "Nothing to offer: open a blank form"}},
    summary="My last answer of this form type, safe to prefill a new answer with",
)
async def get_autofill(
    task_id: UUID, actor: CurrentActor, session: SessionDep
) -> AutofillOut | Response:
    offer = await autofill.autofill(session, actor, task_id)
    return offer if offer is not None else Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{task_id}/responses", summary="Every answer I filed for this task, newest first")
async def my_responses(
    task_id: UUID, actor: CurrentActor, session: SessionDep
) -> list[ResponseOut]:
    return await service.my_responses(session, actor, task_id)


@router.post(
    "/{task_id}/responses",
    status_code=status.HTTP_201_CREATED,
    summary="Submit my answer to this task",
)
async def submit(
    task_id: UUID, body: AnswerIn, actor: CurrentActor, session: SessionDep
) -> AnswerSavedOut:
    return await answers.submit(session, actor, task_id, body.answer)


@router.put(
    "/{task_id}/responses/{response_id}",
    summary="Edit one of my answers to this task",
)
async def edit(
    task_id: UUID, response_id: UUID, body: AnswerIn, actor: CurrentActor, session: SessionDep
) -> AnswerSavedOut:
    return await answers.edit(session, actor, task_id, body.answer, response_id)
