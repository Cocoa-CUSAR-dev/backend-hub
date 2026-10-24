"""src/reports/xlsx.py: cell values as web-backend wrote them, and the cases
where Kotlin threw (a missing yes/no or number)."""

import io
from datetime import date, datetime
from decimal import Decimal

from openpyxl import load_workbook

from src.db.reports import FarmExportRow, HarvestExportRow
from src.reports import xlsx


def test_java_datetime_drops_zero_seconds_like_localdatetime() -> None:
    assert xlsx.java_datetime(datetime(2026, 10, 12, 1, 0)) == "2026-10-12T01:00"
    assert xlsx.java_datetime(datetime(2026, 10, 12, 1, 0, 30)) == "2026-10-12T01:00:30"
    assert xlsx.java_datetime(datetime(2026, 10, 12, 1, 0, 30, 500000)) == "2026-10-12T01:00:30.5"
    assert xlsx.java_datetime(None) == "-"


def _farm(**over: object) -> FarmExportRow:
    values: dict[str, object] = {
        "farm_name": "สวน",
        "found_date": date(2020, 5, 1),
        "total_area": Decimal("12.50"),
        "province_name_en": "Chumphon",
        "district_name_en": "Mueang",
        "subdistrict_name_en": "Tha Taphao",
        "contact_name": "สมชาย",
        "phone_number": "0800000001",
        "line": "somchai",
        "facebook": None,
    }
    return FarmExportRow(**(values | over))  # type: ignore[arg-type]


def test_farm_row_fills_missing_line_facebook_and_area() -> None:
    assert xlsx.farm_row(_farm()) == [
        "สวน", "2020-05-01", 12.5, "Chumphon", "Mueang", "Tha Taphao", "สมชาย", "0800000001",
        "somchai", "-",
    ]  # fmt: skip
    row = xlsx.farm_row(_farm(total_area=None, found_date=None, line=None))
    assert (row[1], row[2], row[8]) == ("-", 0.0, "-")


def test_harvest_row_writes_missing_yes_no_and_numbers_instead_of_failing() -> None:
    row = HarvestExportRow(
        harvest_date=datetime(2026, 10, 3, 1),
        farm_name="สวน",
        grade_code="A",
        quantity_kg=None,
        weight_gram_per_pod=Decimal("430"),
        is_clean=True,
        is_appropriate_size=None,
        is_sprout=False,
        is_dry=True,
        is_shriveled=None,
        cut_test_result=None,
    )
    assert xlsx.harvest_row(row) == [
        "2026-10-03T01:00", "สวน", "A", 0.0, 430.0, "Y", "-", "N", "Y", "-", "-",
    ]  # fmt: skip


def test_writer_keeps_sheet_order_and_headers() -> None:
    writer = xlsx.Writer()
    writer.sheet(xlsx.HARVEST_SHEET, xlsx.HARVEST_HEADERS)
    writer.rows([["2026-10-03T01:00", "สวน", "A", 1.0, 2.0, "Y", "Y", "N", "Y", "N", "ok"]])
    writer.sheet(xlsx.FARM_SHEET, xlsx.FARM_HEADERS)

    wb = load_workbook(io.BytesIO(writer.finish()), read_only=True)

    assert wb.sheetnames == ["Raw Harvest Data", "Farm Registry"]
    harvest = list(wb["Raw Harvest Data"].iter_rows(values_only=True))
    assert list(harvest[0]) == xlsx.HARVEST_HEADERS
    assert harvest[1][2] == "A"
    assert list(next(wb["Farm Registry"].iter_rows(values_only=True))) == xlsx.FARM_HEADERS
