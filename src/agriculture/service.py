"""Farms and plots: register them, list them, and attach a farm photo.

Ported from mobile-backend's agriculture_handler.go and farm_image_handler.go.
One function per action, shared by every door (the mobile app now, the LINE
farm and plot pickers once the chatbot calls these, docs-and-plan#259).

Permissions (docs-and-plan#265): create:farm:own (a farm or a plot on your
own farm), read:farm:own (your farms and plots), read:farm:all (everyone's),
update:farm:own (your farm's photo). Go gated these routes on the farmer
role instead.
"""

import hashlib
import logging
import os.path
from dataclasses import asdict
from datetime import date
from uuid import UUID, uuid4

from src import clock, db
from src.actor import Actor, require
from src.agriculture.schemas import (
    FarmCreatedOut,
    FarmImageOut,
    FarmIn,
    FarmOut,
    GisPoint,
    PlotCreatedOut,
    PlotIn,
    PlotOut,
)
from src.database import Session, transaction
from src.exceptions import Forbidden, Invalid, NotFound, Unavailable
from src.r2 import client as r2

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500

# One rai is 1,600 m^2; plot areas are stored in rai.
SQ_M_PER_RAI = 1600

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_URL_SECONDS = 60 * 60
# storage.file rows for farm photos: which table and kind they belong to.
FARM_IMAGE_TABLE = "agriculture.farm"
FARM_IMAGE_CODE = "farm_image"


def _caller(actor: Actor) -> UUID:
    if actor.user_id is None:
        raise Forbidden("A user is required")
    return actor.user_id


def _page(page: int, page_size: int) -> tuple[int, int]:
    """(limit, offset), falling back the way Go did instead of failing."""
    if page_size <= 0:
        page_size = DEFAULT_PAGE_SIZE
    page_size = min(page_size, MAX_PAGE_SIZE)
    return page_size, max(page, 0) * page_size


def _found_date(value: str) -> date:
    """Blank means today (the farmer's day). Go silently stored year 1 for a
    malformed date; this refuses it instead."""
    if not value.strip():
        return clock.bangkok_today()
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise Invalid(fields={"found_date": "Use YYYY-MM-DD"}) from None


def _wkt_point(point: GisPoint) -> str:
    return f"POINT({point.lng:f} {point.lat:f})"


def _wkt_polygon(points: list[GisPoint]) -> str:
    """A closed outline: the first point is repeated at the end. Coordinates
    are lon lat with six decimals, as Go's %f wrote them. Needs 3+ points:
    PostGIS rejects a ring with fewer than 4 (3 distinct + closing), which
    used to roll the whole registration back as a 500."""
    if len(points) < 3:
        raise Invalid(fields={"gis": "An outline needs at least 3 points"})
    ring = [*points, points[0]]
    return "POLYGON((" + ",".join(f"{p.lng:f} {p.lat:f}" for p in ring) + "))"


async def create_farm(session: Session, actor: Actor, farm: FarmIn) -> FarmCreatedOut:
    """Register a farm owned by the caller, with its location or outline."""
    require(actor, "create:farm:own")
    user_id = _caller(actor)
    found_date = _found_date(farm.found_date)
    # One point is the farm's location, three or more its outline; checked
    # before anything is written.
    wkt = None
    if farm.gis:
        wkt = _wkt_point(farm.gis[0]) if len(farm.gis) == 1 else _wkt_polygon(farm.gis)
    farm_id = uuid4()
    now = clock.utc_now()
    async with transaction(session):
        await db.agriculture.insert_farm(
            session,
            farm_id,
            farm_name=farm.farm_name,
            hub_id=farm.hub_id,
            found_date=found_date,
            address_detail=farm.address_detail,
            province_id=farm.province_id,
            district_id=farm.district_id,
            subdistrict_id=farm.subdistrict_id,
            zip_code=farm.zip_code,
            contact_name=farm.contact_name,
            phone_number=farm.phone_number,
            line=farm.line,
            facebook=farm.facebook,
            created_at=now.replace(tzinfo=None),
            updated_at=now.replace(tzinfo=None),
        )
        await db.agriculture.link_farmer(session, user_id, farm_id)
        if wkt is not None:
            await db.storage.insert_geo(
                session,
                uploaded_by=user_id,
                wkt=wkt,
                code=str(farm_id),
                source_type="farm",
                created_at=now,
            )
    return FarmCreatedOut(farm_id=farm_id)


async def create_plot(session: Session, actor: Actor, plot: PlotIn) -> PlotCreatedOut:
    """Register a plot on one of the caller's farms.

    Go accepted any farm_id, so a farmer could add plots to someone else's
    farm; the farm must be the caller's now.
    """
    require(actor, "create:farm:own")
    user_id = _caller(actor)
    found_date = _found_date(plot.found_date)
    # A single point is not an outline, so Go stored nothing for it; two
    # points can't make one either.
    wkt = _wkt_polygon(plot.gis) if len(plot.gis) >= 2 else None
    if not await db.agriculture.farm_exists(session, plot.farm_id):
        raise NotFound("Farm not found")
    if not await db.agriculture.farmer_owns_farm(session, user_id, plot.farm_id):
        raise Forbidden("Not your farm")
    plot_id = uuid4()
    now = clock.utc_now()
    async with transaction(session):
        await db.agriculture.insert_plot(
            session,
            plot_id,
            farm_id=plot.farm_id,
            plot_name=plot.plot_name,
            total_area=plot.gis_area_m2 / SQ_M_PER_RAI,
            land_ownership=plot.land_ownership,
            cocoa_planted_area=plot.cocoa_planted_area,
            has_chemical_use=plot.has_chemical_use,
            land_type_id=plot.land_type_id,
            soil_type_id=plot.soil_type_id,
            water_source_id=plot.water_source_id,
            watering_system_id=plot.watering_system_id,
            found_date=found_date,
            created_at=now.replace(tzinfo=None),
            updated_at=now.replace(tzinfo=None),
        )
        if wkt is not None:
            await db.storage.insert_geo(
                session,
                uploaded_by=user_id,
                wkt=wkt,
                code=str(plot_id),
                source_type="plot",
                area_sq_m=plot.gis_area_m2,
                created_at=now,
            )
    return PlotCreatedOut(plot_id=plot_id)


def _scope(actor: Actor) -> UUID | None:
    """Whose farms a list shows: everyone's (None) or the caller's own."""
    if actor.can("read:farm:all"):
        return None
    require(actor, "read:farm:own")
    return _caller(actor)


async def _image_urls(session: Session, farm_ids: list[UUID]) -> dict[UUID, str]:
    """Links to each farm's newest photo. A missing or broken link leaves
    that farm without one; it never fails the list."""
    if not r2.configured() or not farm_ids:
        return {}
    paths = await db.storage.latest_file_paths(
        session, table_name=FARM_IMAGE_TABLE, code=FARM_IMAGE_CODE, ref_ids=farm_ids
    )
    urls: dict[UUID, str] = {}
    for farm_id, path in paths.items():
        try:
            urls[farm_id] = await r2.presigned_get(path, IMAGE_URL_SECONDS)
        except Exception:
            logger.exception("could not sign a farm photo link", extra={"key": path})
    return urls


async def list_farms(
    session: Session, actor: Actor, *, page: int = 0, page_size: int = DEFAULT_PAGE_SIZE
) -> list[FarmOut]:
    """Farms (the caller's, or all with read:farm:all), each with its plots
    and a link to its newest photo."""
    farmer_id = _scope(actor)
    limit, offset = _page(page, page_size)
    farms = await db.agriculture.farms(session, farmer_id=farmer_id, limit=limit, offset=offset)
    farm_ids = [farm.farm_id for farm in farms]
    plots_by_farm: dict[UUID, list[PlotOut]] = {farm_id: [] for farm_id in farm_ids}
    for plot in await db.agriculture.plots_of_farms(session, farm_ids):
        plots_by_farm[plot.farm_id].append(PlotOut.model_validate(plot, from_attributes=True))
    urls = await _image_urls(session, farm_ids)
    return [
        FarmOut.model_validate(
            {
                **asdict(farm),
                "plots": plots_by_farm[farm.farm_id],
                "image_url": urls.get(farm.farm_id),
            }
        )
        for farm in farms
    ]


async def list_plots(
    session: Session, actor: Actor, *, page: int = 0, page_size: int = DEFAULT_PAGE_SIZE
) -> list[PlotOut]:
    """Plots on the caller's farms, or all with read:farm:all."""
    farmer_id = _scope(actor)
    limit, offset = _page(page, page_size)
    plots = await db.agriculture.plots(session, farmer_id=farmer_id, limit=limit, offset=offset)
    return [PlotOut.model_validate(plot, from_attributes=True) for plot in plots]


def _image_type(data: bytes) -> tuple[str, str] | None:
    """(mime type, extension) from the file's first bytes, for the formats
    accepted. The name and the client's claimed type are not trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


async def upload_farm_image(
    session: Session, actor: Actor, farm_id: UUID, *, filename: str, data: bytes
) -> FarmImageOut:
    """Store a new photo of one of the caller's farms and return a link to it.

    JPG, PNG or WEBP up to 5 MB, judged by the bytes. The object is private
    in R2; storage.file remembers it, and farm lists sign a fresh link.
    """
    require(actor, "update:farm:own")
    user_id = _caller(actor)
    if not r2.configured():
        raise Unavailable("Photo storage is not configured")
    if not await db.agriculture.farmer_owns_farm(session, user_id, farm_id):
        raise Forbidden("Not your farm")
    if len(data) > MAX_IMAGE_BYTES:
        raise Invalid(fields={"image": "รูปต้องมีขนาดไม่เกิน 5 MB"})
    kind = _image_type(data)
    if kind is None:
        raise Invalid(fields={"image": "รองรับเฉพาะไฟล์ JPG, PNG หรือ WEBP"})
    mime_type, extension = kind

    object_name = f"{uuid4()}.{extension}"
    key = f"farms/{farm_id}/{object_name}"
    # Uploaded before the record is written. If the insert then fails, the
    # object stays in R2 with nothing pointing at it: accepted, since it is
    # private and small, and the other order could leave a record pointing
    # at a photo that never arrived.
    await r2.put(key, data, mime_type)
    async with transaction(session):
        await db.storage.insert_file(
            session,
            file_name=object_name,
            table_name=FARM_IMAGE_TABLE,
            ref_id=farm_id,
            code=FARM_IMAGE_CODE,
            original_name=os.path.basename(filename)[:255],
            file_extension=extension,
            mime_type=mime_type,
            file_size=len(data),
            storage_path=key,
            checksum=hashlib.sha256(data).hexdigest(),
            uploaded_by=user_id,
            created_at=clock.utc_now_naive(),
        )
    try:
        image_url: str | None = await r2.presigned_get(key, IMAGE_URL_SECONDS)
    except Exception:
        logger.exception("could not sign the new farm photo link", extra={"key": key})
        image_url = None
    return FarmImageOut(farm_id=farm_id, image_url=image_url)
