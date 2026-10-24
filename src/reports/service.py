"""The raw-data Excel download (RPT-01), ported from web-backend's
ReportController.downloadRawData and XlsxService.

Two sheets, FARM (farm registry) and HARVEST (one row per harvest and grade);
no `sheets` means both, in that order. Rows are read a page at a time and
streamed into a write-only workbook, as Kotlin meant to: its export answered
400 for every request (it paged 1000 rows where its PageRequest allows 500),
and its harvest sheet threw on a missing yes/no or number.
"""

from dataclasses import dataclass

from src import clock, db
from src.actor import Actor, require
from src.database import Session
from src.exceptions import Invalid
from src.reports import xlsx

SHEETS = ("FARM", "HARVEST")
PAGE_SIZE = 500
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True, slots=True)
class Export:
    content: bytes
    filename: str


def _sheets(requested: list[str] | None) -> list[str]:
    """The sheets asked for, upper-cased, in the order asked; every sheet when
    none. `sheets=FARM,HARVEST` and `sheets=FARM&sheets=HARVEST` both work."""
    if not requested:
        return list(SHEETS)
    names = [n.strip().upper() for value in requested for n in value.split(",") if n.strip()]
    unknown = [n for n in names if n not in SHEETS]
    if unknown or not names:
        raise Invalid(fields={"sheets": f"Use one or more of {', '.join(SHEETS)}"})
    return list(dict.fromkeys(names))


async def raw_data_xlsx(session: Session, actor: Actor, sheets: list[str] | None = None) -> Export:
    require(actor, "read:report:all")
    chosen = _sheets(sheets)
    writer = xlsx.Writer()
    for name in chosen:
        if name == "FARM":
            writer.sheet(xlsx.FARM_SHEET, xlsx.FARM_HEADERS)
            offset = 0
            while farms := await db.reports.farm_export_page(
                session, limit=PAGE_SIZE, offset=offset
            ):
                writer.rows(xlsx.farm_row(f) for f in farms)
                offset += PAGE_SIZE
        else:
            writer.sheet(xlsx.HARVEST_SHEET, xlsx.HARVEST_HEADERS)
            offset = 0
            while harvests := await db.reports.harvest_export_page(
                session, limit=PAGE_SIZE, offset=offset
            ):
                writer.rows(xlsx.harvest_row(h) for h in harvests)
                offset += PAGE_SIZE
    stamp = clock.bangkok_now().strftime("%Y%m%d_%H%M")
    return Export(content=writer.finish(), filename=f"raw_data_{stamp}.xlsx")
