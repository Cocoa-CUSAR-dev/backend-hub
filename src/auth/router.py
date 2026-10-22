from fastapi import APIRouter, status

from src.auth import service
from src.auth.dependencies import CurrentActor, SessionDep
from src.auth.schemas import LoginIn, RefreshIn, RegisterIn, ResetPasswordIn, TokenPairOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Create an account (no role yet) and sign in",
)
async def register(body: RegisterIn, session: SessionDep) -> TokenPairOut:
    return await service.register(session, body.username, body.password)


@router.post("/login", summary="Sign in with username + password")
async def login(body: LoginIn, session: SessionDep) -> TokenPairOut:
    return await service.login(session, body.username, body.password)


@router.post("/refresh", summary="Swap a refresh token for a new token pair")
async def refresh(body: RefreshIn, session: SessionDep) -> TokenPairOut:
    return await service.refresh(session, body.refresh_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke this device's refresh token",
)
async def logout(body: RefreshIn, session: SessionDep) -> None:
    await service.logout(session, body.refresh_token)


@router.patch(
    "/reset-password",
    summary="Change my password; signs out every other device",
)
async def reset_password(
    body: ResetPasswordIn, actor: CurrentActor, session: SessionDep
) -> TokenPairOut:
    return await service.reset_password(session, actor, body.new_password)
