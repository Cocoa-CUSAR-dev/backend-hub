"""/api/v1/processing -- processing stations and their batches.

Replaces mobile-backend's POST/GET /processing_stations and GET /batches
(PRC-01..03 in the API migration map).
"""

from fastapi import APIRouter, status

from src import pagination
from src.auth.dependencies import CurrentActor, SessionDep
from src.processing import service
from src.processing.schemas import BatchOut, StationCreatedOut, StationIn, StationOut

router = APIRouter(prefix="/api/v1/processing", tags=["processing"])


@router.post(
    "/stations",
    status_code=status.HTTP_201_CREATED,
    summary="Register a processing station I run",
)
async def create_station(
    body: StationIn, actor: CurrentActor, session: SessionDep
) -> StationCreatedOut:
    return await service.create_station(session, actor, body)


@router.get("/stations", summary="Stations (mine, or all with read:station:all), with batches")
async def list_stations(
    actor: CurrentActor,
    session: SessionDep,
    page: int = 0,
    page_size: int = pagination.DEFAULT_PAGE_SIZE,
) -> list[StationOut]:
    return await service.list_stations(session, actor, page=page, page_size=page_size)


@router.get("/batches", summary="Batches at my stations")
async def list_batches(
    actor: CurrentActor,
    session: SessionDep,
    page: int = 0,
    page_size: int = pagination.DEFAULT_PAGE_SIZE,
) -> list[BatchOut]:
    return await service.list_batches(session, actor, page=page, page_size=page_size)
