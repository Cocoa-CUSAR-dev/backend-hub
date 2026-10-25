"""The dashboard's JSON, key for key as web-backend's Analytics.Report wrote it
(the web app's charts read these names)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Out(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class SeriesOut(_Out):
    label: str
    values: list[float]
    unit: str | None


class TimeSeriesOut(_Out):
    from_: str = Field(serialization_alias="from", description="First month, yyyy-MM")
    to: str = Field(description="Last month, yyyy-MM")
    title: str
    metadata: dict[str, Any]
    series: list[SeriesOut]


class DataItemOut(_Out):
    label: str
    value: float
    unit: str | None


class SummaryOut(_Out):
    from_: str = Field(serialization_alias="from", description="First month, yyyy-MM")
    to: str = Field(description="Last month, yyyy-MM")
    title: str
    metadata: dict[str, Any]
    data: DataItemOut


class AreaIn(BaseModel):
    """The map dashboard's drawn area, as a GeoJSON geometry in a string;
    none means every farm that has a location."""

    model_config = ConfigDict(populate_by_name=True)

    geo_json: str | None = Field(default=None, alias="geoJson")
