"""Cloud-Optimized GeoTIFF output.

A COG is what makes a Bhoomi result a research product rather than a screenshot:
TiTiler serves tiles from it over HTTP range requests, QGIS opens it directly,
and the embedded provenance travels with the file when someone downloads it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio

from .raster_utils import Grid

logger = logging.getLogger(__name__)

NODATA = -9999.0

COG_PROFILE = {
    "driver": "COG",
    "dtype": "float32",
    "compress": "DEFLATE",
    "predictor": 3,          # floating-point predictor
    "blocksize": 512,
    "overview_resampling": "average",
}


def write_cog(
    path: str | Path,
    array: np.ndarray,
    grid: Grid,
    metadata: dict | None = None,
    nodata: float = NODATA,
) -> Path:
    """Write ``array`` as a COG on ``grid``, replacing NaN with ``nodata``.

    ``metadata`` is embedded as GeoTIFF tags so the output is self-describing --
    process, source scenes, index formula, baselines, valid fraction. Attribution
    belongs here too, so it survives download (PLAN.md 14).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = array.astype(np.float32, copy=True)
    data[~np.isfinite(data)] = nodata

    profile = {
        **COG_PROFILE,
        "width": grid.width,
        "height": grid.height,
        "count": 1,
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": nodata,
    }

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
        if metadata:
            dst.update_tags(**{k: str(v) for k, v in metadata.items()})

    logger.info("wrote %s (%.1f MB)", path, path.stat().st_size / 1e6)
    return path


def validate_cog(path: str | Path) -> tuple[bool, list[str]]:
    """Check that ``path`` is a valid COG. Returns ``(is_valid, messages)``.

    Worth running on every output before marking a job complete: an invalid COG
    still opens in QGIS but makes TiTiler read badly, and the failure mode is
    "tiles are slow" rather than an error -- which is very hard to diagnose later.
    """
    path = Path(path)
    try:
        from rio_cogeo.cogeo import cog_validate  # type: ignore

        valid, errors, warnings = cog_validate(str(path))
        return bool(valid), [*errors, *warnings]
    except ImportError:
        pass

    # Fallback if rio-cogeo is unavailable: check the structural essentials.
    messages: list[str] = []
    with rasterio.open(path) as src:
        if not src.profile.get("tiled", False):
            messages.append("not tiled")
        # Overviews are only required once the image exceeds a single block.
        # Below that the block IS the whole image, so there is nothing to
        # decimate and GDAL writes none -- correctly. Demanding them anyway
        # failed every small output: NDBI at 20 m over the demo AOI is
        # 260x225 against a 512 block, and rio-cogeo passes it.
        block = min(src.block_shapes[0]) if src.block_shapes else 512
        if max(src.width, src.height) > block and not src.overviews(1):
            messages.append("no overviews")
        if src.nodata is None:
            messages.append("nodata not set")
    if messages:
        messages.append("(install rio-cogeo for a full validation)")
    return not messages, messages


def default_metadata(
    process: str,
    scene_ids: list[str],
    formula: str,
    baselines: list[str] | None = None,
    valid_fraction: float | None = None,
    extra: dict | None = None,
) -> dict:
    """Standard provenance tags for a Bhoomi output."""
    from datetime import datetime, timezone

    meta = {
        "BHOOMI_PROCESS": process,
        "BHOOMI_SOURCE_SCENES": ",".join(scene_ids),
        "BHOOMI_FORMULA": formula,
        "BHOOMI_GENERATED": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "BHOOMI_ATTRIBUTION": "Contains modified Copernicus Sentinel data",
    }
    if baselines:
        meta["BHOOMI_PROCESSING_BASELINES"] = ",".join(baselines)
    if valid_fraction is not None:
        meta["BHOOMI_VALID_FRACTION"] = f"{valid_fraction:.4f}"
    if extra:
        meta.update(extra)
    return meta
