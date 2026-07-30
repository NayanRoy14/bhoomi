"""Windowed raster reading and grid alignment.

Every raster read in Bhoomi goes through this module. The source may be an HTTP(S)
COG URL or a local staged file -- the distinction is deliberately invisible to
callers, because Bhoonidhi (PLAN.md 2.2) requires a download-and-stage path while
Sentinel-2 supports direct range reads. Writing that abstraction now makes the
second data source a configuration change instead of a rewrite.

Verified 2026-07-30: 32 KB of a 206 MB COG fetched in 1.34 s over HTTP from India,
anonymous, no credentials -- 0.015% of the file.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

logger = logging.getLogger(__name__)

WGS84 = "EPSG:4326"

#: Hard ceiling on output size (PLAN.md 8). Area alone is the wrong guard because
#: it ignores resolution -- the same AOI is 4x the pixels at 10 m versus 20 m.
MAX_PIXELS = 50_000_000

#: GDAL settings for reading COGs over HTTP. Without these GDAL issues far more
#: requests than necessary, turning a windowed read into a slow full scan.
GDAL_HTTP_OPTIONS = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "67108864",
}


class GridTooLargeError(ValueError):
    """Raised when a requested grid exceeds MAX_PIXELS."""


def configure_gdal() -> None:
    """Apply GDAL environment settings for efficient remote COG reads."""
    for key, value in GDAL_HTTP_OPTIONS.items():
        os.environ.setdefault(key, value)


def utm_crs_for(lon: float, lat: float) -> CRS:
    """Return the UTM CRS containing a point. Kolkata resolves to EPSG:32645."""
    zone = int((lon + 180.0) / 6.0) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


@dataclass(frozen=True)
class Grid:
    """A concrete output raster definition: CRS, affine transform and shape.

    Change detection requires both dates to land on an identical grid. Deriving
    one Grid from the AOI and reprojecting both scenes onto it -- rather than
    reprojecting one scene onto the other -- keeps the result independent of
    which scene happened to be chosen first.
    """

    crs: CRS
    transform: Affine
    width: int
    height: int

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def resolution(self) -> float:
        return abs(self.transform.a)

    @property
    def pixel_count(self) -> int:
        return self.width * self.height

    def bounds(self) -> tuple[float, float, float, float]:
        left, top = self.transform * (0, 0)
        right, bottom = self.transform * (self.width, self.height)
        return left, bottom, right, top


def grid_for_aoi(
    bounds_wgs84: tuple[float, float, float, float],
    resolution: float,
    crs: CRS | None = None,
) -> Grid:
    """Build an output Grid covering an AOI, snapped to the resolution.

    Snapping the origin to a multiple of the resolution means two AOIs computed
    at different times land on the same pixel centres, which is what makes
    outputs comparable and tile-cacheable.
    """
    west, south, east, north = bounds_wgs84
    if crs is None:
        crs = utm_crs_for((west + east) / 2.0, (south + north) / 2.0)

    left, bottom, right, top = transform_bounds(WGS84, crs, west, south, east, north)

    left = math.floor(left / resolution) * resolution
    bottom = math.floor(bottom / resolution) * resolution
    right = math.ceil(right / resolution) * resolution
    top = math.ceil(top / resolution) * resolution

    width = int(round((right - left) / resolution))
    height = int(round((top - bottom) / resolution))

    if width * height > MAX_PIXELS:
        raise GridTooLargeError(
            f"Requested grid is {width}x{height} = {width * height:,} pixels at "
            f"{resolution} m, over the {MAX_PIXELS:,} limit. Reduce the AOI."
        )

    return Grid(crs=crs, transform=Affine(resolution, 0, left, 0, -resolution, top),
                width=width, height=height)


def read_to_grid(
    source: str,
    grid: Grid,
    resampling: Resampling = Resampling.bilinear,
    band: int = 1,
) -> np.ndarray:
    """Read ``source`` reprojected onto ``grid``, fetching only what is needed.

    Works identically for a local path and an HTTP COG URL.
    """
    configure_gdal()
    with rasterio.open(source) as src:
        with WarpedVRT(
            src,
            crs=grid.crs,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            resampling=resampling,
        ) as vrt:
            return vrt.read(band)


def read_scl_to_grid(source: str, grid: Grid) -> np.ndarray:
    """Read an SCL band onto ``grid`` using nearest-neighbour.

    SCL holds categorical class codes. Interpolating them would invent classes
    that do not exist -- averaging 'cloud' (9) and 'vegetation' (4) into 6.5 is
    meaningless, and rounding it lands on 'water'.
    """
    return read_to_grid(source, grid, resampling=Resampling.nearest)


def describe(source: str) -> dict:
    """Cheap metadata probe. Reads only the COG header, not the pixels."""
    configure_gdal()
    with rasterio.open(source) as src:
        return {
            "crs": str(src.crs),
            "width": src.width,
            "height": src.height,
            "dtype": src.dtypes[0],
            "resolution": abs(src.transform.a),
            "nodata": src.nodata,
            "overviews": src.overviews(1),
        }
