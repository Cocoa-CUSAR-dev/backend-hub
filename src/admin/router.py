from typing import Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

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
