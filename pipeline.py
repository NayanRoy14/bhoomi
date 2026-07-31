"""Composition layer: catalogue metadata + raster mathematics.

``catalogue/`` knows what scenes exist. ``processing/`` knows the mathematics.
Neither imports the other. This module is the only place that knows both, and
it is what the FastAPI worker will call in January -- the web layer should add
HTTP and nothing else.

    scene = catalogue.get("S2A_45QXF_20200310_1_L2A")
    result = compute_index(scene, aoi, "ndvi")
    result.write(r"D:\\Bhoomi\\outputs\\ndvi.tif")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import cache
from catalogue import Scene
from processing import (
    Grid,
    apply_mask,
    change,
    cog,
    grid_for_aoi,
    harmonize,
    indices,
    masking,
    raster_utils,
)

logger = logging.getLogger(__name__)

#: Offset detection reads a decimated overview per scene (~6.0 s). Cached by
#: scene id because it is a property of the scene, not of the request. The
#: default persists to disk so a worker restart does not re-pay it; swap in a
#: PostgresOffsetCache when the scenes table lands (PLAN.md 6).
_offset_cache: cache.OffsetCache = cache.default_cache()


def set_offset_cache(new_cache: cache.OffsetCache) -> None:
    """Replace the cache backend. Called by tests and by the worker bootstrap."""
    global _offset_cache
    _offset_cache = new_cache


class PipelineError(RuntimeError):
    """A request cannot be satisfied as specified."""


@dataclass
class IndexResult:
    array: np.ndarray
    grid: Grid
    index: str
    scene_id: str
    valid_fraction: float
    offset_present: bool
    baseline: str | None
    #: What settled the reflectance convention: "pixels", "baseline" or
    #: "metadata". Recorded in the GeoTIFF because "metadata" means the pixels
    #: could not decide, and a reader deserves to know which of the two it got.
    offset_basis: str = "pixels"
    warnings: list[str] = field(default_factory=list)

    def stats(self) -> dict:
        finite = self.array[np.isfinite(self.array)]
        if finite.size == 0:
            return {}
        return {
            "min": float(finite.min()), "max": float(finite.max()),
            "mean": float(finite.mean()), "stddev": float(finite.std()),
            "median": float(np.median(finite)),
        }

    def write(self, path: str | Path) -> Path:
        formula = {"ndvi": "(nir - red) / (nir + red)",
                   "ndwi": "(green - nir) / (green + nir)",
                   "ndbi": "(swir16 - nir) / (swir16 + nir)"}.get(self.index, self.index)
        return cog.write_cog(
            path, self.array, self.grid,
            metadata=cog.default_metadata(
                self.index, [self.scene_id], formula,
                baselines=[self.baseline] if self.baseline else None,
                valid_fraction=self.valid_fraction,
                extra={"BHOOMI_BOA_OFFSET_PRESENT": self.offset_present,
                       "BHOOMI_BOA_OFFSET_BASIS": self.offset_basis}))


@dataclass
class ChangeResult:
    array: np.ndarray
    grid: Grid
    index: str
    scene_ids: tuple[str, str]
    baselines: tuple[str | None, str | None]
    stats: change.ChangeStats
    warnings: list[str] = field(default_factory=list)
    #: The two per-date index rasters the difference was taken between, in
    #: chronological order. Carried because a difference raster cannot be
    #: un-differenced: showing a user *what changed* needs both sides, and
    #: recomputing them would mean reading every band a second time. They are
    #: already in memory when the difference is formed, so this costs the
    #: lifetime of two arrays and no extra work.
    earlier: IndexResult | None = None
    later: IndexResult | None = None

    def write(self, path: str | Path) -> Path:
        return cog.write_cog(
            path, self.array, self.grid,
            metadata=cog.default_metadata(
                "change", list(self.scene_ids),
                f"{self.index}(later) - {self.index}(earlier)",
                baselines=[b for b in self.baselines if b],
                valid_fraction=self.stats.valid_fraction,
                extra={"BHOOMI_LOSS_FRACTION": f"{self.stats.loss_fraction:.4f}",
                       "BHOOMI_GAIN_FRACTION": f"{self.stats.gain_fraction:.4f}",
                       "BHOOMI_ASYMMETRY": f"{self.stats.asymmetry:.2f}",
                       "BHOOMI_WARNINGS": " | ".join(self.warnings) or "none"}))


def offset_decision(scene: Scene, band: str = "nir") -> harmonize.OffsetDecision:
    """How this scene's reflectance should be interpreted, and on what evidence.

    The pixel floor is measured from a decimated overview of the full tile and
    takes precedence over metadata, every field of which has been observed wrong
    (PLAN.md 5.3). It is one-sided: it can prove the offset ABSENT and never
    that it is present, so a bright tile with no dark target falls back to
    metadata and the decision carries a warning.

    Only the measurement is cached. The verdict is re-derived on every call, so
    a change to the threshold takes effect without invalidating anything.
    """
    floor = _offset_cache.get(scene.id)
    if floor is None:
        evidence = harmonize.measure_offset_floor_in_scene(scene.href(band))
        _offset_cache.set(scene.id, evidence.floor_dn)
    else:
        evidence = harmonize.OffsetEvidence(floor_dn=floor, sample_pixels=0)

    decision = harmonize.resolve_offset(evidence, scene.properties)
    logger.info("scene %s: offset_present=%s (basis: %s, floor %.0f DN)",
                scene.id, decision.present, decision.basis, evidence.floor_dn)
    return decision


def offset_present(scene: Scene, band: str = "nir") -> bool:
    """Whether the BOA offset is in this scene's pixels.

    Thin wrapper over :func:`offset_decision` for callers that do not need the
    basis or the warning. Prefer :func:`offset_decision` inside the pipeline --
    dropping the warning is exactly the silent-uncertainty failure this project
    is trying not to have.
    """
    return offset_decision(scene, band).present


def compute_index(
    scene: Scene,
    aoi: dict,
    index: str = "ndvi",
    resolution: float | None = None,
    mask_snow: bool = True,
    check_coverage: bool = True,
) -> IndexResult:
    """Compute one index for one scene over one AOI."""
    if index not in indices.INDEX_BANDS:
        raise PipelineError(
            f"Unknown process {index!r}. Available: {sorted(indices.INDEX_BANDS)}")

    bands, native_resolution = indices.INDEX_BANDS[index]
    resolution = resolution or native_resolution

    if not scene.has_bands(bands):
        raise PipelineError(
            f"Scene {scene.id} lacks bands {[b for b in bands if b not in scene.assets]} "
            f"required for {index}.")

    if check_coverage:
        coverage = scene.aoi_coverage(aoi)
        if coverage < 0.999:
            raise PipelineError(
                f"AOI is only {coverage:.1%} inside scene {scene.id}. Bhoomi V1 "
                "processes one scene at a time -- reduce the AOI or choose a scene "
                "that fully contains it.")

    from catalogue.base import SearchQuery
    grid = grid_for_aoi(SearchQuery(aoi=aoi).bbox(), resolution)
    decision = offset_decision(scene)
    present = decision.present

    raw = {b: raster_utils.read_to_grid(scene.href(b), grid,
                                        _resampling_for(resolution, b))
           for b in bands}

    invalid = np.zeros(grid.shape, dtype=bool)
    for array in raw.values():
        invalid |= (array == 0)

    warnings: list[str] = []
    if decision.warning:
        warnings.append(decision.warning)
    if "scl" in scene.assets:
        scl = raster_utils.read_scl_to_grid(scene.href("scl"), grid)
        invalid |= masking.scl_mask(scl, mask_snow=mask_snow)
    else:
        warnings.append(
            f"Scene {scene.id} has no SCL band; cloud and shadow are NOT masked.")
        logger.warning(warnings[-1])

    reflectance = [
        apply_mask(harmonize.to_reflectance(raw[b], scene.properties, present), invalid)
        for b in bands
    ]
    array = indices.INDEX_FUNCTIONS[index](*reflectance)
    kept = masking.valid_fraction(invalid)

    if kept < 0.5:
        warnings.append(
            f"Only {kept:.1%} of the AOI is valid after masking; interpret with care.")

    return IndexResult(array=array, grid=grid, index=index, scene_id=scene.id,
                       valid_fraction=kept, offset_present=present,
                       baseline=scene.processing_baseline,
                       offset_basis=decision.basis, warnings=warnings)


def _resampling_for(target_resolution: float, band: str):
    """Average when downsampling; bilinear otherwise.

    Averaging approximates what a coarser detector would have measured.
    Bilinear merely interpolates four samples, which is wrong for aggregation.
    """
    from rasterio.enums import Resampling

    native = 20.0 if band in {"swir16", "swir22", "scl"} else 10.0
    return Resampling.average if target_resolution > native else Resampling.bilinear


def compute_change(
    earlier: Scene,
    later: Scene,
    aoi: dict,
    index: str = "ndvi",
    resolution: float | None = None,
    threshold: float = 0.2,
) -> ChangeResult:
    """Difference one index across two scenes on a shared grid."""
    if earlier.acquired_at > later.acquired_at:
        earlier, later = later, earlier

    resolution = resolution or indices.INDEX_BANDS[index][1]
    a = compute_index(earlier, aoi, index, resolution)
    b = compute_index(later, aoi, index, resolution)

    diff, compat = change.difference(a.array, b.array,
                                     earlier.properties, later.properties)
    warnings = [*a.warnings, *b.warnings, *compat]
    stats = change.change_stats(diff, threshold=threshold, warnings=warnings)

    logger.info("change %s: %s -> %s, loss %.2f%% gain %.2f%% (%.2f:1)",
                index, earlier.id, later.id,
                stats.loss_fraction * 100, stats.gain_fraction * 100, stats.asymmetry)

    return ChangeResult(array=diff, grid=a.grid, index=index,
                        scene_ids=(earlier.id, later.id),
                        baselines=(earlier.processing_baseline,
                                   later.processing_baseline),
                        stats=stats, warnings=warnings,
                        earlier=a, later=b)
