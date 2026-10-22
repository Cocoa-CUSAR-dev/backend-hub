"""Profiles for each role a person can hold. One model per role, all fields
optional for PATCH; the *Create models make the DB's NOT NULL columns
required. Field names match the table columns (src/db/*: *_COLUMNS).
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.auth.schemas import TokenPairOut

SelfServiceRole = Literal["farmer", "hub_collector", "processor", "researcher"]


class _Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def values(self) -> dict[str, Any]:
        """Only the fields the client actually sent."""
        return self.model_dump(exclude_unset=True)


class _Contact(_Profile):
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    id_card_number: str | None = None
    phone_number: str | None = None
    line: str | None = None
    facebook: str | None = None
    subdistrict_id: UUID | None = None
    district_id: UUID | None = None
    province_id: UUID | None = None
    zip_code: str | None = None


class FarmerProfile(_Contact):
    birth_date: date | None = None
    nationality: str | None = None
    ethnicity: str | None = None
    religion: str | None = None
    address_detail: str | None = None
    salary_income: Decimal | None = None
    family_member_count: int | None = Field(default=None, ge=1)
    agri_worker_count: int | None = Field(default=None, ge=0)
    agri_experience: date | None = None


class FarmerProfileCreate(FarmerProfile):
    first_name: str
    last_name: str
    # agriculture.farmer has these three NOT NULL.
    subdistrict_id: UUID
    district_id: UUID
    province_id: UUID


class ProcessorProfile(_Contact):
    birth_date: date | None = None
    address_detail: str | None = None


class ProcessorProfileCreate(ProcessorProfile):
    first_name: str
    last_name: str


class HubCollectorProfile(_Contact):
    birthdate: date | None = None  # the column really is "birthdate" here


class HubCollectorProfileCreate(HubCollectorProfile):
    first_name: str
    last_name: str


class ResearcherProfile(_Profile):
    first_name: str | None = None
    last_name: str | None = None
    organization: str | None = None


class ResearcherSignup(_Profile):
    signup_code: str = Field(description="Given out by the project team.")
    first_name: str
    last_name: str
    organization: str

    def values(self) -> dict[str, Any]:
        return self.model_dump(exclude={"signup_code"})


class Profiles(BaseModel):
    """Each role's profile on its own -- never merged. null = not that role."""

    farmer: dict[str, Any] | None = None
    hub_collector: dict[str, Any] | None = None
    processor: dict[str, Any] | None = None
    researcher: dict[str, Any] | None = None


class MeOut(BaseModel):
    user_id: UUID
    username: str
    roles: list[str]
    must_reset_password: bool
    profiles: Profiles


class RoleAddedOut(TokenPairOut):
    """New tokens, so the new role works on the very next request."""
