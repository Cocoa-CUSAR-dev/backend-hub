"""Collection hubs and the harvests received there.

Ported from mobile-backend's collection_handler.go (RegisterHub, GetMyHub,
GetMyHarvests). The hub tables live in the processing schema, so their
queries are in db.processing; harvests are db.collection.

Permissions (database V34): create:hub:own (register my hub), read:hub:own
(my hub and its harvests), read:hub:all (every hub, researchers). Go gated
these routes on the hub_collector role instead.
"""

from dataclasses import asdict
from uuid import UUID, uuid4

from src import clock, db, pagination, registration
from src.actor import Actor, require
from src.collection.schemas import HarvestOut, HubCreatedOut, HubIn, HubOut
from src.database import Session, transaction
from src.exceptions import Forbidden


def _caller(actor: Actor) -> UUID:
    if actor.user_id is None:
        raise Forbidden("A user is required")
    return actor.user_id


async def create_hub(session: Session, actor: Actor, hub: HubIn) -> HubCreatedOut:
    """Register a hub and make it the caller's (one hub per collector: a
    second registration moves the collector to the new hub, as in Go)."""
    require(actor, "create:hub:own")
    user_id = _caller(actor)
    found_date = registration.found_date(hub.found_date)
    hub_id = uuid4()
    now = clock.utc_now()
    async with transaction(session):
        await db.processing.insert_hub(
            session,
            hub_id,
            hub_name=hub.hub_name,
            found_date=found_date,
            address_detail=hub.address_detail,
            province_id=hub.province_id,
            district_id=hub.district_id,
            subdistrict_id=hub.subdistrict_id,
            zip_code=hub.zip_code,
            contact_name=hub.contact_name,
            phone_number=hub.phone_number,
            line=hub.line,
            facebook=hub.facebook,
            created_at=now.replace(tzinfo=None),
            updated_at=now.replace(tzinfo=None),
        )
        # Go updated the collector row without checking it existed, leaving
        # an orphan hub for someone with no collector profile.
        if not await db.processing.set_collector_hub(session, user_id, hub_id):
            raise Forbidden("Add the hub collector role first")
        if hub.gis:
            await db.storage.insert_geo(
                session,
                uploaded_by=user_id,
                wkt=registration.wkt_point(hub.gis[0]),
                code=str(hub_id),
                source_type="hub",
                created_at=now,
            )
    return HubCreatedOut(hub_id=hub_id)


async def _hub_scope(session: Session, actor: Actor) -> list[UUID] | None:
    """Which hubs a list covers: every hub (None) or the caller's own."""
    if actor.can("read:hub:all"):
        return None
    require(actor, "read:hub:own")
    return await db.processing.collector_hub_ids(session, _caller(actor))


async def list_hubs(
    session: Session,
    actor: Actor,
    *,
    page: int = 0,
    page_size: int = pagination.DEFAULT_PAGE_SIZE,
) -> list[HubOut]:
    """Hubs (the caller's, or all with read:hub:all), each with every harvest
    received there and the name of the farm it came from."""
    hub_ids = await _hub_scope(session, actor)
    if hub_ids == []:
        return []
    limit, offset = pagination.window(page, page_size)
    hubs = await db.processing.hubs(session, hub_ids=hub_ids, limit=limit, offset=offset)
    listed = [hub.hub_id for hub in hubs]
    rows = await db.collection.harvests(session, hub_ids=listed) if listed else []
    names = await db.agriculture.farm_names(session, sorted({row.farm_id for row in rows}))
    by_hub: dict[UUID, list[HarvestOut]] = {hub_id: [] for hub_id in listed}
    for row in rows:
        by_hub[row.hub_id].append(
            HarvestOut.model_validate({**asdict(row), "farm_name": names.get(row.farm_id)})
        )
    return [HubOut.model_validate({**asdict(hub), "harvests": by_hub[hub.hub_id]}) for hub in hubs]


async def list_harvests(
    session: Session,
    actor: Actor,
    *,
    page: int = 0,
    page_size: int = pagination.DEFAULT_PAGE_SIZE,
) -> list[HarvestOut]:
    """Harvests received at the caller's hub (or every hub with
    read:hub:all), newest first."""
    hub_ids = await _hub_scope(session, actor)
    if hub_ids == []:
        return []
    limit, offset = pagination.window(page, page_size)
    rows = await db.collection.harvests(session, hub_ids=hub_ids, limit=limit, offset=offset)
    return [HarvestOut.model_validate(asdict(row)) for row in rows]
