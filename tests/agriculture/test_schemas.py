from datetime import date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.agriculture.schemas import FarmIn, PlotOut


def test_farm_in_requires_what_the_table_needs() -> None:
    with pytest.raises(ValidationError) as caught:
        FarmIn.model_validate({"farm_name": "  ", "zip_code": "50000"})

    missing = {error["loc"][0] for error in caught.value.errors()}
    assert {
        "farm_name",
        "province_id",
        "district_id",
        "subdistrict_id",
        "contact_name",
        "phone_number",
    } <= missing


def test_plot_out_writes_dates_and_times_the_way_go_did() -> None:
    plot = PlotOut(
        plot_id=uuid4(),
        farm_id=uuid4(),
        plot_name="A",
        total_area=1.0,
        land_ownership=None,
        cocoa_planted_area=None,
        has_chemical_use=None,
        land_type_id=None,
        soil_type_id=None,
        water_source_id=None,
        watering_system_id=None,
        found_date=date(2026, 1, 2),
        created_at=datetime(2026, 10, 1, 3, 4, 5),
        updated_at=datetime(2026, 10, 1, 3, 4, 5),
    )

    data = plot.model_dump(mode="json")

    assert data["found_date"] == "2026-01-02T00:00:00Z"
    assert data["created_at"] == "2026-10-01T03:04:05Z"
