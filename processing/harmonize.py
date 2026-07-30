"""Convert Sentinel-2 L2A digital numbers to surface reflectance.

This module exists because the obvious reading of the STAC metadata is wrong.

`earthsearch:boa_offset_applied: True` does NOT mean "the -1000 offset is present
in these pixels, subtract it". It means Earth Search has ALREADY applied the
correction -- the pixels are ready to use as DN / 10000.

Measured over 2.56M real pixels on 2026-07-30 (probes/verify_offset2.py): applying
the offset a second time puts ~65% of pixels outside [-1, 1], a range NDVI cannot
mathematically occupy. See PLAN.md 5.3.

Never infer the convention from acquisition date. The archive is retrospectively
reprocessed, so a 2020 acquisition can carry baseline 05.00.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

QUANTIFICATION_VALUE = 10000.0
BOA_ADD_OFFSET = -1000.0
#: Processing baseline from which ESA began applying BOA_ADD_OFFSET (2022-01-25).
OFFSET_BASELINE = 4.0

OFFSET_FLAG = "earthsearch:boa_offset_applied"
BASELINE_KEY = "s2:processing_baseline"


class HarmonizationError(ValueError):
    """Raised when the reflectance convention cannot be determined from metadata.

    Deliberately fatal. Guessing here produces confidently wrong output rather
    than a visible failure, which is the worse outcome.
    """


def parse_baseline(properties: dict) -> float | None:
    """Parse ``s2:processing_baseline`` (e.g. ``"05.00"``) to a float, or None."""
    raw = properties.get(BASELINE_KEY)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Unparseable %s: %r", BASELINE_KEY, raw)
        return None


#: Detector constants. Derived from physics, not fitted to observations: the
#: offset is exactly 1000 DN and reflectance cannot be meaningfully below about
#: -0.02 (-200 DN), so a scene carrying the offset has essentially no pixels
#: below ~800 DN. 700 keeps margin. Measured separation over 7 Kolkata scenes:
#: the offset-bearing scene had 0.00% of pixels below 700 DN; the others had
#: 3.48%-8.17%.
DARK_DN = 700.0
DARK_MIN_FRACTION = 0.01
MIN_SAMPLE_PIXELS = 10_000


def detect_offset_in_array(dn: np.ndarray, nodata: float = 0.0) -> bool:
    """Whether the BOA offset appears present in these pixels.

    Prefer :func:`detect_offset_in_scene`, which samples the whole tile. This
    variant is only safe on a window known to contain dark targets -- a small
    AOI of uniformly bright bare soil has no dark pixels either way, and would
    be misread as offset-bearing.
    """
    valid = dn[dn > nodata]
    if valid.size < MIN_SAMPLE_PIXELS:
        raise HarmonizationError(
            f"Only {valid.size} valid pixels; need {MIN_SAMPLE_PIXELS:,} to judge "
            "the reflectance convention reliably."
        )
    return float((valid < DARK_DN).mean()) < DARK_MIN_FRACTION


def detect_offset_in_scene(band_url: str, decimation: int = 32) -> bool:
    """Detect the offset from a decimated overview of the FULL scene.

    Reads the COG's built-in overview rather than the AOI window, so the sample
    spans the whole 110 km tile and reliably contains water or shadow. Costs a
    few hundred KB.
    """
    import rasterio

    with rasterio.open(band_url) as src:
        out = (max(src.height // decimation, 1), max(src.width // decimation, 1))
        data = src.read(1, out_shape=out).astype(np.float32)
    return detect_offset_in_array(data)


def reflectance_params(
    properties: dict,
    offset_present: bool | None = None,
) -> tuple[float, float]:
    """Return ``(scale, offset)`` where ``reflectance = DN * scale + offset``.

    ``offset_present`` should come from :func:`detect_offset_in_scene`. It takes
    precedence over metadata, because for this collection **no metadata field is
    reliable** (PLAN.md 5.3):

    - ``earthsearch:boa_offset_applied: False`` meant "offset present" on the
      2022 scene and "offset absent" on the 2025 scene.
    - ``raster:bands.offset`` reports -0.1 uniformly, contradicting measurement.
    - The GeoTIFF's own scale/offset tags are unset.

    Metadata is retained only as a cross-check that logs on disagreement.
    """
    scale = 1.0 / QUANTIFICATION_VALUE

    if offset_present is not None:
        metadata_says = _metadata_offset_present(properties)
        if metadata_says is not None and metadata_says != offset_present:
            logger.warning(
                "Metadata disagrees with pixels for baseline %s (flag=%r): metadata "
                "implies offset_present=%s, pixels say %s. Trusting the pixels.",
                properties.get(BASELINE_KEY), properties.get(OFFSET_FLAG),
                metadata_says, offset_present,
            )
        return scale, (BOA_ADD_OFFSET * scale if offset_present else 0.0)

    metadata_says = _metadata_offset_present(properties)
    if metadata_says is None:
        raise HarmonizationError(
            f"Cannot determine reflectance convention: {OFFSET_FLAG!r} absent and no "
            "pixel evidence supplied. Pass offset_present from "
            "detect_offset_in_scene()."
        )
    logger.warning(
        "Falling back to metadata for the reflectance convention. This field has "
        "been observed to be wrong; prefer detect_offset_in_scene()."
    )
    return scale, (BOA_ADD_OFFSET * scale if metadata_says else 0.0)


def _metadata_offset_present(properties: dict) -> bool | None:
    """What the metadata implies, or None if it says nothing. Unreliable."""
    if OFFSET_FLAG not in properties:
        return None
    if properties[OFFSET_FLAG]:
        return False  # provider already corrected it
    baseline = parse_baseline(properties)
    return baseline is not None and baseline >= OFFSET_BASELINE


def to_reflectance(
    dn: np.ndarray,
    properties: dict,
    offset_present: bool | None = None,
) -> np.ndarray:
    """Apply the correct scale/offset for this scene. Returns float32."""
    scale, offset = reflectance_params(properties, offset_present)
    return (dn.astype(np.float32) * np.float32(scale)) + np.float32(offset)


def baselines_match(properties_a: dict, properties_b: dict) -> bool:
    """Whether two scenes share a processing baseline.

    Change detection across differing baselines partly measures Sen2Cor version
    drift rather than ground change. Measured on the demo AOI: SCL-derived
    vegetation loss was overstated ~4x relative to NDVI for exactly this reason.
    See PLAN.md 5.4.4.
    """
    return parse_baseline(properties_a) == parse_baseline(properties_b)
