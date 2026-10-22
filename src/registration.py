"""Input rules shared by every "register a place" action: farm, plot, hub,
processing station.

All four take the same two things from the mobile app: an optional founding
date and a list of GPS points. One copy of how they are read keeps the four
from drifting apart.
"""

from datetime import date

from pydantic import BaseModel, Field

from src import clock
from src.exceptions import Invalid


class GisPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


def found_date(value: str) -> date:
    """YYYY-MM-DD; blank means today (the farmer's day, Bangkok). Go silently
    stored year 1 for a malformed date; this refuses it instead."""
    if not value.strip():
        return clock.bangkok_today()
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise Invalid(fields={"found_date": "Use YYYY-MM-DD"}) from None


def wkt_point(point: GisPoint) -> str:
    """WKT for one location, lon lat with six decimals as Go's %f wrote them."""
    return f"POINT({point.lng:f} {point.lat:f})"


def wkt_polygon(points: list[GisPoint]) -> str:
    """A closed outline: the first point is repeated at the end. Needs 3+
    points: PostGIS rejects a ring with fewer than 4 (3 distinct + closing),
    which used to roll the whole registration back as a 500."""
    if len(points) < 3:
        raise Invalid(fields={"gis": "An outline needs at least 3 points"})
    ring = [*points, points[0]]
    return "POLYGON((" + ",".join(f"{p.lng:f} {p.lat:f}" for p in ring) + "))"


def wkt_shape(points: list[GisPoint]) -> str:
    """One point is a location, three or more an outline (two is refused)."""
    return wkt_point(points[0]) if len(points) == 1 else wkt_polygon(points)
