from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from src.admin import service
from src.auth.dependencies import CurrentActor, SessionDep

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

AnyRole = Literal["farmer", "hub_collector", "processor", "researcher", "admin"]


class UserRolesOut(BaseModel):
    user_id: UUID
    roles: list[str]


class CreateUserIn(BaseModel):
    username: str = Field(min_length=1)
    temporary_password: str = Field(
        description="They must change it at first sign-in (must_reset_password)."
    )
    role: Literal["researcher", "admin"] = "researcher"
    first_name: str | None = None
    last_name: str | None = None
    organization: str | None = None


@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    summary="Create an account for a researcher or admin (must reset password)",
)
async def create_user(body: CreateUserIn, actor: CurrentActor, session: SessionDep) -> UserRolesOut:
    profile = body.model_dump(include={"first_name", "last_name", "organization"})
    user_id, roles = await service.create_user(
        session, actor, body.username, body.temporary_password, body.role, profile
    )
    return UserRolesOut(user_id=user_id, roles=roles)


@router.post(
    "/users/{user_id}/roles/{role}",
    summary="Grant researcher or admin to a user",
)
async def grant_role(
    user_id: UUID,
    role: Literal["researcher", "admin"],
    actor: CurrentActor,
    session: SessionDep,
) -> UserRolesOut:
    roles = await service.grant_role(session, actor, user_id, role)
    return UserRolesOut(user_id=user_id, roles=roles)


@router.delete(
    "/users/{user_id}/roles/{role}",
    summary="Remove a role from a user (signs them out everywhere)",
)
async def remove_role(
    user_id: UUID, role: AnyRole, actor: CurrentActor, session: SessionDep
) -> UserRolesOut:
    roles = await service.remove_role(session, actor, user_id, role)
    return UserRolesOut(user_id=user_id, roles=roles)


# ---- researchers (ADMIN-02..04) -------------------------------------------------------
# web-backend's Researcher.Entity keys, which an admin screen would read.


class ResearcherOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    user_id: UUID
    first_name: str | None
    last_name: str | None
    organization: str | None


@router.get(
    "/researchers",
    summary="Researchers, filtered by exact name or organization (ADMIN-02)",
    response_model=list[ResearcherOut],
)
async def researchers(
    actor: CurrentActor,
    session: SessionDep,
    first_name: Annotated[str | None, Query(alias="firstName")] = None,
    last_name: Annotated[str | None, Query(alias="lastName")] = None,
    organization: str | None = None,
    page: int = 0,
    size: int = service.DEFAULT_PAGE_SIZE,
) -> list[ResearcherOut]:
    found = await service.researchers(
        session,
        actor,
        first_name=first_name,
        last_name=last_name,
        organization=organization,
        page=page,
        size=size,
    )
    return [ResearcherOut.model_validate(r) for r in found]


@router.get(
    "/researchers/{user_id}", summary="One researcher (ADMIN-03)", response_model=ResearcherOut
)
async def researcher(user_id: UUID, actor: CurrentActor, session: SessionDep) -> ResearcherOut:
    return ResearcherOut.model_validate(await service.researcher(session, actor, user_id))


@router.delete(
    "/researchers/{user_id}",
    summary="Remove the researcher role (profile kept); signs them out (ADMIN-04)",
)
async def delete_researcher(user_id: UUID, actor: CurrentActor, session: SessionDep) -> str:
    return await service.delete_researcher(session, actor, user_id)
