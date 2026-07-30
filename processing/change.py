"""Two-date change detection.

Harder than it looks, for three reasons learned by measurement on 2026-07-30:

1. Both dates must land on an identical grid (raster_utils.Grid), not be
   reprojected onto each other.
2. A pixel is valid in the difference only if valid on BOTH dates.
3. Processing baselines must match, or part of the "change" is Sen2Cor version
   drift. On the demo AOI, SCL-derived vegetation loss read as -66% while NDVI
   read -16% -- a 4x overstatement caused by exactly this. See PLAN.md 5.4.4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .harmonize import baselines_match, parse_baseline

logger = logging.getLogger(__name__)


@dataclass
class ChangeStats:
    """Summary of a change raster.

    ``mean`` alone is misleading when change is concentrated in a minority of
    pixels: the demo AOI has a mean NDVI shift of only -0.027 while 9.73% of
    pixels lost more than 0.2. The loss/gain asymmetry is the honest headline --
    noise moves both directions roughly equally, so a lopsided ratio is evidence
    of real change rather than drift.
    """

    mean: float
    median: float
    loss_fraction: float
    gain_fraction: float
    threshold: float
    valid_fraction: float
    warnings: list[str] = field(default_factory=list)

    @property
    def asymmetry(self) -> float:
        """Loss:gain ratio. ~1.0 suggests noise; markedly above 1 suggests real loss."""
        if self.gain_fraction <= 0.0:
            return float("inf") if self.loss_fraction > 0 else 1.0
        return self.loss_fraction / self.gain_fraction


def check_scene_compatibility(properties_a: dict, properties_b: dict) -> list[str]:
    """Return human-readable warnings about comparing these two scenes.

    Warnings, not errors: the user may legitimately want the comparison. But it
    must never happen silently.
    """
    warnings: list[str] = []

    if not baselines_match(properties_a, properties_b):
        warnings.append(
            f"Processing baselines differ ({parse_baseline(properties_a)} vs "
            f"{parse_baseline(properties_b)}). Part of the measured change may be "
            "Sen2Cor version drift rather than change on the ground."
        )

    month_a = str(properties_a.get("datetime", ""))[5:7]
    month_b = str(properties_b.get("datetime", ""))[5:7]
    if month_a and month_b and month_a != month_b:
        warnings.append(
            f"Acquisitions are from different months ({month_a} vs {month_b}). "
            "Seasonal phenology may dominate the result."
        )

    return warnings


def difference(
    index_a: np.ndarray,
    index_b: np.ndarray,
    properties_a: dict | None = None,
    properties_b: dict | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Compute ``index_b - index_a`` with the union of both masks.

    Inputs must already be on the same grid; shapes are checked, alignment is
    the caller's responsibility via ``raster_utils.grid_for_aoi``.
    """
    if index_a.shape != index_b.shape:
        raise ValueError(
            f"Shape mismatch: {index_a.shape} vs {index_b.shape}. Both dates must be "
            "read onto the same Grid before differencing."
        )

    warnings: list[str] = []
    if properties_a is not None and properties_b is not None:
        warnings = check_scene_compatibility(properties_a, properties_b)
        for message in warnings:
            logger.warning("change detection: %s", message)

    # NaN propagates, which gives the mask union for free.
    return (index_b.astype(np.float32) - index_a.astype(np.float32)), warnings


def change_stats(
    diff: np.ndarray,
    threshold: float = 0.2,
    warnings: list[str] | None = None,
) -> ChangeStats:
    """Summarise a change raster, reporting asymmetry rather than mean alone."""
    finite = np.isfinite(diff)
    n_finite = int(finite.sum())
    if n_finite == 0:
        return ChangeStats(float("nan"), float("nan"), 0.0, 0.0, threshold, 0.0,
                           list(warnings or []))

    values = diff[finite]
    return ChangeStats(
        mean=float(values.mean()),
        median=float(np.median(values)),
        loss_fraction=float((values < -threshold).sum() / n_finite),
        gain_fraction=float((values > threshold).sum() / n_finite),
        threshold=threshold,
        valid_fraction=float(n_finite / diff.size),
        warnings=list(warnings or []),
    )
