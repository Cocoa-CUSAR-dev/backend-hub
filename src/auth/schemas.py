from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str = Field(min_length=1, examples=["0812345678"])
    password: str = Field(min_length=1)


class RegisterIn(BaseModel):
    username: str = Field(min_length=1, examples=["0812345678"])
    password: str


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1)


class ResetPasswordIn(BaseModel):
    new_password: str


class SessionUser(BaseModel):
    user_id: UUID
    username: str
    roles: list[str]
    must_reset_password: bool = Field(
        description="Admin-created account: send the user to reset-password first."
    )


class TokenPairOut(BaseModel):
    """What every login-like endpoint returns, for web and mobile alike."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Seconds until access_token expires.")
    user: SessionUser
