# Bhoomi

**On-demand Earth Observation processing for Indian and open satellite data.**

Bhoomi does not display pre-made map layers. It performs geospatial computation on demand and
returns a standards-compliant raster product that another GIS tool can consume.

> ⚠️ **Early development.** Search and server-side processing work end to end — draw an area,
> pick a scene, watch NDVI run, see it on the map, download the Cloud-Optimized GeoTIFF. Not yet
> deployed publicly, and two-date change detection is still to come. See [Status](#status).
> Development runs August 2026 – March 2027.

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

## Which standards?

- **STAC** — catalogue search (Element84 Earth Search, `sentinel-2-l2a`)
- **Cloud-Optimized GeoTIFF** — all outputs, validated before a job is marked complete
- **OGC API – Processes** — planned; the acceptance test is executing a process from a Python
  script with no browser involved

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
| Object storage, change detection | Not started |
| OGC API – Processes | Not started (February 2027) |

A real NDVI over New Town / Rajarhat, submitted to the deployed stack and finished in 11 s:

```
scene   S2C_45QXF_20260227_0_L2A     AOI ~25 km2
ndvi    median +0.332   range -0.295 .. +0.811   valid_fraction 0.99998
COG     EPSG:32645, 10 m, tiled, deflate, overviews, nodata declared -- 940 KB
```

The browser closes the loop: draw an area, search, pick a scene, pick a process, watch live
progress, see the result rendered on the map with a legend and an opacity slider, and download
the GeoTIFF.

Tiles are rescaled to a fixed [-1, 1] rather than stretched per image. A per-image stretch looks
better and means less — two dates of the same area would get different scales, so comparing them
visually would measure the stretch rather than the ground.

> ⚠️ **The tile server is bound to `127.0.0.1` on purpose.** While outputs live on a filesystem,
> TiTiler will open whatever path it is given, so a publicly reachable tile server is an
> arbitrary-file-read. Object storage removes this rather than mitigating it. Do not expose the
> tile server before that lands.

Outputs are written to local disk and served from `/api/v1/jobs/{id}/download` until the
object-storage decision lands. `cog_uri` is a URL either way, so nothing downstream has to
change when it does — but the worker and the API must currently share a filesystem.

301 tests. 64 of them need Postgres or Redis and skip without:

```bash
docker run -d --rm --name bhoomi-test-pg -p 55432:5432 \
    -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=bhoomi_test postgis/postgis:16-3.4
docker run -d --rm --name bhoomi-test-redis -p 56379:6379 redis:7-alpine

BHOOMI_TEST_DATABASE_URL=postgresql://postgres:testpw@localhost:55432/bhoomi_test \
BHOOMI_TEST_REDIS_URL=redis://localhost:56379/1 \
    python -m pytest
```

## Running it

The whole stack, from a clean clone:

```bash
cp .env.example .env
docker compose up --build
#  frontend  http://localhost:3000
#  API       http://localhost:8000/docs
#  tiles     http://localhost:8001      (loopback only -- see the warning above)
```

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
  storage.py      where finished COGs live -- local disk, object storage later
  tiles.py        TiTiler URL shape and the colour ramp per index
  resolve.py      scene id -> Scene, cache first, catalogue second
cache.py      per-scene BOA-offset decisions -- JSON file, or the scenes table
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
tests/        301 tests; 64 need Postgres or Redis, the rest need nothing
docs/         data-source notes and the Bhoonidhi access request
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

Two things are worth taking from that. A calibration is only valid for the sampling that produced
it — the threshold, the statistic and the sample density are one instrument, and `decimation` was
sitting in a function signature looking like an implementation detail. And the guard is what
caught it: `normalized_difference` **raises** rather than logs when values leave [−1, 1], which is
the only reason this surfaced as a failed job rather than a plausible-looking raster with a
systematic bias.

See [`PLAN.md`](PLAN.md) §5.3 and §5.3.1.

## Licence and attribution

Code: Apache-2.0. See [LICENSE](LICENSE).

Sentinel-2 data is provided under the Copernicus licence. Outputs carry the required
attribution — *"Contains modified Copernicus Sentinel data"* — embedded in the COG metadata, so
it survives download.

Bhoonidhi/NRSC terms are under review and no Bhoonidhi data is redistributed by this project.
