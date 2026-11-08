"""Farm/station-scoped picker for the 5 previously-blocked child handlers --
farm_activity_fertilizer, farm_activity_chemical, harvest_grade_detail,
fermentation_batch, drying_batch. Each needs a parent row ID (a farm
activity, harvest, or batch) that isn't part of the form's own questions;
the farmer picks it from a list scoped to their own farm/station, via the
membership links confirmed with the original schema designer (เป็นเอก
สิทธิมงคล): farmer to farm, hub collector to hub, processor to processing
station. See docs/plans/chatbot-child-handler-design.md for the full design.

harvest_grade_detail is the one case that answer didn't fully resolve --
a harvest carries BOTH a farm and a hub, and grading may be done by the
farmer or by hub staff. Rather than blocking on that decision, the harvest
picker shows the union of both memberships: offered if the requester is on
its farm OR works at its hub. Showing one extra choice is a much smaller
problem than hiding the right one -- worth narrowing later if it turns out
one side never applies in practice.

The queries are in the owner files under src/db/ (agriculture, collection,
processing, ref); this module composes them into labelled options. That the
chatbot lists these rows itself, rather than asking Go, is deliberate and
permanent: Go owns the writes but never exposed a scoped read API for them.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from src import db
from src.database import Session
from src.line import handler_registry

logger = logging.getLogger(__name__)

_QUICK_REPLY_LIMIT = 13  # LINE's own cap on Quick Reply items per message

# Which handlers need a parent picked first, and which column the pick is
# stored under, used to be hard-coded here (_PARENT_KIND_BY_HANDLER and
# FIELD_NAME). Since US2-13 they come from form.handler_registry -- see
# src/line/handler_registry.py. What stays as code is everything below: how
# to word "which harvest?" and how to find the farmer's own candidate rows,
# per parent kind. That is product logic, not data.

PROMPT = {
    "farm_activity": "เลือกกิจกรรมในฟาร์มที่เกี่ยวข้อง:",
    "harvest": "เลือกการเก็บเกี่ยวที่เกี่ยวข้อง:",
    "batch": "เลือกแบทช์การแปรรูปที่เกี่ยวข้อง:",
}

EMPTY_PROMPT = {
    "farm_activity": "คุณยังไม่มีกิจกรรมในฟาร์มที่บันทึกไว้เลย กรุณาบันทึกกิจกรรมในฟาร์มก่อน",
    "harvest": "คุณยังไม่มีการเก็บเกี่ยวที่บันทึกไว้เลย กรุณาบันทึกการเก็บเกี่ยวก่อน",
    "batch": "คุณยังไม่มีแบทช์การแปรรูปที่บันทึกไว้เลย กรุณาบันทึกแบทช์ก่อน",
}


@dataclass(frozen=True)
class ParentOption:
    id: str
    label: str


# Shown when the registry names a parent kind this module has no picker
# query for (a new child handler under a brand-new parent kind, added by
# migration before its picker was written). Refusing honestly beats starting
# a conversation that could never be submitted -- the same honest-failure
# pattern as CB-1.
UNSUPPORTED_PROMPT = "ขออภัยครับ แบบฟอร์มนี้ยังไม่รองรับการกรอกในแชท กรุณากรอกผ่านแอปนะครับ"


async def kind_for_handler(session: Session, handler: str) -> str | None:
    """The parent kind to pick before this handler's questions, from the
    registry -- or None for a standalone handler."""
    return await handler_registry.parent_kind_for(session, handler)


def has_picker(kind: str) -> bool:
    """Whether this module knows how to list candidate parent rows of `kind`.
    False means the caller must refuse with UNSUPPORTED_PROMPT (and the gap
    is logged): see handler_registry's docstring."""
    supported = kind in _CHOICE_QUERY
    if not supported:
        logger.error("no parent picker query for parent kind %r -- refusing to start", kind)
    return supported


async def field_name_for(session: Session, kind: str) -> str:
    """The real destination column a picked parent rides under in the answer
    payload (see docs' "field_name trick"), from the registry."""
    field_name = await handler_registry.parent_field_for(session, kind)
    if field_name is None:
        # kind came from the same registry moments ago, so this only happens
        # if the row was deactivated in between -- fail loudly, not silently
        # submitting without a parent.
        raise LookupError(f"no handler_registry parent_field for parent kind {kind!r}")
    return field_name


def _label(prefix: str, when: date | datetime | None, place: str) -> str:
    day = f"{when:%d/%m/%Y}" if when is not None else ""
    return f"{prefix} {day} — {place}"


async def _farm_activity_choices(session: Session, user_id: UUID) -> list[ParentOption]:
    activities = await db.agriculture.farm_activities_of_farmer(
        session, user_id, _QUICK_REPLY_LIMIT
    )
    type_names = {
        c.id: c.name
        for c in await db.ref.choices(
            session,
            "farm_activity_type_constant",
            "farm_activity_type_id",
            "farm_activity_type_name",
        )
    }
    return [
        ParentOption(
            id=str(a.farm_activity_id),
            label=_label(
                type_names.get(str(a.farm_activity_type_id)) or "กิจกรรม",
                a.created_at,
                a.farm_name or "",
            ),
        )
        for a in activities
    ]


async def _harvest_choices(session: Session, user_id: UUID) -> list[ParentOption]:
    harvests = await db.collection.harvests_of(
        session,
        farm_ids=await db.agriculture.farm_ids_of_farmer(session, user_id),
        hub_ids=await db.processing.hub_ids_of_collector(session, user_id),
        limit=_QUICK_REPLY_LIMIT,
    )
    farm_names = await db.agriculture.farm_names(
        session, [h.farm_id for h in harvests if h.farm_id is not None]
    )
    hub_names = await db.processing.hub_names(
        session, [h.hub_id for h in harvests if h.hub_id is not None]
    )

    def place(farm_id: UUID | None, hub_id: UUID | None) -> str:
        # The farm's name when it has one, else the hub's, else nothing.
        farm_name = farm_names.get(farm_id) if farm_id is not None else None
        hub_name = hub_names.get(hub_id) if hub_id is not None else None
        return farm_name if farm_name is not None else (hub_name or "")

    return [
        ParentOption(
            id=str(h.harvest_id),
            label=_label("เก็บเกี่ยว", h.harvest_date, place(h.farm_id, h.hub_id)),
        )
        for h in harvests
    ]


async def _batch_choices(session: Session, user_id: UUID) -> list[ParentOption]:
    batches = await db.processing.batches_of_processor(session, user_id, _QUICK_REPLY_LIMIT)
    return [
        ParentOption(id=str(b.batch_id), label=_label("แบทช์", b.created_at, b.station_name or ""))
        for b in batches
    ]


_CHOICE_QUERY = {
    "farm_activity": _farm_activity_choices,
    "harvest": _harvest_choices,
    "batch": _batch_choices,
}


async def choices_for(session: Session, kind: str, user_id: UUID) -> list[ParentOption]:
    return await _CHOICE_QUERY[kind](session, user_id)
