"""Farmers, farms, plots and every agriculture handler table.

Owns:
    agriculture.farmer
    agriculture.farm
    agriculture.farmer_farm
    agriculture.plot
    agriculture.plot_breed
    agriculture.farm_activity
    agriculture.farm_activity_chemical
    agriculture.farm_activity_fertilizer
    agriculture.farm_pest_disease_record
    agriculture.farm_initial_cost
    agriculture.farm_economic_eval

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import _profiles

FARMER_COLUMNS = frozenset(
    {
        "first_name", "last_name", "nickname", "birth_date", "nationality", "ethnicity",
        "religion", "id_card_number", "address_detail", "subdistrict_id", "district_id",
        "province_id", "zip_code", "phone_number", "line", "facebook", "salary_income",
        "family_member_count", "agri_worker_count", "agri_experience",
    }
)  # fmt: skip


async def farmer_profile(session: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    return await _profiles.get(session, "agriculture.farmer", FARMER_COLUMNS, user_id)


async def create_farmer_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    return await _profiles.insert(session, "agriculture.farmer", FARMER_COLUMNS, user_id, values)


async def update_farmer_profile(
    session: AsyncSession, user_id: UUID, values: dict[str, Any]
) -> bool:
    return await _profiles.update(session, "agriculture.farmer", FARMER_COLUMNS, user_id, values)
