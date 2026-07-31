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
from dataclasses import dataclass

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


#: The discriminator, and **why it is one-sided**.
#:
#: The offset is exactly 1000 DN and surface reflectance cannot be meaningfully
#: below about -0.02 (-200 DN). So an offset-BEARING product cannot hold valid
#: pixels below ~800 DN, and a scene whose floor sits below that is certainly
#: offset-ABSENT.
#:
#: The converse does not follow. A high floor is equally consistent with an
#: offset-bearing product and with a bright scene that simply contains no dark
#: target -- no water, no shadow, no dense canopy. Measured over 48 scenes
#: (2026-07-31, `probes/` companion data `outputs/calib_measurements.json`),
#: the two classes overlap completely in that regime:
#:
#:     offset ABSENT, floor at or above 800 DN : 922, 942, 983, 1094, 1464,
#:                                               1777, 1814, 1938, 2045, 2048
#:     offset PRESENT                          : 1003
#:
#: 1003 sits inside the absent range. No threshold on the floor separates them,
#: and a narrower "just above 800 means present" band fails too -- Delhi 2019 is
#: offset-absent with a floor of 1094. So the pixel test PROVES ABSENT or says
#: nothing, and `present=True` is only ever reached through metadata.
FLOOR_DN = 800.0

#: Which percentile of the valid distribution counts as "the floor".
#:
#: Not the minimum. The offset-present scene in the sample above holds exactly
#: **2 valid pixels out of 7,535,025** below 800 DN -- its true distribution
#: starts at 820 -- so a rule reading `min()` classifies it absent on a
#: rounding-level number of outliers. At the 0.1st percentile the same scene
#: reads 1003 DN. Ten scenes stand between the highest conclusive absent floor
#: (698 DN) and the threshold.
FLOOR_PERCENTILE = 0.1

MIN_SAMPLE_PIXELS = 10_000

#: How far to decimate when sampling the tile.
#:
#: Averaging raises the floor, so decimation still costs something. Measured on
#: real tiles, 2026-07-31, p0.1 in DN:
#:
#:     scene                          dec 4   dec 8  dec 16  dec 32
#:     chennai 2022-02-04 (PRESENT)    1003    1010    1021    1027
#:     kolkata 2019-04-30 (absent)      698     764     800     846   <- crosses
#:     delhi   2022-04-19 (absent)      648     798    1031    1299   <- crosses
#:     sundarbans 2022-03-22 (absent)    96     104     114     136
#:     ghats   2022-03-12 (absent)      238     252     278     354
#:     thar    2022-04-30 (absent)     1938    2012    2082    2175
#:
#: **But the failure mode is different in kind from the rule this replaced.**
#: The floor only ever moves up, and the pixel test's only positive claim is
#: "absent", so a coarser sample loses a verdict rather than inverting one. The
#: dark-fraction rule flipped scenes to the opposite answer; this one falls back
#: to metadata and says so.
#:
#: 4 rather than 8 or 16 for a concrete reason: Delhi 2022-04-19 is conclusive
#: at 4 and 8 but not at 16, and its metadata claims the offset is present when
#: it is not -- so at 16 the fallback would get that scene wrong. Kolkata
#: 2019-04-30 is conclusive only at 4. The cost is paid once per scene ever into
#: a cache shared by every worker (PLAN.md 6), and the cache now stores the
#: measurement rather than the verdict, so this can be revisited without
#: re-reading anything.
#:
#: Cost, measured in the worker container against a warm connection: decimation
#: 4 is ~11 s, 8 is ~2.2 s, 16 is ~0.6 s. Cold connections are much slower and
#: much noisier -- unwarmed reads at decimation 4 have been seen at 159 s and,
#: on 2026-07-31, at **492 s** inside a freshly started worker container. That
#: is long enough to blow PLAN.md 8's 10-minute job timeout on its own, and a
#: two-date change job needs two of them. See PLAN.md 5.3.2 -- an open
#: operational risk, not a settled cost.
DEFAULT_DECIMATION = 4


@dataclass(frozen=True)
class OffsetEvidence:
    """What the pixels alone can say about the reflectance convention.

    One-sided by construction: see :data:`FLOOR_DN`. ``conclusive`` means the
    floor proves the offset is ABSENT. It is never evidence that the offset is
    present.
    """

    floor_dn: float
    sample_pixels: int

    @property
    def conclusive(self) -> bool:
        """Whether the floor is low enough to prove the offset absent."""
        return self.floor_dn < FLOOR_DN


@dataclass(frozen=True)
class OffsetDecision:
    """The resolved convention, and what resolved it."""

    present: bool
    #: ``"pixels"``, ``"baseline"`` or ``"metadata"``.
    basis: str
    evidence: OffsetEvidence | None = None
    warning: str | None = None


def measure_offset_floor(dn: np.ndarray, nodata: float = 0.0) -> OffsetEvidence:
    """Measure the floor of the valid DN distribution.

    Prefer :func:`measure_offset_floor_in_scene`, which samples the whole tile.
    A small AOI window is a worse sample for the same reason it always was: it
    may contain no dark target even when the surrounding tile does.
    """
    valid = dn[dn > nodata]
    if valid.size < MIN_SAMPLE_PIXELS:
        raise HarmonizationError(
            f"Only {valid.size} valid pixels; need {MIN_SAMPLE_PIXELS:,} to judge "
            "the reflectance convention reliably."
        )
    floor = float(np.percentile(valid, FLOOR_PERCENTILE))
    evidence = OffsetEvidence(floor_dn=floor, sample_pixels=int(valid.size))

    # Logged at every call. This number is the whole pixel-side decision, and
    # the failure it guards against is one where a wrong answer still looks
    # like a plausible raster.
    logger.info(
        "offset floor: p%.1f = %.0f DN over %d valid pixels (threshold %.0f) -> %s",
        FLOOR_PERCENTILE, floor, valid.size, FLOOR_DN,
        "offset absent" if evidence.conclusive else "inconclusive from pixels")
    return evidence


def measure_offset_floor_in_scene(
    band_url: str, decimation: int = DEFAULT_DECIMATION
) -> OffsetEvidence:
    """Measure the floor from a decimated overview of the FULL scene.

    Reads across the whole 110 km tile rather than the AOI window, so the sample
    has the best chance of containing water or shadow.
    """
    import rasterio

    with rasterio.open(band_url) as src:
        out = (max(src.height // decimation, 1), max(src.width // decimation, 1))
        data = src.read(1, out_shape=out).astype(np.float32)
    return measure_offset_floor(data)


def resolve_offset(evidence: OffsetEvidence | None, properties: dict) -> OffsetDecision:
    """Combine pixel evidence with metadata into a decision.

    The order matters and is not arbitrary:

    1. **A pre-04.00 baseline settles it.** The offset convention did not exist
       before Processing Baseline 04.00, so such a product cannot carry it.
    2. **Otherwise the pixels settle it, if they can.** A floor below
       :data:`FLOOR_DN` proves the offset absent, and this overrides metadata --
       which is the point. Over the 48-scene sample, ``boa_offset_applied`` was
       wrong on three scenes (Kolkata 2022-03-20, Delhi 2022-04-19, Sundarbans
       2022-03-22, all claiming the offset was not applied when the pixels are
       plainly unshifted). All three have floors of 240, 648 and 96 DN, so the
       pixel test catches every one and the flag is never consulted for them.
    3. **Only where the pixels are silent does metadata decide**, and the result
       carries a warning. This is the bright-arid case: no water, no shadow,
       nothing dark to shift. Metadata resolved all 11 such scenes in the sample
       correctly, but it is the field this project has already caught lying, so
       the uncertainty travels with the output rather than being swallowed.
    """
    baseline = parse_baseline(properties)

    if baseline is not None and baseline < OFFSET_BASELINE:
        return OffsetDecision(present=False, basis="baseline", evidence=evidence)

    if evidence is not None and evidence.conclusive:
        metadata_says = _metadata_offset_present(properties)
        if metadata_says is True:
            logger.warning(
                "Metadata claims the offset is present for baseline %s (flag=%r) but "
                "the floor is %.0f DN, which an offset-bearing product cannot reach. "
                "Trusting the pixels.",
                properties.get(BASELINE_KEY), properties.get(OFFSET_FLAG),
                evidence.floor_dn)
        return OffsetDecision(present=False, basis="pixels", evidence=evidence)

    metadata_says = _metadata_offset_present(properties)
    if metadata_says is None:
        raise HarmonizationError(
            "Cannot determine the reflectance convention: the pixel floor is "
            f"{'unmeasured' if evidence is None else f'{evidence.floor_dn:.0f} DN, too high to be conclusive'}"
            f" and {OFFSET_FLAG!r} is absent. Refusing to guess -- a wrong choice "
            "here shifts NDVI by ~0.24 while leaving every value in range."
        )

    floor = "unmeasured" if evidence is None else f"{evidence.floor_dn:.0f} DN"
    warning = (
        f"The BOA offset could not be determined from pixels: the scene's floor is "
        f"{floor}, and no valid pixel below {FLOOR_DN:.0f} DN means the tile contains "
        f"no dark target rather than that the offset is present. Fell back to "
        f"{OFFSET_FLAG} (offset_present={metadata_says}), a field observed wrong on "
        f"3 of 48 measured scenes. If this is wrong, every value shifts by ~0.24 and "
        f"still looks plausible."
    )
    logger.warning(warning)
    return OffsetDecision(present=bool(metadata_says), basis="metadata",
                          evidence=evidence, warning=warning)


def reflectance_params(
    properties: dict,
    offset_present: bool | None = None,
) -> tuple[float, float]:
    """Return ``(scale, offset)`` where ``reflectance = DN * scale + offset``.

    ``offset_present`` should come from :func:`resolve_offset`. It takes
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
            "pixel evidence supplied. Pass offset_present from resolve_offset()."
        )
    logger.warning(
        "Falling back to metadata for the reflectance convention. This field has "
        "been observed to be wrong; prefer resolve_offset()."
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
