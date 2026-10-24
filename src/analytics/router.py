"""/api/v1/analytics -- the researchers' dashboard charts and totals.

Replaces web-backend's 15 /analytics routes with six, the mode in the path
(ANL-01..06 in the API migration map). Query names stay the web app's
(provinceId, gradeCode, roleName, ...).
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from src.analytics import service
from src.analytics.schemas import AreaIn, SummaryOut, TimeSeriesOut
from src.auth.dependencies import CurrentActor, SessionDep

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

From = Annotated[str | None, Query(alias="from", description="First month, yyyy-MM")]
To = Annotated[str | None, Query(description="Last month, yyyy-MM")]
Grade = Annotated[str | None, Query(alias="gradeCode", description="One grade, e.g. A")]
Province = Annotated[UUID | None, Query(alias="provinceId")]
District = Annotated[UUID | None, Query(alias="districtId")]
Subdistrict = Annotated[UUID | None, Query(alias="subdistrictId")]
Farm = Annotated[UUID | None, Query(alias="farmId")]
Role = Annotated[str | None, Query(alias="roleName", description="Only this role, e.g. farmer")]


@router.get(
    "/harvest/time-series/{mode}",
    summary="Harvest per month and grade: running total, per month, average or count",
    response_model=TimeSeriesOut,
)
async def harvest_series(
    mode: Literal["sum", "delta", "average", "frequency"],
    actor: CurrentActor,
    session: SessionDep,
    from_: From = None,
    to: To = None,
    province_id: Province = None,
    district_id: District = None,
    subdistrict_id: Subdistrict = None,
    farm_id: Farm = None,
    grade_code: Grade = None,
) -> TimeSeriesOut:
    place = service.Place(province_id, district_id, subdistrict_id, farm_id)
    return await service.harvest_series(
        session, actor, mode, from_=from_, to=to, place=place, grade_code=grade_code
    )


@router.get(
    "/harvest/summary/{mode}",
    summary="Harvest total weight or count over the range",
    response_model=SummaryOut,
)
async def harvest_summary(
    mode: Literal["total", "count"],
    actor: CurrentActor,
    session: SessionDep,
    from_: From = None,
    to: To = None,
    province_id: Province = None,
    district_id: District = None,
    subdistrict_id: Subdistrict = None,
    farm_id: Farm = None,
    grade_code: Grade = None,
) -> SummaryOut:
    place = service.Place(province_id, district_id, subdistrict_id, farm_id)
    return await service.harvest_summary(
        session, actor, mode, from_=from_, to=to, place=place, grade_code=grade_code
    )


@router.post(
    "/harvest/spatial/time-series/{mode}",
    summary="Harvest per month and grade inside a drawn area (map dashboard)",
    response_model=TimeSeriesOut,
)
async def harvest_series_in_area(
    mode: Literal["sum", "delta", "average", "frequency"],
    actor: CurrentActor,
    session: SessionDep,
    body: AreaIn,
    from_: From = None,
    to: To = None,
    grade_code: Grade = None,
) -> TimeSeriesOut:
    return await service.harvest_series(
        session, actor, mode, from_=from_, to=to, grade_code=grade_code, area=body.geo_json
    )


@router.post(
    "/harvest/spatial/summary/{mode}",
    summary="Harvest total weight or count inside a drawn area (map dashboard)",
    response_model=SummaryOut,
)
async def harvest_summary_in_area(
    mode: Literal["total", "count"],
    actor: CurrentActor,
    session: SessionDep,
    body: AreaIn,
    from_: From = None,
    to: To = None,
    grade_code: Grade = None,
) -> SummaryOut:
    return await service.harvest_summary(
        session, actor, mode, from_=from_, to=to, grade_code=grade_code, area=body.geo_json
    )


@router.get(
    "/users/time-series/{mode}",
    summary="New accounts per month: running total or per month",
    response_model=TimeSeriesOut,
)
async def user_series(
    mode: Literal["sum", "delta"],
    actor: CurrentActor,
    session: SessionDep,
    from_: From = None,
    to: To = None,
    role_name: Role = None,
) -> TimeSeriesOut:
    return await service.user_series(session, actor, mode, from_=from_, to=to, role_name=role_name)


@router.get(
    "/users/summary/{mode}",
    summary="New accounts over the range",
    response_model=SummaryOut,
)
async def user_summary(
    mode: Literal["count"],
    actor: CurrentActor,
    session: SessionDep,
    from_: From = None,
    to: To = None,
    role_name: Role = None,
) -> SummaryOut:
    return await service.user_summary(session, actor, mode, from_=from_, to=to, role_name=role_name)
