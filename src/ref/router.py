"""/api/v1/ref -- dropdown choices for the mobile app's forms.

Replaces mobile-backend's GET /constants/:key (REF-01 in the API migration map).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Query

from src.auth.dependencies import CurrentActor, SessionDep
from src.ref import service

router = APIRouter(prefix="/api/v1/ref", tags=["ref"])

_Param = Annotated[str | None, Query()]


@router.get(
    "/constants/{key}",
    summary="The choices for one dropdown (a form field's name minus _id), in Go's shape",
)
async def constants(
    key: str,
    actor: CurrentActor,
    session: SessionDep,
    province_id: _Param = None,
    district_id: _Param = None,
    farm_id: _Param = None,
    hub_id: _Param = None,
    station_id: _Param = None,
    zip_code: _Param = None,
    subdistrict_id: _Param = None,
) -> list[dict[str, Any]]:
    filters = service.Filters(
        province_id=province_id,
        district_id=district_id,
        farm_id=farm_id,
        hub_id=hub_id,
        station_id=station_id,
        zip_code=zip_code,
        subdistrict_id=subdistrict_id,
    )
    return await service.constants(session, actor, key, filters)
