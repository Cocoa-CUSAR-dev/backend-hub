"""/api/v1/me/history and /api/v1/me/diaries -- the farmer's own past
submissions and daily diary (ME-03, ME-04, ME-05), replacing web-backend's
routes of the same paths. A `{date}` that isn't a real day is 422."""

from datetime import date as Date

from fastapi import APIRouter

from src.auth.dependencies import CurrentActor, SessionDep
from src.diary import service
from src.diary.schemas import AnswerFieldOut, DiaryOut

router = APIRouter(prefix="/api/v1/me", tags=["me"])


@router.get("/history", summary="The days I submitted anything on, newest first (ME-03)")
async def submission_days(
    actor: CurrentActor,
    session: SessionDep,
    page: int = 0,
    size: int = service.DEFAULT_PAGE_SIZE,
) -> list[Date]:
    return await service.submission_days(session, actor, page=page, size=size)


@router.get(
    "/history/{date}",
    summary="What I answered on one day (ME-04)",
    response_model=list[AnswerFieldOut],
)
async def answers_on(date: Date, actor: CurrentActor, session: SessionDep) -> list[AnswerFieldOut]:
    return await service.answers_on(session, actor, date)


@router.get(
    "/diaries/{date}",
    summary="My diary for one day; 404 when none was written (ME-05)",
    response_model=DiaryOut,
)
async def diary_on(date: Date, actor: CurrentActor, session: SessionDep) -> DiaryOut:
    return await service.diary_on(session, actor, date)
