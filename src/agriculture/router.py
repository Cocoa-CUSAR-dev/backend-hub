"""/api/v1/agriculture -- farms, plots and farm photos.

Replaces mobile-backend's POST/GET /farms, POST /farms/:farm_id/image and
POST/GET /plots (AGR-01..05 in the API migration map).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status

from src.agriculture import service
from src.agriculture.schemas import (
    FarmCreatedOut,
    FarmImageOut,
    FarmIn,
    FarmOut,
    PlotCreatedOut,
    PlotIn,
    PlotOut,
)
from src.auth.dependencies import CurrentActor, SessionDep

router = APIRouter(prefix="/api/v1/agriculture", tags=["agriculture"])


@router.post(
    "/farms",
    status_code=status.HTTP_201_CREATED,
    summary="Register a farm I own, with its location or outline",
)
async def create_farm(body: FarmIn, actor: CurrentActor, session: SessionDep) -> FarmCreatedOut:
    return await service.create_farm(session, actor, body)


@router.get("/farms", summary="Farms (mine, or all with read:farm:all), with plots and photo link")
async def list_farms(
    actor: CurrentActor,
    session: SessionDep,
    page: int = 0,
    page_size: int = service.DEFAULT_PAGE_SIZE,
) -> list[FarmOut]:
    return await service.list_farms(session, actor, page=page, page_size=page_size)


@router.post("/farms/{farm_id}/image", summary="Upload a photo of my farm (JPG/PNG/WEBP, 5 MB)")
async def upload_farm_image(
    farm_id: UUID,
    image: Annotated[UploadFile, File(description="The photo, as multipart field 'image'")],
    actor: CurrentActor,
    session: SessionDep,
) -> FarmImageOut:
    # One byte past the limit is enough for the service to refuse it, so a
    # huge upload is never read into memory whole.
    data = await image.read(service.MAX_IMAGE_BYTES + 1)
    return await service.upload_farm_image(
        session, actor, farm_id, filename=image.filename or "", data=data
    )


@router.post(
    "/plots",
    status_code=status.HTTP_201_CREATED,
    summary="Register a plot on one of my farms",
)
async def create_plot(body: PlotIn, actor: CurrentActor, session: SessionDep) -> PlotCreatedOut:
    return await service.create_plot(session, actor, body)


@router.get("/plots", summary="Plots on my farms (or all with read:farm:all)")
async def list_plots(
    actor: CurrentActor,
    session: SessionDep,
    page: int = 0,
    page_size: int = service.DEFAULT_PAGE_SIZE,
) -> list[PlotOut]:
    return await service.list_plots(session, actor, page=page, page_size=page_size)
