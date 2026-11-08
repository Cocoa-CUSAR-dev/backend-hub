"""Request and response shapes for processing stations and their batches.

Field names and layout are mobile-backend's (snake_case, each station with
its batches nested), so the mobile app keeps working when it switches over.
"""

from uuid import UUID

from pydantic import BaseModel

from src.go_json import GoDate, GoTime
from src.registration import GisPoint


class StationIn(BaseModel):
    processing_station_name: str | None = None
    hub_id: UUID | None = None
    address_detail: str | None = None
    # Optional in Go's request but NOT NULL in the table (Go answered 500).
    province_id: UUID
    district_id: UUID
    subdistrict_id: UUID
    # Accepted for the app's sake but not stored, exactly as in Go.
    gis: list[GisPoint] = []


class StationCreatedOut(BaseModel):
    message: str = "สร้างสถานีแปรรูปสำเร็จ"
    station_id: UUID


class BatchOut(BaseModel):
    batch_id: UUID
    processing_station_id: UUID
    origin: str | None
    notes: str | None
    quantity_kg: float | None
    created_at: GoTime
    updated_at: GoTime


class StationOut(BaseModel):
    processing_station_id: UUID
    processing_station_name: str | None
    hub_id: UUID | None
    geo_id: UUID | None
    found_date: GoDate | None
    processing_station_area: float | None
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
    batches: list[BatchOut]
