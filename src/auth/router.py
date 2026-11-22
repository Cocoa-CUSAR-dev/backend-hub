from fastapi import APIRouter, BackgroundTasks, status

from src.auth import password_reset, service
from src.auth.dependencies import CurrentActor, SessionDep
from src.auth.schemas import (
    ForgotPasswordConfirmIn,
    ForgotPasswordIn,
    ForgotPasswordOut,
    LineLinkIn,
    LineLoginIn,
    LoginIn,
    RefreshIn,
    RegisterIn,
    ResetPasswordIn,
    SsoExchangeIn,
    TokenPairOut,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Create an account (no role yet) and sign in",
)
async def register(body: RegisterIn, session: SessionDep) -> TokenPairOut:
    return await service.register(session, body.username, body.password, body.email)


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


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask for a code to reset a forgotten password (sent by LINE or email)",
)
async def forgot_password(body: ForgotPasswordIn, background: BackgroundTasks) -> ForgotPasswordOut:
    background.add_task(password_reset.send_code, body.login)
    return ForgotPasswordOut(message=password_reset.NEUTRAL_MESSAGE)


@router.post(
    "/forgot-password/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password with the code; signs out every device",
)
async def forgot_password_confirm(body: ForgotPasswordConfirmIn, session: SessionDep) -> None:
    await password_reset.confirm(session, body.login, body.code, body.new_password)


@router.post("/line/login", summary="LIFF: swap a LINE ID token for a token pair")
async def line_login(body: LineLoginIn, session: SessionDep) -> TokenPairOut:
    return await service.line_login(session, body.id_token)


@router.post("/line/link", summary="Link a LINE account to an existing account and sign in")
async def line_link(body: LineLinkIn, session: SessionDep) -> TokenPairOut:
    return await service.line_link(session, body.id_token, body.username, body.password)


@router.post("/sso/exchange", summary="Redeem the one-time link from the LINE diary card")
async def sso_exchange(body: SsoExchangeIn, session: SessionDep) -> TokenPairOut:
    return await service.sso_exchange(session, body.token)
