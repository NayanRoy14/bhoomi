"""What Bhoomi can be asked to compute.

Each process is a ProcessSpec whose `run` reports the stage it has reached and
returns whatever outputs it produced. It does **not** move the job into a
terminal state and does not write to the database — the runner does both, so a
process cannot mark itself completed after failing halfway.

## The real indices, and what porting them cost

PLAN.md 11 predicted that `processing/` would need no changes to be driven
from the queue, "which is the payoff for keeping it web-free". That held:
`_run_index` below calls `pipeline.compute_index` and `IndexResult.write` with
no modification to either, and nothing under `processing/` or `catalogue/`
changed to make this work. The only new code is the part that is genuinely
web-shaped — publishing the file and describing it for 7.5.

`fake` is kept alongside them. It is the only way to exercise the queue
without touching the network, and it is what the delivery tests use. It is
registered but **unlisted** (`public=False`): a client discovering
`change, fake, ndbi, ndvi, ndwi` would have no way to tell that one of those
computes nothing, and a process catalogue is a statement about what the server
is for. `get("fake")` still resolves and it still executes, so nothing that
asks for it by name notices.

## Estimates

`estimate_seconds` feeds 7.3's `estimated_seconds`, using the measured fit
from 8: `3.2 + 2.8 x Mpixels`, plus offset detection when it has not been paid
for this scene yet.
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

import pipeline
from backend import storage
from processing import cog
from backend.db.jobs import Job, JobStatus
from backend.resolve import resolve_scene

logger = logging.getLogger(__name__)

#: Sentinel-2 at 10 m: 100 m^2 per pixel, so 1 km^2 is 0.01 Mpixel per band.
MPIXELS_PER_KM2_10M = 0.01

#: PLAN.md 8, measured: seconds ~ 3.2 + 2.8 x Mpixels, dominated by a fixed
#: ~3 s of connection setup and COG header reads rather than by throughput.
FIXED_SECONDS = 3.2
SECONDS_PER_MPIXEL = 2.8

#: Detecting the BOA offset reads a decimated overview of the full tile (8).
#: Only paid once per scene ever -- and the cache is shared, so it is once per
#: scene across every worker -- but a first-time estimate that omits it is
#: wrong by several times over on a small AOI.
#:
#: 11 s, measured in the worker container against a warm connection at the
#: decimation the detector now uses (harmonize.DEFAULT_DECIMATION = 4). The old
#: 6 s belonged to decimation 32, which was the sampling that misclassified
#: scenes (5.3.1).
#:
#: A cold connection costs far more -- a first job after the container starts
#: has been seen at 65 s end to end -- so this is the steady state, not a
#: worst case. `estimated_seconds` is advisory, and the status endpoint is what
#: tells the truth while a job runs.
OFFSET_DETECTION_SECONDS = 11.0

#: PLAN.md 6: anonymous job outputs expire after 30 days. Demo outputs are
#: pinned by setting this to None on the row afterwards.
RETENTION = timedelta(days=30)


class InvalidOutput(RuntimeError):
    """A finished raster failed COG validation and was not published.

    Raised rather than warned: an invalid COG reaches the user looking fine and
    degrades the tile server silently, which is worse than a failed job.
    """


class Reporter(Protocol):
    """Moves the job into a state and persists the progress that goes with it."""

    def __call__(self, status: JobStatus) -> None:
        ...


@dataclass(frozen=True)
class OutputSpec:
    """One product, described for `outputs` (6) and 7.5.

    Returned rather than written, so the process stays free of persistence and
    the runner remains the only thing that touches job state.
    """

    output_type: str
    cog_uri: str
    bounds: dict
    crs: str
    resolution_m: float
    size_bytes: int | None = None
    valid_fraction: float | None = None
    stats: dict | None = None
    expires_at: datetime | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    scene_count: int
    description: str
    estimate_seconds: Callable[[float], float]
    run: Callable[[Reporter, Job], list[OutputSpec]]

    #: Whether to advertise this process to clients. False means *unlisted*,
    #: not disabled: `get()` still returns it and it still executes, so the
    #: delivery tests are unaffected. Only `fake` sets it, and only because a
    #: catalogue is a claim about what a server is for -- a client discovering
    #: `change, fake, ndbi, ndvi, ndwi` has to work out for itself that one of
    #: those computes nothing. Diagnostics belong in the registry, not in the
    #: shop window.
    public: bool = True


def _index_estimate(mpixels: float) -> float:
    return FIXED_SECONDS + SECONDS_PER_MPIXEL * mpixels + OFFSET_DETECTION_SECONDS


def _bounds_wgs84(grid) -> dict:
    """The grid's footprint as GeoJSON in EPSG:4326.

    `outputs.bounds` is GEOMETRY(Polygon, 4326) but a Grid is in UTM (D4-era
    output is EPSG:32645 over Kolkata), so this is a reprojection, not a copy.
    """
    from rasterio.warp import transform_bounds

    west, south, east, north = transform_bounds(grid.crs, "EPSG:4326", *grid.bounds())
    return {
        "type": "Polygon",
        "coordinates": [[[west, south], [east, south], [east, north],
                         [west, north], [west, south]]],
    }


def _publish(result, job: Job, *, output_type: str, stats: dict,
             valid_fraction: float, warnings: list[str],
             variant: str | None = None) -> OutputSpec:
    """Write the COG, move it into storage, and describe it.

    Written to a scratch file first because rasterio writes to a path, and COG
    creation is not something to do inside the storage backend -- with S3 the
    two are unavoidably separate steps anyway.

    `stats` and `valid_fraction` are passed rather than read off `result`,
    because an index and a change raster summarise themselves differently: an
    index reports a distribution, a change raster reports loss against gain
    (§5.4.4). Only `write` and `grid` are common to both.

    `variant` is None for a job's primary output, which keeps its key on the
    job id alone. A change job's two per-date rasters pass one.
    """
    key = storage.key_for(str(job.id), variant)
    backend = storage.get_storage()

    # Built inside the backend's own scratch area when it has one, so storing
    # it is a rename on the same filesystem rather than a copy of ~20 MB.
    with tempfile.TemporaryDirectory(prefix="bhoomi-", dir=backend.scratch_dir()) as scratch:
        path = Path(scratch) / key
        result.write(path)

        # Checked before publishing, which is what the README claims and what
        # cog.validate_cog's own docstring asks for -- and was not happening.
        # An invalid COG still opens in QGIS but makes a tile server read
        # badly, so the failure surfaces later as "tiles are slow", which is
        # very hard to trace back here.
        valid, messages = cog.validate_cog(path)
        if not valid:
            raise InvalidOutput(f"Output failed COG validation: {'; '.join(messages)}")

        size = backend.put(path, key)

    return OutputSpec(
        output_type=output_type,
        cog_uri=backend.url_for(key) or storage.download_url(str(job.id), variant),
        bounds=_bounds_wgs84(result.grid),
        crs=str(result.grid.crs),
        resolution_m=result.grid.resolution,
        size_bytes=size,
        valid_fraction=valid_fraction,
        stats=stats,
        expires_at=datetime.now(timezone.utc) + RETENTION,
        warnings=warnings,
    )


def _run_index(index: str) -> Callable[[Reporter, Job], list[OutputSpec]]:
    """Build the runner for one index. The pipeline call is unchanged."""

    def run(report: Reporter, job: Job) -> list[OutputSpec]:
        report(JobStatus.SEARCHING)
        scene = resolve_scene(job.scene_ids[0])

        # READING and PROCESSING are reported around a single pipeline call:
        # compute_index reads bands and computes in one pass, and splitting it
        # to make the progress bar more granular would mean reading twice.
        report(JobStatus.READING)
        result = pipeline.compute_index(
            scene, job.aoi, index,
            mask_snow=bool(job.parameters.get("mask_snow", True)),
        )
        report(JobStatus.PROCESSING)

        for warning in result.warnings:
            logger.warning("job %s: %s", job.id, warning)

        report(JobStatus.WRITING_COG)
        return [_publish(result, job, output_type="index_raster",
                         stats=result.stats(), valid_fraction=result.valid_fraction,
                         warnings=list(result.warnings))]

    return run


#: The index a change job differences when `parameters.index` says nothing.
DEFAULT_CHANGE_INDEX = "ndvi"


def _change_estimate(mpixels: float) -> float:
    """Two dates: two index computations, and two scenes to measure."""
    return 2 * (FIXED_SECONDS + SECONDS_PER_MPIXEL * mpixels + OFFSET_DETECTION_SECONDS)


def _change_stats(stats) -> dict:
    """`ChangeStats` as JSON for `outputs.stats` (§6) and 7.5.

    §5.4.4 rule 3: report the loss/gain asymmetry *alongside* the mean, never
    the mean alone. On the demo AOI the mean NDVI shift is only -0.027 while
    9.73 % of pixels lost more than 0.2 and 3.26 % gained more than it -- the
    mean understates a real change concentrated in a minority of pixels, and
    the 3:1 ratio is what distinguishes it from noise, which moves both
    directions roughly equally.
    """
    asymmetry = stats.asymmetry
    return {
        "mean": stats.mean,
        "median": stats.median,
        "loss_fraction": stats.loss_fraction,
        "gain_fraction": stats.gain_fraction,
        "threshold": stats.threshold,
        # inf is not JSON; it means "loss with no gain at all to divide by".
        "asymmetry": None if asymmetry == float("inf") else asymmetry,
    }


def _run_change(report: Reporter, job: Job) -> list[OutputSpec]:
    """Difference one index across two dates (PLAN.md 5.4.4).

    `pipeline.compute_change` does the work and already enforces what matters:
    it orders the pair chronologically, derives one output grid from the AOI
    and reprojects *both* scenes onto it rather than onto each other, unions
    the masks, and returns compatibility warnings -- including the processing
    baseline mismatch that §5.3 identified as partly measuring Sen2Cor version
    drift rather than ground change.

    A baseline mismatch warns rather than refuses. §5.3 asks the API to *flag*
    it, and refusing would make whole year-pairs unusable for a confound the
    user may reasonably accept once told.
    """
    index = str(job.parameters.get("index") or DEFAULT_CHANGE_INDEX)

    report(JobStatus.SEARCHING)
    earlier, later = (resolve_scene(scene_id) for scene_id in job.scene_ids[:2])

    report(JobStatus.READING)
    result = pipeline.compute_change(earlier, later, job.aoi, index)
    report(JobStatus.PROCESSING)

    for warning in result.warnings:
        logger.warning("job %s: %s", job.id, warning)

    report(JobStatus.WRITING_COG)
    outputs = [_publish(result, job, output_type="change_raster",
                        stats=_change_stats(result.stats),
                        valid_fraction=result.stats.valid_fraction,
                        warnings=list(result.warnings))]

    # The two sides of the difference, so the interface can show *what*
    # changed rather than only how much (PLAN.md 11, before/after swipe). A
    # difference raster cannot be un-differenced -- +0.3 could be bare ground
    # becoming scrub or forest becoming denser forest, and only the two dates
    # distinguish them.
    #
    # Published after the change raster, and failures here are not allowed to
    # lose it: the difference is the result the user asked for, and a job that
    # threw away a completed analysis because a supplementary render failed
    # would be trading the answer for a picture.
    for variant, side in (("earlier", result.earlier), ("later", result.later)):
        if side is None:
            continue
        try:
            outputs.append(_publish(
                side, job, output_type=f"{variant}_{result.index}",
                stats=side.stats(), valid_fraction=side.valid_fraction,
                warnings=list(side.warnings), variant=variant))
        except Exception as exc:
            logger.warning("job %s: %s date raster not published (%s); the change "
                           "raster is unaffected", job.id, variant, exc)

    return outputs


def _fake_estimate(mpixels: float) -> float:
    return 10.0


def _run_fake(report: Reporter, job: Job) -> list[OutputSpec]:
    """Walk the 4.3 state machine over ~10 seconds, computing nothing.

    Kept now that the real indices exist: it is the only process that exercises
    the queue without touching the network, which is what makes the delivery
    tests fast and deterministic.

    The sleeps are uneven on purpose: equal steps would hide an off-by-one in
    progress reporting, and a frontend polling at 2 s (7.4) should see the
    status change at times that are not a multiple of its own interval.
    """
    for status, seconds in (
        (JobStatus.SEARCHING, 2.0),
        (JobStatus.READING, 3.0),
        (JobStatus.PROCESSING, 3.5),
        (JobStatus.WRITING_COG, 1.5),
    ):
        report(status)
        time.sleep(seconds)
    return []


FAKE = ProcessSpec(
    name="fake",
    scene_count=1,
    description="Queue plumbing check: sleeps ~10 s, produces no output.",
    estimate_seconds=_fake_estimate,
    run=_run_fake,
    public=False,
)

#: NDVI/NDWI at 10 m, NDBI at 20 m -- D4: an index is computed at the coarsest
#: native resolution of its inputs, and `pipeline` already applies that.
_INDEX_DESCRIPTIONS = {
    "ndvi": "Normalized Difference Vegetation Index, (nir - red) / (nir + red), 10 m.",
    "ndwi": "Normalized Difference Water Index, (green - nir) / (green + nir), 10 m.",
    "ndbi": "Normalized Difference Built-up Index, (swir16 - nir) / (swir16 + nir), 20 m.",
}

REGISTRY: dict[str, ProcessSpec] = {FAKE.name: FAKE}

for _name, _description in _INDEX_DESCRIPTIONS.items():
    REGISTRY[_name] = ProcessSpec(
        name=_name,
        scene_count=1,
        description=_description,
        estimate_seconds=_index_estimate,
        run=_run_index(_name),
    )

REGISTRY["change"] = ProcessSpec(
    name="change",
    #: 7.3: exactly two, chronological. `pipeline.compute_change` sorts them by
    #: acquisition time anyway, so a user who submits them backwards gets the
    #: right answer rather than a sign-flipped one.
    scene_count=2,
    description=(
        "Two-date difference of an index: INDEX(later) - INDEX(earlier). "
        "`parameters.index` selects which, default ndvi."
    ),
    estimate_seconds=_change_estimate,
    run=_run_change,
)

#: What `parameters.index` may name. Change differences an index, so the set is
#: the indices -- not the process registry, which also contains `change` itself
#: and `fake`.
CHANGEABLE_INDICES = tuple(sorted(_INDEX_DESCRIPTIONS))


def get(name: str) -> ProcessSpec | None:
    return REGISTRY.get(name)


def names(include_hidden: bool = False) -> list[str]:
    """Registered process names, advertised ones by default.

    The default is the *public* list because every caller that shows a name to
    a client -- the OGC process list, and the "available" array in an
    unknown-process error -- wants that one. `include_hidden=True` is for
    diagnostics, where hiding a registered process would make a log lie about
    why a job could not be dispatched.
    """
    return sorted(name for name, spec in REGISTRY.items()
                  if include_hidden or spec.public)


def estimate_for(spec: ProcessSpec, aoi_area_km2: float) -> int:
    """Seconds to report at submission (7.3). Rounded up; never below 1."""
    mpixels = aoi_area_km2 * MPIXELS_PER_KM2_10M
    return max(1, round(spec.estimate_seconds(mpixels)))
