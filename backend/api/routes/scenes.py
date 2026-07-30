"""Scene search — POST /api/v1/scenes/search (PLAN.md 7.2)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from backend.api import errors, schemas
from backend.api.deps import get_catalogue
from catalogue import Catalogue, Scene, SearchQuery
from processing import indices, raster_utils

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/scenes", tags=["scenes"])


def _available_processes(scene: Scene) -> list[str]:
    """Which indices this scene actually carries the bands for."""
    return sorted(name for name, (bands, _) in indices.INDEX_BANDS.items()
                  if scene.has_bands(bands))


def _thumbnail(scene: Scene) -> str | None:
    for key in ("thumbnail", "rendered_preview", "overview", "visual"):
        if key in scene.assets:
            return scene.assets[key]
    return None


@router.post("/search", response_model=schemas.SceneSearchResponse,
             summary="Find satellite scenes covering an area and date range")
def search_scenes(
    request: schemas.SceneSearchRequest,
    catalogue: Catalogue = Depends(get_catalogue),
) -> schemas.SceneSearchResponse:
    """Search the catalogue for scenes intersecting the AOI.

    Returns every intersecting scene, including those that only partly cover the
    AOI -- `aoi_coverage` says which. The UI needs to *show* a partial match so
    the user understands why it cannot be processed, rather than having results
    silently disappear.
    """
    aoi = request.aoi.as_dict()

    area_km2 = raster_utils.geometry_area_km2(aoi)
    if area_km2 > schemas.MAX_AOI_KM2:
        raise errors.aoi_too_large(area_km2, schemas.MAX_AOI_KM2)

    if request.start_date and request.end_date:
        if request.start_date > request.end_date:
            raise errors.invalid_date_range(str(request.start_date), str(request.end_date))
        span = (request.end_date - request.start_date).days
        if span > schemas.MAX_DATE_RANGE_DAYS:
            raise errors.date_range_too_long(span, schemas.MAX_DATE_RANGE_DAYS)

    query = SearchQuery(
        aoi=aoi,
        start=str(request.start_date) if request.start_date else None,
        end=str(request.end_date) if request.end_date else None,
        max_cloud=request.max_cloud,
        collections=(request.collection,),
        limit=request.limit,
    )
    scenes = catalogue.search(query)
    logger.info("scene search: %.1f km², %s..%s, cloud<%s -> %d scenes",
                area_km2, request.start_date, request.end_date,
                request.max_cloud, len(scenes))

    return schemas.SceneSearchResponse(
        count=len(scenes),
        aoi_area_km2=round(area_km2, 1),
        scenes=[
            schemas.SceneOut(
                id=s.id,
                collection=s.collection,
                satellite=s.satellite,
                acquired_at=s.acquired_at,
                cloud_cover=s.cloud_cover,
                processing_baseline=s.processing_baseline,
                bbox=list(s.bbox),
                geometry=s.geometry,
                thumbnail=_thumbnail(s),
                aoi_coverage=round(s.aoi_coverage(aoi), 4),
                available_processes=_available_processes(s),
            )
            for s in scenes
        ],
    )
