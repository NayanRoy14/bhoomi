"""Cloud and shadow masking via the Sentinel-2 Scene Classification Layer (SCL).

NDVI over cloud or cloud shadow is meaningless, and differencing two unmasked
dates compounds the error into something that looks like real change. Masking is
P0, not polish (PLAN.md 5.2).

The class table below was verified against real pixels on 2026-07-30
(probes/verify_scl.py) rather than taken from documentation: no unexpected class
values appeared over the demo AOI.
"""

from __future__ import annotations

import numpy as np

#: SCL class value -> human-readable name.
SCL_CLASSES: dict[int, str] = {
    0: "no_data",
    1: "saturated_or_defective",
    2: "dark_area_cast_shadow",
    3: "cloud_shadow",
    4: "vegetation",
    5: "not_vegetated",
    6: "water",
    7: "unclassified",
    8: "cloud_medium_probability",
    9: "cloud_high_probability",
    10: "thin_cirrus",
    11: "snow_ice",
}

#: Classes masked by default. Snow (11) is included but is context-dependent.
DEFAULT_MASK_CLASSES = frozenset({0, 1, 2, 3, 8, 9, 10, 11})

#: Classes that are valid ground observations.
KEEP_CLASSES = frozenset({4, 5, 6, 7})


def scl_mask(
    scl: np.ndarray,
    mask_classes: frozenset[int] | set[int] | None = None,
    mask_snow: bool = True,
) -> np.ndarray:
    """Return a boolean array where ``True`` marks pixels to EXCLUDE."""
    classes = set(DEFAULT_MASK_CLASSES if mask_classes is None else mask_classes)
    if not mask_snow:
        classes.discard(11)
    return np.isin(scl, list(classes))


def apply_mask(array: np.ndarray, invalid: np.ndarray) -> np.ndarray:
    """Set masked pixels to NaN. Returns float32; does not modify the input."""
    out = array.astype(np.float32, copy=True)
    out[invalid] = np.nan
    return out


def valid_fraction(invalid: np.ndarray) -> float:
    """Fraction of pixels that survive masking, in [0, 1].

    Record this on every output (PLAN.md 6). A result that is 80% cloud should
    say so rather than render as a mostly-empty raster the user misreads.
    """
    if invalid.size == 0:
        return 0.0
    return float(1.0 - (invalid.sum() / invalid.size))


def class_histogram(scl: np.ndarray) -> dict[str, float]:
    """Percentage share of each SCL class present. Diagnostic only.

    WARNING: do not report these as a change metric across two dates. SCL is a
    classifier whose version tracks the processing baseline, so its class
    boundaries move between scenes. On the demo AOI this overstated vegetation
    loss ~4x versus NDVI. See PLAN.md 5.4.4.
    """
    total = scl.size
    if total == 0:
        return {}
    values, counts = np.unique(scl, return_counts=True)
    return {
        SCL_CLASSES.get(int(v), f"unknown_{int(v)}"): float(c / total * 100.0)
        for v, c in zip(values, counts)
    }
