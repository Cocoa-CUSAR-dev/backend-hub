"""Request and response shapes for farms and plots.

Field names and the JSON layout are mobile-backend's (snake_case, farms with
their plots nested, image_url on each farm), so the mobile app's forms and
screens keep working when they switch over (Cocoa-CUSAR-dev/mobile-app#67).
Unlike Go, bad input fails here with 422 and a reason per field, instead of
reaching the database and coming back as a 500.
"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from src.go_json import GoDate, GoTime
from src.registration import GisPoint

Required = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FarmIn(BaseModel):
    farm_name: Required
    hub_id: UUID | None = None
    # YYYY-MM-DD; blank or missing means today (Bangkok).
    found_date: str = ""
    address_detail: str | None = None
    # Optional in Go's request, but NOT NULL in the table: Go answered 500
    # when they were missing. Required here instead.
    province_id: UUID
    district_id: UUID
    subdistrict_id: UUID
    zip_code: Required
    contact_name: Required
    phone_number: Required
    line: str | None = None
    facebook: str | None = None
    # One point = the farm's location; two or more = its outline.
    gis: list[GisPoint] = []


class PlotIn(BaseModel):
    farm_id: UUID
    plot_name: Required
    land_ownership: Required
    cocoa_planted_area: float
    has_chemical_use: bool = False
    land_type_id: UUID | None = None
    soil_type_id: UUID | None = None
    water_source_id: UUID | None = None
    watering_system_id: UUID | None = None
    found_date: str = ""
    # The plot outline (two or more points) and its area as the app measured it.
    gis: list[GisPoint] = []
    gis_area_m2: float = Field(default=0, ge=0)


class FarmCreatedOut(BaseModel):
    message: str = "ลงทะเบียนฟาร์มและเจ้าของสำเร็จ"
    farm_id: UUID


class PlotCreatedOut(BaseModel):
    message: str = "ลงทะเบียนแปลงสำเร็จ"
    plot_id: UUID


class FarmImageOut(BaseModel):
    message: str = "อัปโหลดรูปฟาร์มสำเร็จ"
    farm_id: UUID
    # A link valid for an hour; null if signing it failed.
    image_url: str | None


class PlotOut(BaseModel):
    plot_id: UUID
    farm_id: UUID
    plot_name: str | None
    total_area: float | None
    land_ownership: str | None
    cocoa_planted_area: float | None
    has_chemical_use: bool | None
    land_type_id: UUID | None
    soil_type_id: UUID | None
    water_source_id: UUID | None
    watering_system_id: UUID | None
    found_date: GoDate | None
    created_at: GoTime
    updated_at: GoTime


class FarmOut(BaseModel):
    farm_id: UUID
    farm_name: str | None
    hub_id: UUID | None
    geo_id: UUID | None
    found_date: GoDate | None
    total_area: float | None
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
    plots: list[PlotOut]
    # A link to the farm's newest photo, valid for an hour; null if none.
    image_url: str | None
