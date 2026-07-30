"""Normalized-difference spectral indices.

All inputs are surface reflectance (see harmonize.to_reflectance), not raw DN.
Indices are scale-invariant but NOT offset-invariant, which is why harmonization
must happen first -- subtracting a constant from both bands cancels in the
numerator but not the denominator (PLAN.md 5.3).
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

#: Denominators below this magnitude are treated as invalid rather than allowed
#: to produce inf. Never let a divide-by-zero reach a written raster.
EPSILON = 1e-10


class ImplausibleIndexError(ValueError):
    """Raised when too many pixels fall outside the range the index can occupy.

    A normalized difference of two non-negative reflectances is mathematically
    confined to [-1, 1]. A large excursion means the inputs are wrong -- almost
    always a harmonization error -- not that the landscape is unusual.

    This is an exception rather than a log line by design. During the 7-year
    Kolkata series a mis-applied offset drove ~65% of pixels out of range; the
    warning fired correctly but the calling script had raised the logging level
    and silenced it, so a median NDVI of 1.000 reached the analysis. A safety
    net a caller can mute is not a safety net.
    """


def normalized_difference(
    a: np.ndarray,
    b: np.ndarray,
    name: str = "index",
    max_invalid_fraction: float | None = 0.01,
) -> np.ndarray:
    """Compute ``(a - b) / (a + b)`` safely, in float32.

    Values are clamped to [-1, 1], since L2A reflectance can be slightly
    negative over dark targets. But if more than ``max_invalid_fraction`` of
    finite pixels needed clamping, this raises instead: that is a bug signature,
    not dark water. Pass ``None`` to disable the check (diagnostics only).
    """
    a = a.astype(np.float32, copy=False)
    b = b.astype(np.float32, copy=False)

    denominator = a + b
    denominator = np.where(np.abs(denominator) < EPSILON, np.nan, denominator)

    with np.errstate(invalid="ignore", divide="ignore"):
        result = (a - b) / denominator

    finite = np.isfinite(result)
    n_finite = int(finite.sum())
    n_out = int((finite & ((result < -1.0) | (result > 1.0))).sum())

    if n_out and n_finite:
        fraction = n_out / n_finite
        logger.warning("%s: %d pixels (%.3f%%) outside [-1, 1] before clamping",
                       name, n_out, fraction * 100.0)
        if max_invalid_fraction is not None and fraction > max_invalid_fraction:
            raise ImplausibleIndexError(
                f"{name}: {fraction:.1%} of pixels fell outside [-1, 1], over the "
                f"{max_invalid_fraction:.1%} limit. Inputs are almost certainly "
                "mis-harmonized -- check whether the BOA offset was applied "
                "correctly for this scene (see harmonize.py)."
            )

    return np.clip(result, -1.0, 1.0).astype(np.float32)


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """Vegetation index. Bands B08/B04 at 10 m.

    Chlorophyll absorbs red and leaf structure reflects NIR, so vegetation shows
    a large gap between them. Dense vegetation > 0.6; bare soil ~0.1-0.2; water
    negative.
    """
    return normalized_difference(nir, red, name="ndvi")


def ndwi(green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Surface water index (McFeeters). Bands B03/B08 at 10 m.

    This is McFeeters' NDWI for water *extent*, not Gao's NIR/SWIR formulation
    for vegetation water *content*. Document which one is meant -- reviewers
    notice the difference.
    """
    return normalized_difference(green, nir, name="ndwi")


def ndbi(swir: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Built-up index. Bands B11/B08, computed at 20 m (PLAN.md D4).

    B11 is natively 20 m; upsampling it to 10 m would invent detail the sensor
    never recorded. Known weakness: NDBI confuses bare soil with built-up. State
    that as a limitation rather than letting a reviewer find it.
    """
    return normalized_difference(swir, nir, name="ndbi")


INDEX_FUNCTIONS = {"ndvi": ndvi, "ndwi": ndwi, "ndbi": ndbi}

#: Which bands each index needs, and the resolution it is computed at.
INDEX_BANDS = {
    "ndvi": (("nir", "red"), 10.0),
    "ndwi": (("green", "nir"), 10.0),
    "ndbi": (("swir16", "nir"), 20.0),
}
