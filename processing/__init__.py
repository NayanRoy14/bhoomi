"""Bhoomi raster processing library.

Pure geospatial computation with no web dependencies -- this package must never
import from ``backend/``. That keeps it unit-testable without a database,
usable from a notebook, and directly demonstrable to someone who wants to see
the science without running the stack (PLAN.md 10).

Pipeline order matters, and each step exists because of a measured failure:

    read bands onto a common Grid      raster_utils
        -> mask cloud and shadow       masking      (unmasked NDVI is meaningless)
        -> DN to reflectance           harmonize    (offset does not cancel in a ratio)
        -> compute index               indices      (guarded denominator)
        -> difference two dates        change       (baselines must match)
        -> write Cloud-Optimized TIFF  cog
"""

from .change import ChangeStats, change_stats, check_scene_compatibility, difference
from .cog import NODATA, default_metadata, validate_cog, write_cog
from .harmonize import (
    HarmonizationError,
    baselines_match,
    parse_baseline,
    reflectance_params,
    to_reflectance,
)
from .indices import (
    INDEX_BANDS,
    INDEX_FUNCTIONS,
    ImplausibleIndexError,
    ndbi,
    ndvi,
    ndwi,
    normalized_difference,
)
from .masking import (
    DEFAULT_MASK_CLASSES,
    SCL_CLASSES,
    apply_mask,
    class_histogram,
    scl_mask,
    valid_fraction,
)
from .raster_utils import (
    Grid,
    GridTooLargeError,
    configure_gdal,
    describe,
    grid_for_aoi,
    read_scl_to_grid,
    read_to_grid,
    utm_crs_for,
)

__version__ = "0.1.0"

__all__ = [
    "ChangeStats", "change_stats", "check_scene_compatibility", "difference",
    "NODATA", "default_metadata", "validate_cog", "write_cog",
    "HarmonizationError", "baselines_match", "parse_baseline",
    "reflectance_params", "to_reflectance",
    "INDEX_BANDS", "INDEX_FUNCTIONS", "ImplausibleIndexError",
    "ndbi", "ndvi", "ndwi", "normalized_difference",
    "DEFAULT_MASK_CLASSES", "SCL_CLASSES", "apply_mask", "class_histogram",
    "scl_mask", "valid_fraction",
    "Grid", "GridTooLargeError", "configure_gdal", "describe", "grid_for_aoi",
    "read_scl_to_grid", "read_to_grid", "utm_crs_for",
]
