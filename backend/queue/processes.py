"""What Bhoomi can be asked to compute.

## Right now that is one thing, and it computes nothing

`fake` exists to exercise the queue on its own. PLAN.md 11 is explicit about
the order: *"queue plumbing with a fake 10-second job first, then real
processing. Debugging distributed job state and raster math simultaneously is
how weeks disappear."* So this registry deliberately does **not** yet contain
ndvi/ndwi/ndbi/change — submitting one returns 400 with the list of what does
exist, rather than accepting the job and completing it with no raster, which
would be a lie told in the status field.

Adding the real processes is the next commit. Each becomes a ProcessSpec whose
`run` calls into `pipeline`, and `fake` goes away with them.

## Estimates

`estimate_seconds` feeds 7.3's `estimated_seconds`. For real indices it will be
the measured fit from 8 -- `3.2 + 2.8 x Mpixels` -- which is why the signature
takes megapixels even though the fake process ignores them.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from backend.db.jobs import Job, JobStatus

logger = logging.getLogger(__name__)

#: Sentinel-2 at 10 m: 100 m^2 per pixel, so 1 km^2 is 0.01 Mpixel per band.
MPIXELS_PER_KM2_10M = 0.01


class Reporter(Protocol):
    """Moves the job into a state and persists the progress that goes with it."""

    def __call__(self, status: JobStatus) -> None:
        ...


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    scene_count: int
    description: str
    estimate_seconds: Callable[[float], float]
    run: Callable[[Reporter, Job], None]


def _fake_estimate(mpixels: float) -> float:
    return 10.0


def _run_fake(report: Reporter, job: Job) -> None:
    """Walk the 4.3 state machine over ~10 seconds, computing nothing.

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


FAKE = ProcessSpec(
    name="fake",
    scene_count=1,
    description="Queue plumbing check: sleeps ~10 s, produces no output.",
    estimate_seconds=_fake_estimate,
    run=_run_fake,
)

REGISTRY: dict[str, ProcessSpec] = {FAKE.name: FAKE}


def get(name: str) -> ProcessSpec | None:
    return REGISTRY.get(name)


def names() -> list[str]:
    return sorted(REGISTRY)


def estimate_for(spec: ProcessSpec, aoi_area_km2: float) -> int:
    """Seconds to report at submission (7.3). Rounded up; never below 1."""
    mpixels = aoi_area_km2 * MPIXELS_PER_KM2_10M
    return max(1, round(spec.estimate_seconds(mpixels)))
