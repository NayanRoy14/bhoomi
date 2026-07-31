# API

Every example below is a real request against a running Bhoomi, with the response pasted as
returned. The interactive OpenAPI spec is at `/docs`; this document is the prose half — what the
endpoints are *for*, which ones you actually need, and what the errors mean.

**Base URL** in development: `http://localhost:8000`.

---

## Two interfaces, one queue

| | For | Style |
|---|---|---|
| `/api/v1/*` | The web interface, and anything that wants Bhoomi's own vocabulary | JSON, `202 Accepted` |
| `/ogc/*` | Any [OGC API – Processes](https://ogcapi.ogc.org/processes/) client — QGIS, a script, `owslib` | The standard's vocabulary, `201 Created` |

They are **not two systems**. Both call the same submission code and read the same tables; a job
created through one is visible and identical through the other. The OGC layer adds request and
response shapes and nothing else.

Use `/api/v1` if you are writing something Bhoomi-specific. Use `/ogc` if you want code that would
work against another processing server too — see [`examples/ogc_client.py`](../examples/ogc_client.py),
which does exactly that in the standard library alone.

---

## Conventions

**Everything is JSON** except the raster download, which is `image/tiff`.

**Errors carry a code, a message, and the numbers.** The message is written to be read by a person
and to say what to do next:

```json
{
  "detail": {
    "code": "wrong_scene_count",
    "message": "Process 'change' requires exactly 2 scenes; got 1.",
    "expected": 2,
    "got": 1
  }
}
```

Match on `code`, show `message`. The extra fields differ per error and are there so a client can
render its own wording without parsing prose.

**Geometry is GeoJSON in EPSG:4326**, Polygon only. Coordinates are `[longitude, latitude]`.

**Times are UTC ISO 8601.** Dates in search requests are plain `YYYY-MM-DD`.

**Processing is asynchronous.** Nothing computes a raster inside your request — you submit, then
poll. A 500 km² job takes tens of seconds and can take minutes.

---

## Quick start

Four calls: find a scene, submit, poll, download.

```bash
BASE=http://localhost:8000
AOI='{"type":"Polygon","coordinates":[[[88.44,22.59],[88.49,22.59],[88.49,22.63],[88.44,22.63],[88.44,22.59]]]}'

# 1. find a scene that fully covers the area
curl -s -X POST $BASE/api/v1/scenes/search -H 'Content-Type: application/json' \
  -d "{\"aoi\":$AOI,\"start_date\":\"2026-02-20\",\"end_date\":\"2026-03-10\",\"max_cloud\":20}"

# 2. submit
curl -s -X POST $BASE/api/v1/jobs -H 'Content-Type: application/json' \
  -d "{\"process\":\"ndvi\",\"scene_ids\":[\"S2B_45QXF_20260304_0_L2A\"],\"aoi\":$AOI}"

# 3. poll until status is terminal
curl -s $BASE/api/v1/jobs/{job_id}

# 4. the numbers, then the raster
curl -s $BASE/api/v1/jobs/{job_id}/result
curl -sL $BASE/api/v1/jobs/{job_id}/download -o ndvi.tif
```

---

## `GET /health`

Liveness and configuration. Never rate-limited — a health check returning 429 reads as an outage,
and orchestrators restart containers over it.

```json
{"status": "ok", "version": "0.1.0", "catalogue": "earth-search", "queue_depth": 0, "workers": 2}
```

`queue_depth` and `workers` are `null` when no queue is configured, which is a valid deployment
rather than an error. `workers` counts live heartbeat keys, so it can read 0 for a second or two
after a worker starts.

**It does not call the catalogue.** A health check that depends on a third party reports their
outage as yours.

---

## `POST /api/v1/scenes/search`

Find Sentinel-2 scenes intersecting an area.

```json
{
  "aoi": {"type": "Polygon", "coordinates": [[[88.44, 22.59], "..."]]},
  "start_date": "2026-02-20",
  "end_date": "2026-03-10",
  "max_cloud": 20
}
```

| field | required | default | note |
|---|---|---|---|
| `aoi` | yes | — | GeoJSON Polygon, ≤ 500 km² |
| `start_date`, `end_date` | no | — | ≤ 366 days apart |
| `max_cloud` | no | — | percent |
| `collection` | no | `sentinel-2-l2a` | |
| `limit` | no | `50` | |
| `deduplicate` | no | `true` | Collapses reprocessings of one acquisition, keeping the newest baseline |

Response — 4 scenes over a 22.8 km² AOI:

```json
{
  "count": 4,
  "aoi_area_km2": 22.8,
  "scenes": [
    {
      "id": "S2B_45QXF_20260304_0_L2A",
      "collection": "sentinel-2-l2a",
      "satellite": "sentinel-2b",
      "acquired_at": "2026-03-04T04:52:13.346000Z",
      "cloud_cover": 0.000415,
      "processing_baseline": "05.12",
      "bbox": [87.972328, 22.505778, 89.054486, 23.507467],
      "geometry": {"type": "Polygon", "coordinates": ["..."]},
      "thumbnail": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/…/preview.jpg",
      "aoi_coverage": 1.0,
      "available_processes": ["ndbi", "ndvi", "ndwi"]
    }
  ]
}
```

`thumbnail` is the provider's own preview JPEG, or `null`. Handy for a scene list; it is not a
Bhoomi product and carries no provenance.

**`aoi_coverage` is the field that matters.** Bhoomi processes one scene at a time, so a job needs
a scene with coverage ≥ 0.999. Partial matches are *returned rather than hidden*, so the interface
can show why a scene cannot be used instead of results silently disappearing.

**`available_processes`** is derived from the bands the scene actually carries — a scene without
SWIR cannot do NDBI, and saying so here is cheaper than a job that fails.

> **Scene discovery is not part of OGC API – Processes.** That standard describes computation, not
> catalogues. This route stays on `/api/v1` for both interfaces; an OGC client uses it to find an
> id and then executes through `/ogc`.

---

## `POST /api/v1/jobs`

```json
{
  "process": "ndvi",
  "scene_ids": ["S2B_45QXF_20260304_0_L2A"],
  "aoi": {"type": "Polygon", "coordinates": ["..."]},
  "parameters": {}
}
```

Processes: `ndvi`, `ndwi`, `ndbi` (one scene), `change` (two scenes), and `fake` — which sleeps ten
seconds and produces no raster, for checking the queue without touching the network.

| parameter | applies to | default |
|---|---|---|
| `index` | `change` | `"ndvi"`; one of `ndvi`, `ndwi`, `ndbi` |
| `mask_snow` | the indices | `true` — adds SCL class 11 to the cloud mask |

**`202 Accepted`**, with a `Location` header:

```json
{
  "job_id": "ef875dba-e56b-43b0-9b2a-ae5f632cd8d4",
  "status": "queued",
  "position_in_queue": 0,
  "estimated_seconds": 15,
  "links": [
    {"rel": "status", "href": "/api/v1/jobs/ef875dba-…", "type": "application/json"},
    {"rel": "result", "href": "/api/v1/jobs/ef875dba-…/result", "type": "application/json"}
  ]
}
```

**Scene order does not matter for `change`.** The pair is sorted chronologically server-side, so a
pair given either way round produces the same sign rather than a flipped one.

Everything is validated *before* the job is created, so a rejection is immediate rather than
something you poll for: AOI area, scene count, duplicate scenes, the index name, and whether the
AOI actually falls inside the scene.

---

## `GET /api/v1/jobs/{job_id}`

Poll at 2 s while active. Job reads have their own generous budget — see [Limits](#limits).

```json
{
  "job_id": "ef875dba-e56b-43b0-9b2a-ae5f632cd8d4",
  "process": "ndvi",
  "status": "completed",
  "progress": 100,
  "message": "Done",
  "error_message": null,
  "created_at": "2026-07-31T12:45:18.640719Z",
  "started_at": "2026-07-31T12:45:18.737330Z",
  "completed_at": "2026-07-31T12:45:26.604126Z"
}
```

| status | progress | terminal |
|---|---|---|
| `queued` | 0 | |
| `searching` | 10 | |
| `reading` | 30 | |
| `processing` | 60 | |
| `writing_cog` | 85 | |
| `completed` | 100 | ✓ |
| `failed` | *kept* | ✓ |
| `timed_out` | *kept* | ✓ |
| `cancelled` | *kept* | ✓ |

Failure states keep the progress they reached, which says more than resetting to zero.
`error_message` is always safe to display — the traceback is stored separately and never served.

---

## `GET /api/v1/jobs/{job_id}/result`

`404` with `code: result_not_ready` while the job is still running. `409` once it has finished
unsuccessfully — distinct so a polling client stops rather than retrying a 404 that will never
change.

```json
{
  "job_id": "ef875dba-e56b-43b0-9b2a-ae5f632cd8d4",
  "outputs": [
    {
      "type": "index_raster",
      "cog": "http://localhost:8000/api/v1/jobs/ef875dba-…/download",
      "download": "/api/v1/jobs/ef875dba-…/download",
      "bounds": [88.43954798, 22.589465062, 88.490460164, 22.630474743],
      "crs": "EPSG:32645",
      "resolution_m": 10.0,
      "valid_fraction": 0.9998627,
      "stats": {
        "min": -0.4450261890888214,
        "max": 0.9000203013420105,
        "mean": 0.35257935523986816,
        "median": 0.3595658242702484,
        "stddev": 0.19866250455379486
      },
      "expires_at": "2026-08-30T12:45:26.591941Z",
      "tiles": "http://localhost:8001/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=…&rescale=-1.0,1.0&colormap_name=rdylgn",
      "warnings": []
    }
  ]
}
```

**Read `warnings` before `stats`.** They change how the numbers should be read, so reading them
second is too late. They are strings meant for display, and they are usually empty. What appears
there:

- the scene has no SCL band, so **cloud and shadow are not masked**;
- the two scenes have different processing baselines, so part of a change is Sen2Cor version drift;
- the BOA offset could not be determined from pixels and metadata was used instead;
- fewer than half the AOI's pixels survived masking.

**`valid_fraction`** is on every output. A result that is 80 % cloud says so rather than rendering
as a mostly-empty raster.

**`tiles`** is an XYZ template, or `null` when no tile server is configured. Rescaled to a fixed
`[-1, 1]` rather than stretched per image — two dates of the same area would otherwise get
different scales, so comparing them visually would measure the stretch rather than the ground.

**A `change` job returns three outputs**, not one:

| `type` | what |
|---|---|
| `change_raster` | The difference, `later − earlier`. Carries `loss_fraction`, `gain_fraction`, `asymmetry`, `threshold` instead of a distribution |
| `earlier_<index>` | The earlier date's index raster |
| `later_<index>` | The later date's |

A difference cannot be un-differenced — +0.3 could be bare ground becoming scrub or forest
becoming denser forest — so both sides are kept. That is also what makes the before/after swipe
possible.

---

## `GET /api/v1/jobs/{job_id}/download`

The raster itself, `image/tiff`. Follow redirects (`curl -L`): the response is a 307 to object
storage when there is a public URL for it, and the bytes directly otherwise.

`?output=earlier` or `?output=later` selects one side of a change job. Omitted, you get the job's
primary output. Any other value is a `400` — the set is closed because the value reaches a storage
key.

`410 output_missing` means it existed and is gone: past its 30-day retention. Submit the job again.

Every GeoTIFF carries its own provenance in tags, so a file that has been moved around still says
where it came from:

```
BHOOMI_PROCESS              ndvi
BHOOMI_SOURCE_SCENES        S2B_45QXF_20260304_0_L2A
BHOOMI_BOA_OFFSET_PRESENT   False
BHOOMI_BOA_OFFSET_BASIS     pixels
BHOOMI_VALID_FRACTION       0.9999
```

`BHOOMI_BOA_OFFSET_BASIS` is worth reading: `pixels` means the reflectance convention was proved
from the data, `metadata` means the pixels could not decide and a metadata field was trusted
instead. See [`limitations.md`](limitations.md).

---

## OGC API – Processes

Part 1: Core, asynchronous execution. Start at `/ogc` and follow links, or read
`/conformance` first.

| endpoint | |
|---|---|
| `GET /ogc` | Landing page, links onward |
| `GET /conformance` | Which classes are implemented |
| `GET /ogc/processes` | Process list |
| `GET /ogc/processes/{processID}` | Full input and output schemas |
| `POST /ogc/processes/{processID}/execution` | Execute → `201` + `Location` |
| `GET /ogc/jobs` | Job list, paged with `limit` and `offset` |
| `GET /ogc/jobs/{jobID}` | Status |
| `GET /ogc/jobs/{jobID}/results` | Results document |

**Execution is always asynchronous.** There is no synchronous mode to offer — a job reading bands
over HTTP can take minutes — so a `201` comes back whether or not you send `Prefer: respond-async`.
Send it anyway and it is echoed in `Preference-Applied`.

```bash
curl -s -X POST $BASE/ogc/processes/ndvi/execution \
  -H 'Content-Type: application/json' -H 'Prefer: respond-async' \
  -d '{"inputs": {"aoi": {...}, "scene_ids": ["S2B_45QXF_20260304_0_L2A"]}}'
```

Everything in `inputs` other than `aoi` and `scene_ids` is passed through as a process parameter.
Unknown keys are **not** rejected — the process description says what is understood, and refusing
extras would break a client sending a field a later version added.

Status uses the standard's vocabulary, not Bhoomi's:

```json
{
  "jobID": "ef875dba-e56b-43b0-9b2a-ae5f632cd8d4",
  "processID": "ndvi",
  "type": "process",
  "status": "successful",
  "message": "Done",
  "progress": 100,
  "created": "2026-07-31T12:45:18.640719Z",
  "started": "2026-07-31T12:45:18.737330Z",
  "finished": "2026-07-31T12:45:26.604126Z",
  "links": [{"href": "/ogc/jobs/ef875dba-…", "rel": "self", "type": "application/json"}]
}
```

| Bhoomi | OGC |
|---|---|
| `queued` | `accepted` |
| `searching`, `reading`, `processing`, `writing_cog` | `running` |
| `completed` | `successful` |
| `failed`, `timed_out` | `failed` |
| `cancelled` | `dismissed` |

A timeout maps to `failed`, not `dismissed`: `dismissed` means the *client* asked for the job to
stop, and a job killed at the time limit was nobody's decision but the server's.

Results are returned **by reference**, matching `outputTransmission: ["reference"]` — a 20 MB
GeoTIFF base64'd into JSON would be neither useful nor loadable by the tools this exists for:

```json
{
  "ndvi": {
    "href": "http://localhost:8000/api/v1/jobs/ef875dba-…/download",
    "type": "image/tiff; application=geotiff; profile=cloud-optimized",
    "title": "index_raster"
  }
}
```

**Declared conformance classes** are only those actually implemented — a client trusts this list
and will call what it advertises:

```
…/ogcapi-processes-1/1.0/conf/core
…/ogcapi-processes-1/1.0/conf/ogc-process-description
…/ogcapi-processes-1/1.0/conf/json
…/ogcapi-processes-1/1.0/conf/oas30
…/ogcapi-processes-1/1.0/conf/job-list
…/ogcapi-common-1/1.0/conf/core
…/ogcapi-common-1/1.0/conf/json
```

Deliberately absent: `sync-execute` (no synchronous mode), `dismiss` (`DELETE /ogc/jobs/{jobID}` does not
exist, and telling a client it can cancel a runaway job when it cannot is worse than silence), and
`callback`.

---

## Errors

| status | code | meaning |
|---|---|---|
| 400 | `aoi_too_large` | Over 500 km². Carries `area_km2` and `limit_km2` |
| 400 | `aoi_spans_scenes` | The AOI is not inside one scene. Carries `coverage` and `scene_id` |
| 400 | `date_range_too_long` | Over 366 days in a single search |
| 400 | `invalid_date_range` | `start_date` after `end_date` |
| 400 | `unknown_process` | Carries `available` |
| 400 | `wrong_scene_count` | Carries `expected` and `got` |
| 400 | `unknown_index` | `parameters.index` is not an index |
| 400 | `duplicate_scenes` | The same scene twice; the difference would be zero everywhere |
| 400 | `missing_input` | An OGC execute body omitted `aoi` or `scene_ids` |
| 400 | `unknown_output` | `?output=` named something that is not served |
| 404 | `job_not_found` | Including a malformed id — "no such job" is truer than a schema complaint |
| 404 | `result_not_ready` | Still running. Carries `status` and `progress` |
| 404 | `scene_not_found` | No such scene in the catalogue |
| 409 | `job_did_not_complete` | Finished unsuccessfully. Stop polling |
| 410 | `output_missing` | Existed, now past retention. Retrying will not help |
| 429 | `rate_limited` | Carries `retry_after` and a `Retry-After` header |
| 429 | `too_many_active_jobs` | Carries `active`, `limit`, `scope` (`client` or `global`) |
| 502 | `catalogue_unavailable` | Upstream STAC failed — never surfaced as a 500 |
| 503 | `jobs_unavailable` | No database or no queue. The request was fine; the deployment is incomplete. Scene search still works |

**Honour `Retry-After`.** It says exactly how long to wait, and a client that treats 429 as fatal
is a badly-behaved client.

---

## Limits

| Limit | Value | Why |
|---|---|---|
| AOI area | 500 km² | ~5 M pixels at 10 m across two bands |
| Date range per search | 366 days | Bounds catalogue query cost |
| Scenes per search | 50 | Response size |
| Searches | 120 / hour / IP | |
| Job submissions | 20 / hour / IP | |
| Job reads | 1200 / hour / IP | Its own budget, so polling never starves search |
| Concurrent jobs | 2 global, 1 per IP | One VPS. The rest queue |
| Job timeout | 10 minutes | Hard kill |
| Output size | 200 MB | |
| Output retention | 30 days | |

Comparing two dates **years** apart is fine — the 366-day cap is per *search*, not on the gap
between two scenes. Run two searches and submit one id from each.

Rate limits are keyed on client IP. `X-Forwarded-For` is ignored unless `BHOOMI_TRUSTED_PROXY_HOPS`
says a proxy of ours set it; honouring it blindly would let any caller reset their own limit with
one header.

---

## What the API will not do

- **Mosaic across scenes.** An AOI crossing a scene boundary is refused rather than silently
  stitched.
- **Authenticate anyone.** Rate limits are a throttle, not an identity, and the OGC job list is
  public because of it.
- **Cancel a job.** There is no `DELETE`; the `dismiss` conformance class is not declared.
- **Execute synchronously.** See above.
- **Serve Bhoonidhi data.** Not yet — see [`limitations.md`](limitations.md).
