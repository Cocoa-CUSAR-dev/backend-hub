"""Processing stations and their batches.

Ported from mobile-backend's processing_handler.go (RegisterStation,
GetMyProcessingStation, GetMyBatches).

Permissions (database V34): create:station:own (register a station I run),
read:station:own (my stations and their batches), read:station:all (every
station, researchers). Go gated these routes on the processor role instead.
"""

from dataclasses import asdict
from uuid import UUID, uuid4

from src import clock, db, pagination
from src.actor import Actor, require
from src.database import Session, transaction
from src.exceptions import Forbidden
from src.processing.schemas import BatchOut, StationCreatedOut, StationIn, StationOut


def _caller(actor: Actor) -> UUID:
    if actor.user_id is None:
        raise Forbidden("A user is required")
    return actor.user_id


async def create_station(session: Session, actor: Actor, station: StationIn) -> StationCreatedOut:
    """Register a processing station run by the caller."""
    require(actor, "create:station:own")
    user_id = _caller(actor)
    station_id = uuid4()
    now = clock.utc_now_naive()
    async with transaction(session):
        await db.processing.insert_station(
            session,
            station_id,
            processing_station_name=station.processing_station_name,
            hub_id=station.hub_id,
            address_detail=station.address_detail,
            province_id=station.province_id,
            district_id=station.district_id,
            subdistrict_id=station.subdistrict_id,
            created_at=now,
            updated_at=now,
        )
        await db.processing.link_processor_station(session, user_id, station_id)
    return StationCreatedOut(station_id=station_id)


def _scope(actor: Actor) -> UUID | None:
    """Whose stations a list covers: everyone's (None) or the caller's."""
    if actor.can("read:station:all"):
        return None
    require(actor, "read:station:own")
    return _caller(actor)


async def list_stations(
    session: Session,
    actor: Actor,
    *,
    page: int = 0,
    page_size: int = pagination.DEFAULT_PAGE_SIZE,
) -> list[StationOut]:
    """Stations (the caller's, or all with read:station:all), each with its
    batches."""
    processor_id = _scope(actor)
    limit, offset = pagination.window(page, page_size)
    stations = await db.processing.stations(
        session, processor_id=processor_id, limit=limit, offset=offset
    )
    ids = [station.processing_station_id for station in stations]
    by_station: dict[UUID, list[BatchOut]] = {station_id: [] for station_id in ids}
    for batch in await db.processing.batches_of_stations(session, ids):
        by_station[batch.processing_station_id].append(BatchOut.model_validate(asdict(batch)))
    return [
        StationOut.model_validate(
            {**asdict(station), "batches": by_station[station.processing_station_id]}
        )
        for station in stations
    ]


async def list_batches(
    session: Session,
    actor: Actor,
    *,
    page: int = 0,
    page_size: int = pagination.DEFAULT_PAGE_SIZE,
) -> list[BatchOut]:
    """Batches at the caller's stations (or all with read:station:all).

    Go's GET /batches also returned the joined station columns mixed into
    each row; only the batch's own fields are returned here.
    """
    processor_id = _scope(actor)
    limit, offset = pagination.window(page, page_size)
    rows = await db.processing.batches(
        session, processor_id=processor_id, limit=limit, offset=offset
    )
    return [BatchOut.model_validate(asdict(row)) for row in rows]
