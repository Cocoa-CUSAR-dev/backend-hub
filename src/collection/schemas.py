"""Request and response shapes for hubs and the harvests received there.

Field names and layout are mobile-backend's (snake_case, each hub with its
harvests nested), so the mobile app keeps working when it switches over
(Cocoa-CUSAR-dev/mobile-app#67).
"""

from uuid import UUID

from pydantic import BaseModel

from src.go_json import GoDate, GoTime
from src.registration import GisPoint


class HubIn(BaseModel):
    hub_name: str | None = None
    # YYYY-MM-DD; blank or missing means today (Bangkok).
    found_date: str = ""
    address_detail: str | None = None
    # Optional in Go's request but NOT NULL in the table (Go answered 500).
    province_id: UUID
    district_id: UUID
    subdistrict_id: UUID
    zip_code: str | None = None
    contact_name: str | None = None
    phone_number: str | None = None
    line: str | None = None
    facebook: str | None = None
    # The hub's location; only the first point is stored, as in Go.
    gis: list[GisPoint] = []


class HubCreatedOut(BaseModel):
    message: str = "ลงทะเบียน Hub สำเร็จ"
    hub_id: UUID


class HarvestOut(BaseModel):
    """One harvest with one of its grade rows: a harvest graded into several
    grades appears once per grade, as in Go."""

    harvest_id: UUID
    hub_id: UUID
    farm_id: UUID
    plot_id: UUID | None
    tree_id: UUID | None
    harvest_date: GoTime | None
    created_at: GoTime | None
    updated_at: GoTime | None
    grade_code: str | None
    quantity_kg: float | None
    grade_description: str | None
    is_clean: bool | None
    is_appropriate_size: bool | None
    weight_gram_per_pod: float | None
    is_sprout: bool | None
    is_dry: bool | None
    is_shriveled: bool | None
    cut_test_result: str | None
    collection_id: UUID | None
    batch_id: UUID | None
    # Filled on a hub's harvests (GET /hubs); null on GET /harvests, as in Go.
    farm_name: str | None = None


class HubOut(BaseModel):
    hub_id: UUID
    hub_name: str | None
    geo_id: UUID | None
    found_date: GoDate | None
    address_detail: str | None
    subdistrict_id: UUID | None
    district_id: UUID | None
    province_id: UUID | None
    zip_code: str | None
    contact_name: str | None
    phone_number: str | None
    line: str | None
    facebook: str | None
    created_at: GoTime
    updated_at: GoTime
    harvests: list[HarvestOut]
