"""/api/v1/reports -- the raw-data Excel download for researchers.

Replaces web-backend's GET /reports/raw-data/download (RPT-01 in the API
migration map).
"""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from src.auth.dependencies import CurrentActor, SessionDep
from src.reports import service

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


@router.get(
    "/raw-data",
    summary="Download farms and harvests as an Excel file",
    response_class=Response,
    responses={200: {"content": {service.MEDIA_TYPE: {}}, "description": "The .xlsx file"}},
)
async def raw_data_xlsx(
    actor: CurrentActor,
    session: SessionDep,
    sheets: Annotated[
        list[str] | None,
        Query(description="FARM, HARVEST or both (comma-separated or repeated); default both"),
    ] = None,
) -> Response:
    export = await service.raw_data_xlsx(session, actor, sheets)
    return Response(
        content=export.content,
        media_type=service.MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename={export.filename}"},
    )
