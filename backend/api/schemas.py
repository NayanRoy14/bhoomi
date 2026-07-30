"""Request and response models for the Bhoomi API (PLAN.md 7).

Geometry is GeoJSON in EPSG:4326 throughout, in and out.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

#: PLAN.md 8. Enforced server-side; the UI also enforces the AOI cap client-side.
MAX_AOI_KM2 = 500.0
MAX_DATE_RANGE_DAYS = 366
MAX_SCENES = 50


class Geometry(BaseModel):
    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]

    @field_validator("coordinates")
    @classmethod
    def ring_must_be_closed(cls, value):
        if not value or len(value[0]) < 4:
            raise ValueError("Polygon ring needs at least 4 positions")
        if value[0][0] != value[0][-1]:
            raise ValueError("Polygon ring must be closed (first position == last)")
        return value

    def as_dict(self) -> dict:
        return {"type": self.type, "coordinates": self.coordinates}


class SceneSearchRequest(BaseModel):
    aoi: Geometry
    start_date: date | None = None
    end_date: date | None = None
    max_cloud: float | None = Field(default=None, ge=0, le=100)
    collection: str = "sentinel-2-l2a"
    limit: int = Field(default=MAX_SCENES, ge=1, le=MAX_SCENES)
    #: Collapse repeat versions of the same acquisition, keeping the newest
    #: processing baseline. The archive serves e.g. 2020-03-30 over tile 45QXF
    #: as both _0_ (Sen2Cor 02.14) and _1_ (05.00). Showing both invites the
    #: user to pick between them on cloud cover, which is how a Sen2Cor version
    #: change ends up inside a change-detection result (PLAN.md 5.3).
    deduplicate: bool = True

    model_config = {
        "json_schema_extra": {
            "example": {
                "aoi": {"type": "Polygon", "coordinates": [[
                    [88.35, 22.55], [88.52, 22.55], [88.52, 22.68],
                    [88.35, 22.68], [88.35, 22.55]]]},
                "start_date": "2020-02-15",
                "end_date": "2020-03-31",
                "max_cloud": 10,
            }
        }
    }


class SceneOut(BaseModel):
    id: str
    collection: str
    satellite: str | None
    acquired_at: datetime
    cloud_cover: float | None
    processing_baseline: str | None
    bbox: list[float]
    geometry: dict[str, Any]
    thumbnail: str | None
    #: Fraction of the AOI inside this scene. Below 1.0 means the AOI crosses a
    #: scene boundary and cannot be processed as one job (PLAN.md D3).
    aoi_coverage: float
    available_processes: list[str]


class SceneSearchResponse(BaseModel):
    count: int
    aoi_area_km2: float
    scenes: list[SceneOut]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    catalogue: str
    #: Null until the queue exists (January 2027).
    queue_depth: int | None = None
    workers: int | None = None
