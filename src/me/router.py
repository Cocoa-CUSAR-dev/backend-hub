"""/api/v1/me -- my account, my roles, my profile for each role.

Adding a role and editing a profile have one route per role so each shows
its own form in the API docs; they all call the same two service functions.
"""

from typing import Any

from fastapi import APIRouter, status

from src.auth.dependencies import CurrentActor, SessionDep
from src.me import service
from src.me.schemas import (
    FarmerProfile,
    FarmerProfileCreate,
    HubCollectorProfile,
    HubCollectorProfileCreate,
    MeOut,
    ProcessorProfile,
    ProcessorProfileCreate,
    ResearcherProfile,
    ResearcherSignup,
    RoleAddedOut,
)

router = APIRouter(prefix="/api/v1/me", tags=["me"])

_ADDED = status.HTTP_201_CREATED


@router.get("", summary="Me: every role I hold and each role's profile")
async def me(actor: CurrentActor, session: SessionDep) -> MeOut:
    return await service.me(session, actor)


# ---- add a role ------------------------------------------------------------


@router.post("/roles/farmer", status_code=_ADDED, summary="Become a farmer too")
async def add_farmer(
    body: FarmerProfileCreate, actor: CurrentActor, session: SessionDep
) -> RoleAddedOut:
    return await service.add_role(session, actor, "farmer", body.values())


@router.post("/roles/hub_collector", status_code=_ADDED, summary="Become a hub collector too")
async def add_hub_collector(
    body: HubCollectorProfileCreate, actor: CurrentActor, session: SessionDep
) -> RoleAddedOut:
    return await service.add_role(session, actor, "hub_collector", body.values())


@router.post("/roles/processor", status_code=_ADDED, summary="Become a processor too")
async def add_processor(
    body: ProcessorProfileCreate, actor: CurrentActor, session: SessionDep
) -> RoleAddedOut:
    return await service.add_role(session, actor, "processor", body.values())


@router.post(
    "/roles/researcher",
    status_code=_ADDED,
    summary="Become a researcher (needs the researcher sign-up code)",
)
async def add_researcher(
    body: ResearcherSignup, actor: CurrentActor, session: SessionDep
) -> RoleAddedOut:
    return await service.add_role(
        session, actor, "researcher", body.values(), signup_code=body.signup_code
    )


# ---- edit one role's profile -------------------------------------------------


@router.patch("/profiles/farmer", summary="Edit my farmer profile")
async def edit_farmer(
    body: FarmerProfile, actor: CurrentActor, session: SessionDep
) -> dict[str, Any]:
    return await service.update_profile(session, actor, "farmer", body.values())


@router.patch("/profiles/hub_collector", summary="Edit my hub collector profile")
async def edit_hub_collector(
    body: HubCollectorProfile, actor: CurrentActor, session: SessionDep
) -> dict[str, Any]:
    return await service.update_profile(session, actor, "hub_collector", body.values())


@router.patch("/profiles/processor", summary="Edit my processor profile")
async def edit_processor(
    body: ProcessorProfile, actor: CurrentActor, session: SessionDep
) -> dict[str, Any]:
    return await service.update_profile(session, actor, "processor", body.values())


@router.patch("/profiles/researcher", summary="Edit my researcher profile")
async def edit_researcher(
    body: ResearcherProfile, actor: CurrentActor, session: SessionDep
) -> dict[str, Any]:
    return await service.update_profile(session, actor, "researcher", body.values())
