"""OGC API - Processes, Part 1: Core (PLAN.md 7.6).

**A facade, not a second implementation.** Every submission goes through
`backend.api.submit.submit_job`, the same function `/api/v1/jobs` calls, and
reads the same `jobs` table. A job created here is indistinguishable from one
created there, because it *is* one -- `/ogc/jobs/{id}` and `/api/v1/jobs/{id}`
will both describe it, in their own vocabularies.

That is the whole point of the standard. The value is not the URL shape; it is
that a client which has never heard of Bhoomi can discover the processes, read
their input schemas, execute one, and fetch the result -- because it already
knows how to talk to any OGC processing server. 7.6's acceptance test says so:
"a QGIS user, or a Python script using owslib or plain requests, executes an
NDVI process and loads the result -- without opening the website."

**Execution is always asynchronous.** Part 1 Core lets a server support
synchronous execution, and a client asks for async with `Prefer: respond-async`.
Bhoomi has no synchronous mode to offer: a job that reads bands over HTTP can
take minutes (PLAN.md 5.3.2 records 492 seconds for one offset measurement), so
holding a connection open for it would be a worse lie than an honest 201. The
server therefore returns 201 + Location regardless, and echoes
`Preference-Applied` when the client did ask.

O5 resolved by building rather than mounting pygeoapi: this is a few hundred
lines against a second framework, its own config format and a second way to
reach the database.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from backend.api import errors, schemas
from backend.api.deps import get_catalogue, get_job_store, get_scene_store
from backend.api.submit import submit_job
from backend.db import SceneStore
from backend.db.jobs import Job, JobStatus, JobStore
from backend.queue import processes
from catalogue import Catalogue
from processing import __version__

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ogc"])

#: Conformance classes this implementation actually meets. Declaring one that
#: is not implemented is worse than declaring none: a client trusts this list
#: and will call what it advertises.
#:
#: Not declared, deliberately:
#:   .../conf/sync-execute   -- there is no synchronous mode (see the module docstring)
#:   .../conf/dismiss        -- DELETE /jobs/{id} is not implemented; PLAN.md 12
#:                              lists job cancellation as hardening, and claiming
#:                              it before it exists would break a client that
#:                              tried to cancel a runaway job.
#:   .../conf/callback       -- no subscriber callbacks
CONFORMANCE = [
    "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/ogc-process-description",
    "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/json",
    "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/job-list",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/json",
]

#: OGC job status vocabulary (Part 1 Core, `statusCode`). Bhoomi's own state
#: machine (PLAN.md 4.3) is finer-grained because the UI shows the stage; the
#: standard has five values and every one of ours has to land on one of them.
#:
#: `timed_out` maps to `failed`, not `dismissed`: `dismissed` means the client
#: asked for the job to stop, and a job killed at the 10-minute limit did not
#: produce a result and was nobody's decision but the server's.
_STATUS = {
    JobStatus.QUEUED: "accepted",
    JobStatus.SEARCHING: "running",
    JobStatus.READING: "running",
    JobStatus.PROCESSING: "running",
    JobStatus.WRITING_COG: "running",
    JobStatus.COMPLETED: "successful",
    JobStatus.FAILED: "failed",
    JobStatus.TIMED_OUT: "failed",
    JobStatus.CANCELLED: "dismissed",
}

COG_MEDIA_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"


# --------------------------------------------------------------------- models

class Link(BaseModel):
    href: str
    rel: str
    type: str | None = None
    title: str | None = None


class LandingPage(BaseModel):
    title: str
    description: str
    links: list[Link]


class Conformance(BaseModel):
    conformsTo: list[str]


class ProcessSummary(BaseModel):
    id: str
    title: str
    description: str
    version: str
    jobControlOptions: list[str]
    outputTransmission: list[str]
    links: list[Link]


class ProcessDescription(ProcessSummary):
    inputs: dict[str, Any]
    outputs: dict[str, Any]


class ProcessList(BaseModel):
    processes: list[ProcessSummary]
    links: list[Link]


class ExecuteRequest(BaseModel):
    """Part 1 Core execute body.

    `inputs` is free-form by design -- the standard says its keys are whatever
    the process description declares, so validating the shape here would mean
    duplicating the description. The values are checked by `submit_job`, which
    is the same code path the native API uses.
    """

    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    response: str = "document"


class StatusInfo(BaseModel):
    jobID: str
    processID: str
    type: str = "process"
    status: str
    message: str | None = None
    progress: int | None = None
    created: Any = None
    started: Any = None
    finished: Any = None
    links: list[Link] = Field(default_factory=list)


class JobList(BaseModel):
    jobs: list[StatusInfo]
    links: list[Link]


# ------------------------------------------------------------------ discovery

def _process_links(name: str) -> list[Link]:
    return [
        Link(href=f"/ogc/processes/{name}", rel="self",
             type="application/json", title=f"{name} process description"),
        Link(href=f"/ogc/processes/{name}/execution", rel="http://www.opengis.net/def/rel/ogc/1.0/execute",
             type="application/json", title=f"Execute {name}"),
    ]


def _summary(spec) -> ProcessSummary:
    return ProcessSummary(
        id=spec.name,
        title=spec.name.upper(),
        description=spec.description,
        version=__version__,
        # Async only -- see the module docstring.
        jobControlOptions=["async-execute"],
        outputTransmission=["reference"],
        links=_process_links(spec.name),
    )


#: The AOI schema, shared by every process. A GeoJSON Polygon, which is what
#: `submit_job` accepts; naming the format lets a client validate before
#: sending rather than learning from a 400.
_AOI_SCHEMA = {
    "title": "Area of interest",
    "description": (
        f"GeoJSON Polygon in EPSG:4326. Maximum {schemas.MAX_AOI_KM2} km² "
        "(PLAN.md 8), and it must fall inside a single scene."
    ),
    "minOccurs": 1,
    "maxOccurs": 1,
    "schema": {
        "type": "object",
        "format": "geojson-geometry",
        "required": ["type", "coordinates"],
        "properties": {
            "type": {"type": "string", "enum": ["Polygon"]},
            "coordinates": {"type": "array"},
        },
    },
}


#: The two sides of a change job, in chronological order.
_SIDES = ("earlier", "later")


def _side_output_id(side: str) -> str:
    """The declared output identifier for one side of a change job.

    Deliberately **not** the storage `output_type`. A per-date row is written as
    `earlier_ndvi` / `later_ndbi`, because the index is a run parameter — but a
    process description is static, written before any job exists, so it cannot
    name the index. It has to advertise a stable identifier.

    This function exists so the description and the results document cannot
    drift: they were written separately and did, the description advertising
    `earlier_index` while results returned `earlier_ndvi`. A client that reads
    the description to learn what outputs to expect — the only reason the
    description exists — found neither of the ids it was promised. The specific
    index stays discoverable through the result's `title`.
    """
    return f"{side}_index"


def output_id_for(process: str, output_type: str) -> str:
    """Map a stored `outputs.output_type` to its declared output identifier.

    The inverse direction of `_side_output_id`, and the single place that
    knows the mapping. `..._raster` is named for the process that produced it
    (`index_raster` from an `ndvi` job is just `ndvi`), and the per-date
    rasters collapse to the side identifiers the description declares.
    """
    if output_type.endswith("_raster"):
        return process
    for side in _SIDES:
        if output_type.startswith(f"{side}_"):
            return _side_output_id(side)
    return output_type


def _describe(spec) -> ProcessDescription:
    """The full description, including input and output schemas."""
    inputs: dict[str, Any] = {
        "aoi": _AOI_SCHEMA,
        "scene_ids": {
            "title": "Scene identifiers",
            "description": (
                f"Exactly {spec.scene_count} Sentinel-2 scene "
                f"{'id' if spec.scene_count == 1 else 'ids'}. "
                + ("Order does not matter: a change job is sorted "
                   "chronologically server-side, so a pair given either way "
                   "round produces the same sign."
                   if spec.scene_count > 1 else
                   "Find one with POST /api/v1/scenes/search.")
            ),
            "minOccurs": spec.scene_count,
            "maxOccurs": spec.scene_count,
            "schema": {"type": "array", "items": {"type": "string"},
                       "minItems": spec.scene_count, "maxItems": spec.scene_count},
        },
    }

    if spec.name == "change":
        inputs["index"] = {
            "title": "Index to difference",
            "description": "Applied identically to both dates (PLAN.md 5.4.4).",
            "minOccurs": 0,
            "maxOccurs": 1,
            "schema": {"type": "string",
                       "enum": sorted(processes.CHANGEABLE_INDICES),
                       "default": processes.DEFAULT_CHANGE_INDEX},
        }
    elif spec.name in {"ndvi", "ndwi", "ndbi"}:
        inputs["mask_snow"] = {
            "title": "Mask snow and ice",
            "description": "Adds SCL class 11 to the cloud mask.",
            "minOccurs": 0,
            "maxOccurs": 1,
            "schema": {"type": "boolean", "default": True},
        }

    outputs = {
        spec.name: {
            "title": f"{spec.name.upper()} raster",
            "description": (
                "Cloud-Optimized GeoTIFF, float32, in the scene's UTM zone. "
                "Carries its provenance in GeoTIFF tags: source scenes, "
                "processing baselines, and the valid-pixel fraction after "
                "cloud masking."
            ),
            "schema": {"type": "string", "format": "binary",
                       "contentMediaType": COG_MEDIA_TYPE},
        }
    }
    if spec.name == "change":
        # The two sides of the difference are published too, and a client that
        # only reads the description should know they exist.
        for side in _SIDES:
            outputs[_side_output_id(side)] = {
                "title": f"{side.capitalize()} date index raster",
                "description": (
                    f"The {side} of the two dates, as its own COG. A difference "
                    "raster cannot be un-differenced, so both sides are kept."
                ),
                "schema": {"type": "string", "format": "binary",
                           "contentMediaType": COG_MEDIA_TYPE},
            }

    summary = _summary(spec)
    return ProcessDescription(**summary.model_dump(), inputs=inputs, outputs=outputs)


@router.get("/ogc", response_model=LandingPage, summary="OGC API landing page")
def landing() -> LandingPage:
    return LandingPage(
        title="Bhoomi — OGC API Processes",
        description=(
            "On-demand Earth Observation processing. Execution is asynchronous; "
            "submit a process and poll the job."
        ),
        links=[
            Link(href="/ogc", rel="self", type="application/json",
                 title="This document"),
            Link(href="/ogc/conformance", rel="http://www.opengis.net/def/rel/ogc/1.0/conformance",
                 type="application/json", title="Conformance declaration"),
            Link(href="/ogc/processes", rel="http://www.opengis.net/def/rel/ogc/1.0/processes",
                 type="application/json", title="Processes offered"),
            Link(href="/ogc/jobs", rel="http://www.opengis.net/def/rel/ogc/1.0/job-list",
                 type="application/json", title="Jobs"),
            Link(href="/openapi.json", rel="service-desc",
                 type="application/vnd.oai.openapi+json;version=3.0",
                 title="OpenAPI definition"),
        ],
    )


#: Both paths serve the same declaration, and the reason is a compliance bug
#: this had for real. The landing page is at `/ogc`, which makes `/ogc` the API
#: root; OGC API - Common puts the conformance declaration at `{root}/conformance`,
#: so the spec-correct path is `/ogc/conformance`. It was only ever served at
#: `/conformance`. A client that followed the landing page's link worked, but a
#: validator -- or anyone applying the standard's own path rule to the root they
#: were given -- got a 404 from an API advertising Core conformance.
#:
#: `/ogc/conformance` is now the canonical one and the landing page points there.
#: `/conformance` stays because it was published, `examples/ogc_client.py` and
#: docs/api.md used it, and removing a working URL to fix an unreachable one
#: would trade this bug for a worse one.
_CONFORMANCE_PATHS = ("/ogc/conformance", "/conformance")


@router.get(_CONFORMANCE_PATHS[0], response_model=Conformance,
            summary="Conformance declaration")
@router.get(_CONFORMANCE_PATHS[1], response_model=Conformance,
            summary="Conformance declaration (legacy path)", include_in_schema=False)
def conformance() -> Conformance:
    return Conformance(conformsTo=CONFORMANCE)


@router.get("/ogc/processes", response_model=ProcessList,
            summary="Processes offered")
def list_processes() -> ProcessList:
    return ProcessList(
        processes=[_summary(processes.get(name)) for name in processes.names()],
        links=[Link(href="/ogc/processes", rel="self", type="application/json")],
    )


@router.get("/ogc/processes/{process_id}", response_model=ProcessDescription,
            summary="Process description")
def describe_process(process_id: str) -> ProcessDescription:
    spec = processes.get(process_id)
    if spec is None:
        raise errors.unknown_process(process_id, processes.names())
    return _describe(spec)


# ------------------------------------------------------------------ execution

@router.post("/ogc/processes/{process_id}/execution", response_model=StatusInfo,
             status_code=201, summary="Execute a process")
def execute(
    process_id: str,
    body: ExecuteRequest,
    http_request: Request,
    response: Response,
    jobs: JobStore = Depends(get_job_store),
    store: SceneStore = Depends(get_scene_store),
    catalogue: Catalogue = Depends(get_catalogue),
) -> StatusInfo:
    """Submit a job in OGC clothing.

    The `inputs` object is unpacked into the same arguments `/api/v1/jobs`
    passes, and nothing else happens here -- so every limit and every error
    message is identical between the two entry points.
    """
    spec = processes.get(process_id)
    if spec is None:
        raise errors.unknown_process(process_id, processes.names())

    inputs = dict(body.inputs)
    aoi = inputs.pop("aoi", None)
    scene_ids = inputs.pop("scene_ids", None)

    if not isinstance(aoi, dict):
        raise errors.missing_input(process_id, "aoi", "a GeoJSON Polygon object")
    if not isinstance(scene_ids, list) or not all(isinstance(s, str) for s in scene_ids):
        raise errors.missing_input(process_id, "scene_ids", "an array of scene id strings")

    # Whatever is left is a process parameter. Unknown keys are not rejected:
    # the process description says what is understood, and `submit_job`
    # validates the ones that matter. Refusing extras would break a client
    # that sends a field a later version added.
    job = submit_job(
        process=spec.name, aoi=aoi, scene_ids=scene_ids, parameters=inputs,
        http_request=http_request, jobs=jobs, store=store, catalogue=catalogue,
    )

    location = f"/ogc/jobs/{job.id}"
    response.headers["Location"] = location
    if "respond-async" in http_request.headers.get("prefer", "").lower():
        response.headers["Preference-Applied"] = "respond-async"
    return _status_info(job)


# ----------------------------------------------------------------------- jobs

def _status_info(job: Job) -> StatusInfo:
    return StatusInfo(
        jobID=str(job.id),
        processID=job.process,
        status=_STATUS[job.status],
        message=job.error_message or job.message,
        progress=job.progress,
        created=job.created_at,
        started=job.started_at,
        finished=job.completed_at,
        links=[
            Link(href=f"/ogc/jobs/{job.id}", rel="self",
                 type="application/json", title="Job status"),
            Link(href=f"/ogc/jobs/{job.id}/results",
                 rel="http://www.opengis.net/def/rel/ogc/1.0/results",
                 type="application/json", title="Job results"),
        ],
    )


def _load(job_id: str, jobs: JobStore) -> Job:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise errors.job_not_found(job_id) from None
    job = jobs.get(job_id)
    if job is None:
        raise errors.job_not_found(job_id)
    return job


@router.get("/ogc/jobs", response_model=JobList, summary="Job list")
def list_jobs(limit: int = Query(20, ge=1, le=100),
              offset: int = Query(0, ge=0),
              jobs: JobStore = Depends(get_job_store)) -> JobList:
    found, total = jobs.recent(limit=limit, offset=offset)
    links = [Link(href=f"/ogc/jobs?limit={limit}&offset={offset}", rel="self",
                  type="application/json")]
    if offset + limit < total:
        links.append(Link(href=f"/ogc/jobs?limit={limit}&offset={offset + limit}",
                          rel="next", type="application/json"))
    if offset > 0:
        links.append(Link(href=f"/ogc/jobs?limit={limit}&offset={max(0, offset - limit)}",
                          rel="prev", type="application/json"))
    return JobList(jobs=[_status_info(j) for j in found], links=links)


@router.get("/ogc/jobs/{job_id}", response_model=StatusInfo,
            summary="Job status")
def job_status(job_id: str, jobs: JobStore = Depends(get_job_store)) -> StatusInfo:
    return _status_info(_load(job_id, jobs))


@router.get("/ogc/jobs/{job_id}/results", summary="Job results")
def job_results(job_id: str, request: Request,
                jobs: JobStore = Depends(get_job_store)) -> dict:
    """Results as a Part 1 Core document: output id to value.

    Rasters are returned **by reference** -- an `href` rather than inline
    bytes -- which is what `outputTransmission: ["reference"]` promises in the
    process description. A 20 MB GeoTIFF base64'd into JSON would be neither
    useful nor loadable by the GIS clients this exists for.

    The hrefs are absolute. A relative one is legal in the standard but makes
    the document useless the moment it is saved to a file or handed to a tool
    that did not perform the request.
    """
    job = _load(job_id, jobs)

    if not job.is_terminal:
        raise errors.result_not_ready(str(job.id), job.status.value, job.progress)
    if job.status is not JobStatus.COMPLETED:
        raise errors.job_failed(str(job.id), job.status.value, job.error_message)

    base = str(request.base_url).rstrip("/")
    results: dict[str, Any] = {}
    for output in jobs.outputs_for(job.id):
        # The identifier the process description declared, never the storage
        # `output_type` — see `output_id_for`. Returning an id that was never
        # advertised is the defect this replaced.
        key = output_id_for(job.process, output.output_type)
        variant = ("?output=earlier" if output.output_type.startswith("earlier_")
                   else "?output=later" if output.output_type.startswith("later_")
                   else "")
        results[key] = {
            "href": f"{base}/api/v1/jobs/{job.id}/download{variant}",
            "type": COG_MEDIA_TYPE,
            "title": output.output_type,
        }
    return results
