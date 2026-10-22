from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from src.admin import service
from src.auth.dependencies import CurrentActor, SessionDep

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

AnyRole = Literal["farmer", "hub_collector", "processor", "researcher", "admin"]


class UserRolesOut(BaseModel):
    user_id: UUID
    roles: list[str]


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
