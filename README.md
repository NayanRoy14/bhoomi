# Bhoomi

**On-demand Earth Observation processing for Indian and open satellite data.**

Bhoomi does not display pre-made map layers. It performs geospatial computation on demand and
returns a standards-compliant raster product that another GIS tool can consume.

### ▶ Try it: **[bhoomi-site.onrender.com](https://bhoomi-site.onrender.com)** · API at **[bhoomi-api.onrender.com](https://bhoomi-api.onrender.com/docs)**

Draw an area, search Sentinel-2, run an index, download the GeoTIFF. No sign-up.

> ⚠️ **Early development, and deployed on a free tier** — which is visible in how it behaves.
> The API **sleeps after 15 minutes**, so the first request takes about 40 seconds; it has not
> crashed. Downloads live on an **ephemeral disk**, so a result link stops working after a
> restart — download it while you are there. There is **no tile server**, so the map preview is
> absent and the UI says so rather than offering a dead link.
>
> None of those are properties of Bhoomi; they are what a deployment with no payment method
> costs. [`docs/deploy-render.md`](docs/deploy-render.md) explains each and what fixes it, and
> [`docs/deploy.md`](docs/deploy.md) is the deployment without the compromises.
> See [Status](#status). Development runs August 2026 – March 2027.

![NDVI change over New Town / Rajarhat, Kolkata, 2020 to 2026](docs/images/kolkata_change.png)

---

## What problem does it solve?

Producing a derived product from satellite imagery normally means: search a catalogue → obtain
scenes → open them in desktop GIS → clip → select bands → compute → export → publish. Seven
manual steps, desktop-bound, not reproducible, not scriptable, not shareable as a live result.

Bhoomi collapses that into one request, over an API, with a URL as the output.

## How is it different from a GIS viewer?

A viewer serves pixels somebody already computed. Bhoomi reads only the bands and the window a
request needs — over HTTP range requests, without downloading whole scenes — masks cloud,
harmonizes reflectance, computes the index, and writes a Cloud-Optimized GeoTIFF that QGIS,
TiTiler or any STAC-aware client can read.

The difference matters. Working through the Kolkata example below, four successive methods gave
four different answers, and **no viewer would have caught any of the errors**:

| Method | Claimed vegetation loss |
|---|---|
| SCL class counts | −66 % |
| NDVI, two dates | −16 % relative |
| NDVI + NDBI, two dates | "85.9 % of loss is construction-like" |
| **NDVI, 7-year recovery test** | **16.6 % of loss is permanent = 1.44 % of the AOI** |

Each step cut the headline by roughly 4×, and each cut came from a control the previous step
lacked. The last number is small, and it is the only one that survives scrutiny.

[`docs/limitations.md`](docs/limitations.md) carries the whole chain, what each control caught,
and where every number came from — including which have been independently re-run and which have
not.

## Architecture

```
Next.js + MapLibre  ->  FastAPI  ->  Redis + RQ  ->  Python worker
                            |                         (rasterio, numpy)
                            v                              |
                       PostGIS   <---------------------    v
                                                    Cloud-Optimized GeoTIFF
                                                           |
                                                       TiTiler -> XYZ tiles
```

Raster work never happens inside an HTTP request. Jobs are queued, and the same queue is what
makes an OGC API – Processes async execution model natural rather than bolted on.

[**`docs/processing.md`**](docs/processing.md) explains the science — what happens to the pixels
and why each step is there. [**`docs/api.md`**](docs/api.md) is the API guide — every example a real request and its real
response — and [**`docs/architecture.md`**](docs/architecture.md) has the rest: the dependency rule and why it is
worth the discipline, the request lifecycle, the job state machine, what happens inside a job, and
the decisions that shaped each — as diagrams that render here and diff like text.

## Which standards?

- **STAC** — catalogue search (Element84 Earth Search, `sentinel-2-l2a`)
- **Cloud-Optimized GeoTIFF** — all outputs, validated before a job is marked complete
- **OGC API – Processes Part 1: Core** — implemented at `/ogc`, async execution. The acceptance
  test is `examples/ogc_client.py`: standard library only, it constructs exactly one URL — the
  landing page — and reaches conformance, the process list, the input schema, the job and the
  GeoTIFF by following link relations from there, with no browser involved. Scene search is the
  one step it does against a Bhoomi route, because finding a scene id is STAC's job and not
  something Processes covers; the script says so where it happens

## Related work

Bhoomi is not the first server-side EO processing stack, and the honest framing is that
most of its architecture is a well-trodden path. What follows is where it sits.

| Project | What it is | How Bhoomi differs |
|---|---|---|
| [**eoAPI**](https://eoapi.dev/) (Development Seed) | The closest architectural neighbour: pgSTAC + stac-fastapi + titiler-pgstac bundled and version-pinned. Does band math — including NDVI — as a tile-time `expression`. | eoAPI computes *per tile request*, for viewing. Bhoomi computes **once, as a job**, and the artifact is a persisted, validated COG with provenance tags. Different output contract: a URL you can hand to QGIS, not a tile pyramid. eoAPI's own docs say its value is "integration, not innovation" — that is fair and it is a better choice than Bhoomi for serving a catalogue. |
| [**openEO by TiTiler**](https://github.com/sentinel-hub/titiler-openeo) (Sinergise + Development Seed, 2025) | A light openEO backend on TiTiler, deployed in the Copernicus Data Space for demonstration. | Synchronous, visualisation-first, openEO process graphs. Bhoomi is async-first and speaks OGC API – Processes. |
| [**openEO**](https://openeo.org/) | The standard for federated EO processing; virtual data cubes, R/Python/JS clients, multiple backends. | Much larger in scope. Bhoomi deliberately implements **OGC API – Processes Part 1: Core** instead — one standard, fully, over a process graph language partially. |
| [**pygeoapi**](https://docs.pygeoapi.io/) | Python OGC API server with an OGC API – Processes plugin architecture and a pluggable job manager. | If the goal were only to expose processes, this is the off-the-shelf answer. Bhoomi implements Part 1 Core directly because the job/queue model came first and the standard was fitted to it — see the acceptance test in `examples/ogc_client.py`. |
| [**Open Data Cube**](https://www.opendatacube.org/) / `odc-stac`, [**gdalcubes**](https://github.com/appelmar/gdalcubes) | Index-and-load datacube frameworks behind Digital Earth Australia/Africa. | Library-level, xarray/Dask-shaped, no HTTP job service. Bhoomi is a service with a state machine. |
| [**Sentinel Hub**](https://www.sentinel-hub.com/develop/api/) | Commercial Processing / Batch / Statistical APIs. | Closed and paid. Handles the reflectance offset for you, which is precisely the problem Bhoomi had to solve itself. |
| [**Bhuvan**](https://bhuvan.nrsc.gov.in/) / [**Bhoonidhi**](https://bhoonidhi.nrsc.gov.in/) (NRSC/ISRO) | India's geoportal and open-data dissemination hub. | Both are **portals** — visualise, browse, download. Neither exposes on-demand server-side computation over an API. This is the gap Bhoomi is aimed at. |

**Where Bhoomi has something the others do not.** Every stack above resolves the Sentinel-2
baseline-04.00 reflectance offset from *metadata* or by pre-harmonising a whole collection:
Earth Engine ships `HARMONIZED` collections, Sentinel Hub applies `harmonizeValues`,
[`stactools-sentinel2` reads `BOA_ADD_OFFSET`](https://github.com/stactools-packages/sentinel2/issues/44)
into the STAC raster extension, GRASS `i.sentinel.import` takes a flag, and the Planetary
Computer [leaves it to the user, in an issue still open](https://github.com/microsoft/PlanetaryComputer/issues/134).

Those all work when the metadata is trustworthy. On Element84 Earth Search — the catalogue
Bhoomi actually reads — **it measurably is not**: `earthsearch:boa_offset_applied` came back
`False` meaning *offset present* on one scene and *offset absent* on another, and
`raster:bands.offset` reported −0.1 regardless. So Bhoomi decides **from the pixels**, and,
more usefully, **declines to decide** when the pixels cannot support one, falling back to
metadata and attaching a warning to the output. Across 48 scenes spanning desert to delta it
misclassifies none.

A detector that returns "I don't know" is the part worth reusing. It is also the part that took
three wrong answers to reach — see [below](#a-note-on-the-hard-part).

## What data?

| Source | Status |
|---|---|
| **Sentinel-2 L2A** | Working. Anonymous COGs on AWS, read via HTTP range requests. |
| **NRSC Bhoonidhi** | Planned. Under Indian Space Policy 2023, data ≥ 5 m is free and open; no public API is documented, so this uses a download-and-stage path. |

## Status

| Component | State |
|---|---|
| `processing/` — the raster library | **Working** — verified against real Sentinel-2 data |
| `catalogue/` — STAC client | **Working** — search, scene lookup, deduplication |
| `pipeline.py` — composition layer | **Working** — AOI + dates in, COG out |
| `examples/` — worked Kolkata analyses | **Working** |
| FastAPI — `/health`, scene search, `/docs` | **Working** |
| Next.js + MapLibre — draw, search, analyse, download | **Working** |
| Docker Compose | **Working** — built and run; backend + postgres verified end to end |
| PostGIS scene caching + alembic | **Working** — write-through on search, 39 tests |
| Job queue — Redis + RQ, worker, state machine | **Working** |
| NDVI / NDWI / NDBI as jobs, COG out | **Working** — verified on live Sentinel-2 |
| TiTiler — results rendered on the map | **Working** — loopback only, see below |
| Object storage — Cloudflare R2 | **Implemented**, verified against MinIO; needs a real bucket |
| Two-date change detection | **Working** — with baseline and seasonality warnings |
| Before/after swipe | **Working** — a change job publishes both dates as well as the difference |
| CI — tests, migrations, harmonization gate | **Working** — 5 jobs, integration runs all 418 tests with no skips |
| OGC API – Processes Part 1: Core | **Working** — `examples/ogc_client.py` executes and downloads by following links from the landing page |
| Public deployment | **Live**, free tier — [site](https://bhoomi-site.onrender.com), [API docs](https://bhoomi-api.onrender.com/docs). Verified by driving the browser end to end and by `ogc_client.py`; see the note at the top for what the free tier costs. |

A real NDVI over New Town / Rajarhat, submitted to the deployed stack and finished in 11 s:

```
scene   S2C_45QXF_20260227_0_L2A     AOI ~25 km2
ndvi    median +0.332   range -0.295 .. +0.811   valid_fraction 0.99998
COG     EPSG:32645, 10 m, tiled, deflate, overviews, nodata declared -- 940 KB
```

The browser closes the loop: draw an area, search, pick a scene, pick a process, watch live
progress, see the result rendered on the map with a legend and an opacity slider, and download
the GeoTIFF. Change detection adds a second date picker, and warns *before* the job is spent if
the two scenes have different processing baselines or sit far apart in the year — both of which
put drift or phenology into what would read as land-use change.

Tiles are rescaled to a fixed [-1, 1] rather than stretched per image. A per-image stretch looks
better and means less — two dates of the same area would get different scales, so comparing them
visually would measure the stretch rather than the ground.

> ⚠️ **The tile server is bound to `127.0.0.1` on purpose.** While outputs live on a filesystem,
> TiTiler will open whatever path it is given, so a publicly reachable tile server is an
> arbitrary-file-read. Object storage removes this rather than mitigating it. Do not expose the
> tile server before that lands.

Outputs go to local disk by default and to object storage when `BHOOMI_S3_BUCKET` is set.
`cog_uri` is a URL either way. On local disk the worker and the API must share a filesystem;
object storage removes that constraint.

418 tests. 105 of them need Postgres, Redis or an S3-compatible store, and skip without:

```bash
docker run -d --rm --name bhoomi-test-pg -p 55432:5432 \
    -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=bhoomi_test postgis/postgis:16-3.4
docker run -d --rm --name bhoomi-test-redis -p 56379:6379 redis:7-alpine
docker run -d --rm --name bhoomi-test-minio -p 59000:9000 \
    -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
    quay.io/minio/minio server /data

BHOOMI_TEST_DATABASE_URL=postgresql://postgres:testpw@localhost:55432/bhoomi_test \
BHOOMI_TEST_REDIS_URL=redis://localhost:56379/1 \
BHOOMI_TEST_S3_ENDPOINT=http://localhost:59000 \
    python -m pytest
```

MinIO stands in for R2 there. That is not a compromise — R2 *is* the S3 API, which is why the
backend is `S3Storage` rather than `R2Storage`, and why changing provider costs an endpoint
rather than a rewrite. It does mean none of it proves anything about R2's own latency.

## Running it

The whole stack, from a clean clone:

```bash
cp .env.example .env
docker compose up --build
#  frontend  http://localhost:3000
#  API       http://localhost:8000/docs
#  tiles     http://localhost:8001      (loopback only -- see the warning above)
```

[**`docs/deploy.md`**](docs/deploy.md) is the production runbook — a public,
always-on deployment where jobs really run, free, on Oracle Cloud's Always Free tier plus
Cloudflare R2. It covers the three constraints that rule out the obvious hosts (the worker
cannot be serverless, Postgres must be PostGIS, Redis must support blocking reads), why the
worker belongs on the US west coast even though the audience is in India, and the fact that
**object storage is a precondition rather than an option** — `docker-compose.prod.yml` gives
the tile server no filesystem at all, which is what makes exposing it safe.

Or without Docker:

```bash
pip install -r requirements-dev.txt
python -m pytest

# API on :8000
uvicorn backend.api.main:app --reload

# frontend on :3000, in another shell
cd frontend && npm install && npm run dev
```

### The analyses

```bash
# Fetch the demo bands (reads only the AOI window from remote COGs)
python probes/clip_demo_aoi.py

# AOI + date range -> STAC search -> NDVI -> validated COG
python examples/search_and_process.py

# NDVI, NDBI, and the two-date change analysis
python examples/kolkata_change.py
python examples/kolkata_ndbi.py
```

The 7-year analysis needs its cache built first (~10 minutes of network):

```bash
python probes/cache_series.py
python examples/kolkata_timeseries.py
```

## Repository layout

```
backend/      FastAPI -- HTTP and nothing else
  api/routes/     health, scenes, jobs
  api/schemas.py  request/response models
  api/errors.py   messages that say what to do about it
  db/             scenes cache, jobs and outputs, alembic migrations
  queue/          RQ setup, the process registry, the worker entry point
  storage.py      where finished COGs live -- local disk, or any S3-compatible store
  tiles.py        TiTiler URL shape and the colour ramp per index
  resolve.py      scene id -> Scene, cache first, catalogue second
cache.py      per-scene measured DN floor -- JSON file, or the scenes table
frontend/     Next.js + MapLibre -- AOI drawing, scene browsing, analysis
catalogue/    STAC client -- no web framework, testable without a server
  base.py         Scene, SearchQuery, Catalogue protocol
  earthsearch.py  Element84 Earth Search, with retry and deduplication
pipeline.py   composition -- the only module importing both libraries
processing/   pure raster library -- no web dependencies, importable from a notebook
  harmonize.py    DN -> reflectance, with pixel-based offset detection
  masking.py      SCL cloud and shadow masking
  raster_utils.py Grid, AOI snapping, windowed reads (local or HTTP)
  indices.py      NDVI / NDWI / NDBI
  change.py       two-date differencing and compatibility checks
  cog.py          COG writing, validation, provenance tags
examples/     worked analyses over Kolkata
probes/       measurement scripts -- every empirical claim in PLAN.md is re-runnable
tests/        418 tests; 105 need Postgres, Redis or S3, the rest need nothing
docs/         limitations.md -- what Bhoomi cannot tell you; Bhoonidhi access request
PLAN.md       the full project plan, with a live decisions register
```

`catalogue/` and `processing/` never import each other, and neither imports the backend. The
only module that knows both is `pipeline.py` — the seam the worker will call, so the web layer
adds HTTP and nothing else. It is also what lets Bhoonidhi arrive later as a second `Catalogue`
implementation without touching any raster code.

## A note on the hard part

The riskiest code in this project is nine lines in `harmonize.py` that decide whether to
subtract 1000 from a pixel value. Three separate metadata fields claim to answer that question
and **all three are unreliable** — one field means "offset present" on a 2022 scene and "offset
absent" on a 2025 scene. Getting it wrong does not crash anything; it silently shifts NDVI by
~0.24 while leaving every value inside its valid range.

The answer is to detect it from the pixels: the offset is exactly 1000 DN, and reflectance
cannot be meaningfully negative, so a scene carrying the offset has essentially no pixels below
~800 DN. Measured across seven scenes, the offset-bearing one had 0.00 % of pixels below 700 DN
and the other six had 3.48–8.17 %.

**That calibration was right and the code was still wrong.** Those percentages were measured near
full resolution, but the detector shipped sampling the tile at decimation 32 — and overviews are
built by *averaging*, which pulls dark pixels up toward their bright neighbours. At 32 the same
offset-absent scenes measure 0.74–1.42 %, straddling the 1 % threshold; four of eight landed on
the wrong side. One of them missed by 0.024 of a percentage point, and subtracting an offset that
was not there produced 93 % negative reflectance and a median NDVI of +1.703.

**Then the fixed version turned out to be measuring the wrong thing entirely.** Widening the
sample from ten scenes on one tile to 48 scenes across 8 regions — deliberately including desert
and salt flat, where dark pixels barely exist — showed the rule misclassifying **36 % of
offset-absent scenes**. Every Thar Desert and Delhi scene reads below the 1 % threshold, not
because they carry the offset but because those tiles contain almost no dark ground. The
statistic was measuring terrain. It worked on Kolkata because Kolkata is wet.

What replaced it is **one-sided**, which is the honest shape for this problem: a floor below
800 DN *proves* the offset absent, while a high floor proves nothing, because a bright desert
tile and an offset-bearing scene are genuinely indistinguishable from pixels. Where the pixels
cannot decide, the decision falls back to metadata and **the output carries a warning saying so**.
Across the 48 scenes: 0 misclassified, against 17 for the rule it replaces.

Three things are worth taking from that. A calibration is only valid for the sampling that
produced it — and, more sharply, **for the population that produced it**. Some questions are not
answerable from the data you have, and a detector that says "I don't know" is worth more than one
that always answers. And the guard is what caught the first failure: `normalized_difference`
**raises** rather than logs when values leave [−1, 1], which is the only reason it surfaced as a
failed job rather than a plausible-looking raster with a systematic bias.

See [`PLAN.md`](PLAN.md) §5.3, §5.3.1 and §5.3.1c.

## Licence and attribution

Code: Apache-2.0. See [LICENSE](LICENSE).

Sentinel-2 data is provided under the Copernicus licence. Outputs carry the required
attribution — *"Contains modified Copernicus Sentinel data"* — embedded in the COG metadata, so
it survives download.

Bhoonidhi/NRSC terms are under review and no Bhoonidhi data is redistributed by this project.
