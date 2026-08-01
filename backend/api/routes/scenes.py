"""Scene search — POST /api/v1/scenes/search (PLAN.md 7.2)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from backend import tiles
from backend.api import errors, schemas
from backend.api.deps import get_catalogue, get_scene_store
from backend.db import SceneStore
from catalogue import Catalogue, Scene, SearchQuery
from catalogue.earthsearch import deduplicate_by_acquisition
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


def _aoi_bounds(geometry: dict) -> tuple[float, float, float, float] | None:
    coords = [c for ring in geometry.get("coordinates", []) for c in ring]
    if not coords:
        return None
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a: tuple[float, float, float, float],
             b: tuple[float, float, float, float]
             ) -> tuple[float, float, float, float] | None:
    """The shared rectangle of two bboxes, or None if they do not meet.

    The preview is cut from the *overlap* rather than from the AOI, because a
    partially covering scene is shown in the list too (`aoi_coverage` < 1).
    Cropping such a scene to the full AOI would ask TiTiler for ground the COG
    does not contain, and the preview would come back mostly nodata -- which
    reads as "this scene is broken" when the honest message is "this scene
    covers the left third of what you drew".
    """
    minx, miny = max(a[0], b[0]), max(a[1], b[1])
    maxx, maxy = min(a[2], b[2]), min(a[3], b[3])
    if minx >= maxx or miny >= maxy:
        return None
    return minx, miny, maxx, maxy


def _preview(scene: Scene, aoi_bbox: tuple[float, float, float, float] | None) -> str | None:
    """An AOI-sized crop where possible, the whole-tile JPEG otherwise.

    Falls back rather than failing: with no tile server configured, or a scene
    carrying no `visual` asset, the old thumbnail is still better than a blank
    row. See `tiles.preview_url` for why the crop is worth having.
    """
    if aoi_bbox is not None:
        window = _overlap(aoi_bbox, tuple(scene.bbox))
        if window is not None:
            cropped = tiles.preview_url(scene.assets.get("visual"), window)
            if cropped:
                return cropped
    return _thumbnail(scene)


@router.post("/search", response_model=schemas.SceneSearchResponse,
             summary="Find satellite scenes covering an area and date range")
def search_scenes(
    request: schemas.SceneSearchRequest,
    catalogue: Catalogue = Depends(get_catalogue),
    store: SceneStore = Depends(get_scene_store),
) -> schemas.SceneSearchResponse:
    """Search the catalogue for scenes intersecting the AOI.

    Returns every intersecting scene, including those that only partly cover the
    AOI -- `aoi_coverage` says which. The UI needs to *show* a partial match so
    the user understands why it cannot be processed, rather than having results
    silently disappear.
    """
    aoi = request.aoi.as_dict()
    aoi_bbox = _aoi_bounds(aoi)

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
    found = len(scenes)
    if request.deduplicate:
        scenes = deduplicate_by_acquisition(scenes)
    logger.info("scene search: %.1f km², %s..%s, cloud<%s -> %d scenes (%d before dedup)",
                area_km2, request.start_date, request.end_date,
                request.max_cloud, len(scenes), found)

    # Write-through, so January's POST /jobs can resolve a scene id to its band
    # hrefs without a second STAC round trip. The deduplicated list, not the raw
    # one: those are the scenes the UI will offer, so those are the ids that can
    # come back. This never affects the response -- see backend/db/scenes.py on
    # why a failure here is logged rather than raised.
    store.put_many(scenes, catalogue=getattr(catalogue, "name", "earth-search"))

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
                thumbnail=_preview(s, aoi_bbox),
                aoi_coverage=round(s.aoi_coverage(aoi), 4),
                available_processes=_available_processes(s),
            )
            for s in scenes
        ],
    )
