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

    separation = _seasonal_separation_days(properties_a, properties_b)
    if separation is not None and separation > SEASONAL_TOLERANCE_DAYS:
        warnings.append(
            f"Acquisitions are {separation} days apart in the year. Seasonal "
            "phenology may dominate the result; prefer a pair from the same "
            "part of the year."
        )

    return warnings


#: How far apart in the *year* two acquisitions may sit before phenology is a
#: serious confound. Crops and canopy move little over three weeks and a great
#: deal over a season.
#:
#: Tighter than 5.4.4's "same month across years" on purpose: a same-month pair
#: can still be 1 March against 31 March, which is a full month of growth. D11
#: chose its demo pair at six days, so the plan's own working standard is much
#: closer than a month.
SEASONAL_TOLERANCE_DAYS = 21


def _seasonal_separation_days(properties_a: dict, properties_b: dict) -> int | None:
    """Distance between two acquisitions in day-of-year, ignoring the year.

    Day-of-year, not calendar month, because the month is a crude proxy that is
    wrong in both directions: 27 February and 10 March are eleven days apart
    and would warn, while 1 March and 31 March are thirty days apart and would
    not. PLAN.md D11 already reasons this way -- it justifies the demo pair as
    "six days apart in day-of-year" -- so the check now measures what the plan
    was actually arguing about.

    Circular, so 31 December and 1 January are one day apart rather than 364.
    """
    from datetime import datetime

    def day_of_year(properties: dict) -> int | None:
        raw = str(properties.get("datetime") or "")[:10]
        try:
            return datetime.strptime(raw, "%Y-%m-%d").timetuple().tm_yday
        except ValueError:
            return None

    a, b = day_of_year(properties_a), day_of_year(properties_b)
    if a is None or b is None:
        return None
    gap = abs(a - b)
    return min(gap, 365 - gap)


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
