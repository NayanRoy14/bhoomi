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
without touching the network, and it is what the delivery tests use.

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
#: Only paid once per scene ever, but a first-time estimate that omits it is
#: wrong by more than a third on a small AOI.
OFFSET_DETECTION_SECONDS = 6.0

#: PLAN.md 6: anonymous job outputs expire after 30 days. Demo outputs are
#: pinned by setting this to None on the row afterwards.
RETENTION = timedelta(days=30)


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


def _publish(result, job: Job, index: str) -> OutputSpec:
    """Write the COG, move it into storage, and describe it.

    Written to a scratch file first because rasterio writes to a path, and COG
    creation is not something to do inside the storage backend -- with S3 the
    two are unavoidably separate steps anyway.
    """
    key = storage.key_for(str(job.id))
    backend = storage.get_storage()

    # Built inside the backend's own scratch area when it has one, so storing
    # it is a rename on the same filesystem rather than a copy of ~20 MB.
    with tempfile.TemporaryDirectory(prefix="bhoomi-", dir=backend.scratch_dir()) as scratch:
        path = Path(scratch) / key
        result.write(path)
        size = backend.put(path, key)

    return OutputSpec(
        output_type="index_raster",
        cog_uri=backend.url_for(key) or storage.download_url(str(job.id)),
        bounds=_bounds_wgs84(result.grid),
        crs=str(result.grid.crs),
        resolution_m=result.grid.resolution,
        size_bytes=size,
        valid_fraction=result.valid_fraction,
        stats=result.stats(),
        expires_at=datetime.now(timezone.utc) + RETENTION,
        warnings=list(result.warnings),
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
        return [_publish(result, job, index)]

    return run


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


def get(name: str) -> ProcessSpec | None:
    return REGISTRY.get(name)


def names() -> list[str]:
    return sorted(REGISTRY)


def estimate_for(spec: ProcessSpec, aoi_area_km2: float) -> int:
    """Seconds to report at submission (7.3). Rounded up; never below 1."""
    mpixels = aoi_area_km2 * MPIXELS_PER_KM2_10M
    return max(1, round(spec.estimate_seconds(mpixels)))
