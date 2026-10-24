"""The raw-data workbook: sheet names, headers and cell values, as web-backend's
XlsxService wrote them, so researchers' existing files and formulas keep working.

Write-only mode streams rows to the file instead of keeping cells in memory,
like Kotlin's SXSSFWorkbook did. Pure functions: no database, no permissions.
"""

import io
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook

from src.db.reports import FarmExportRow, HarvestExportRow

FARM_SHEET = "Farm Registry"
FARM_HEADERS = [
    "Farm Name",
    "Found Date",
    "Area",
    "Province",
    "District",
    "Sub-District",
    "Contact",
    "Phone",
    "Line",
    "Facebook",
]
HARVEST_SHEET = "Raw Harvest Data"
HARVEST_HEADERS = [
    "Date",
    "Farm",
    "Grade",
    "Kg",
    "Gram/Pod",
    "Clean",
    "Size OK",
    "Sprout",
    "Dry",
    "Shriveled",
    "Cut Test",
]

MISSING = "-"


def java_datetime(moment: datetime | None) -> str:
    """A time as Java's LocalDateTime.toString() wrote it in Kotlin's file:
    seconds only when not zero, fractions only when there are some."""
    if moment is None:
        return MISSING
    text = moment.strftime("%Y-%m-%dT%H:%M")
    if moment.second or moment.microsecond:
        text += moment.strftime(":%S")
    if moment.microsecond:
        text += f".{moment.microsecond:06d}".rstrip("0")
    return text


def _number(value: Decimal | None) -> float:
    # Kotlin wrote a missing area as 0.0; the same for kg and gram/pod.
    return float(value) if value is not None else 0.0


def _yes_no(value: bool | None) -> str:
    if value is None:
        return MISSING
    return "Y" if value else "N"


def farm_row(row: FarmExportRow) -> list[object]:
    return [
        row.farm_name,
        row.found_date.isoformat() if row.found_date else MISSING,
        _number(row.total_area),
        row.province_name_en,
        row.district_name_en,
        row.subdistrict_name_en,
        row.contact_name,
        row.phone_number,
        row.line or MISSING,
        row.facebook or MISSING,
    ]


def harvest_row(row: HarvestExportRow) -> list[object]:
    return [
        java_datetime(row.harvest_date),
        row.farm_name,
        row.grade_code,
        _number(row.quantity_kg),
        _number(row.weight_gram_per_pod),
        _yes_no(row.is_clean),
        _yes_no(row.is_appropriate_size),
        _yes_no(row.is_sprout),
        _yes_no(row.is_dry),
        _yes_no(row.is_shriveled),
        row.cut_test_result or MISSING,
    ]


class Writer:
    """Builds the workbook a page of rows at a time, so the service can append
    each page as it reads it instead of holding every row first."""

    def __init__(self) -> None:
        self._wb = Workbook(write_only=True)
        self._ws: Any = None

    def sheet(self, title: str, headers: list[str]) -> None:
        self._ws = self._wb.create_sheet(title)
        self._ws.append(headers)

    def rows(self, rows: Iterable[list[object]]) -> None:
        for row in rows:
            self._ws.append(row)

    def finish(self) -> bytes:
        out = io.BytesIO()
        self._wb.save(out)
        return out.getvalue()
