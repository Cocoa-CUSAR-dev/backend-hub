"""/api/v1/collection -- collection hubs and the harvests received there.

Replaces mobile-backend's POST/GET /hubs and GET /harvests (COL-01..03 in
the API migration map).
"""

from fastapi import APIRouter, status

from src import pagination
from src.auth.dependencies import CurrentActor, SessionDep
from src.collection import service
from src.collection.schemas import HarvestOut, HubCreatedOut, HubIn, HubOut

router = APIRouter(prefix="/api/v1/collection", tags=["collection"])


@router.post(
    "/hubs",
    status_code=status.HTTP_201_CREATED,
    summary="Register my collection hub, with its location",
)
async def create_hub(body: HubIn, actor: CurrentActor, session: SessionDep) -> HubCreatedOut:
    return await service.create_hub(session, actor, body)


@router.get("/hubs", summary="Hubs (mine, or all with read:hub:all), with their harvests")
async def list_hubs(
    actor: CurrentActor,
    session: SessionDep,
    page: int = 0,
    page_size: int = pagination.DEFAULT_PAGE_SIZE,
) -> list[HubOut]:
    return await service.list_hubs(session, actor, page=page, page_size=page_size)


@router.get("/harvests", summary="Harvests received at my hub, newest first, one row per grade")
async def list_harvests(
    actor: CurrentActor,
    session: SessionDep,
    page: int = 0,
    page_size: int = pagination.DEFAULT_PAGE_SIZE,
) -> list[HarvestOut]:
    return await service.list_harvests(session, actor, page=page, page_size=page_size)
