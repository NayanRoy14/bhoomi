# Bhoomi — Project Plan

**On-demand Earth Observation processing platform for Indian and open satellite data**

| | |
|---|---|
| **Status** | Pre-development. Written 2026-07-30. Development starts 2026-08-01. |
| **Target** | Working research-grade prototype, deployed publicly, by 2027-03-31 |
| **Type** | Web GIS · Remote sensing · Geospatial backend |
| **Primary data** | Sentinel-2 L2A (COG on cloud object storage) |
| **Secondary data** | NRSC Bhoonidhi — gated on infrastructure prerequisite, see §2.2 |
| **License** | Open source, non-commercial research/educational |
| **Author** | Nayan Roy · BTech IT, GNIT Kolkata, class of 2028 |

---

## How to use this document

- **§3 (Decisions)** is the live part. Locked decisions are not revisited without writing a
  new entry. Open decisions have owners and deadlines.
- **§11 (Roadmap)** has an *exit criterion* per month. A month is not done because the
  calendar says so — it is done when the criterion is met. If a criterion slips, cut scope
  from §12's priority ladder, do not extend the month silently.
- **§13 (Risk register)** has trigger conditions with dates. Check it at the start of each month.

---

# 1. What Bhoomi is

## 1.1 The workflow

```
User draws an AOI on a map
        ↓
Selects date range + satellite + cloud threshold
        ↓
Server searches a STAC catalogue, returns matching scenes
        ↓
User picks a scene and an analysis
        ↓
Async job: read only the AOI window from the scene's bands over HTTP
        ↓
Mask clouds → harmonize reflectance → compute index → reproject/clip
        ↓
Write a Cloud-Optimized GeoTIFF
        ↓
Serve as XYZ tiles, render on the map
        ↓
User views, compares two dates, or downloads the GeoTIFF
```

## 1.2 The one-sentence differentiator

> Bhoomi does not display pre-made map layers. It performs geospatial computation on demand
> and returns a standards-compliant raster product that another GIS tool can consume.

That distinction is the entire value of the project. Everything in this plan that competes
with it — UI polish, extra indices, extra satellites — is subordinate.

## 1.3 Problem statement

India has a large EO archive (Resourcesat, Cartosat, Oceansat and others) and NRSC's Bhoonidhi
provides catalogue and ordering over it. But producing a derived product from any of it still
means: search a catalogue → obtain scenes → open in desktop GIS → clip → select bands →
compute → export → publish separately. Seven manual steps, desktop-bound, not reproducible,
not scriptable, not shareable as a live result.

Bhoomi collapses that into one request, over an API, with a URL as the output.

## 1.4 Explicit non-goals for V1

Not building, and saying no to during development:

| Not building | Because |
|---|---|
| User accounts / auth | Anonymous rate-limited jobs prove the idea. Auth is a week of work that demonstrates nothing novel. |
| Mobile app | The interaction is map-drawing on a large screen. |
| AI chatbot / NL interface | That is [[Avlokan]]'s territory. Keeping them separate keeps both legible. |
| More than 4 indices | The fifth index is a copy-paste of the fourth. Zero marginal signal. |
| Vector editing / full GIS | Out of scope entirely. |
| Kubernetes | One VPS runs this. |
| ML models | Deliberately V2 (§16). Making it a V1 dependency risks both. |

---

# 2. Data source strategy

**This section is the most important correction to the original draft.** The draft named
Bhoonidhi as the primary catalogue with Sentinel-2 as fallback. That ordering is wrong and
must be inverted.

## 2.1 Sentinel-2 is the primary source (P0)

Sentinel-2 L2A is served as COGs on public cloud object storage with anonymous HTTP access
and a STAC API in front of it. This is the only data path that supports Bhoomi's core
architecture — **windowed reads over HTTP range requests** — without downloading whole scenes.

**Catalogue: Element84 Earth Search v1** — `https://earth-search.aws.element84.com/v1`
**Collection: `sentinel-2-l2a`**

Both settled by direct probe on **2026-07-30** (O1, O2 — see §3.1 D9/D10). Verified from
Kolkata, anonymous, no VPN, no credentials:

| Check | Result |
|---|---|
| Endpoint reachable | 200 in ~1.1 s |
| Collections offered | `sentinel-2-l2a`, `sentinel-2-c1-l2a`, `sentinel-2-l1c`, `sentinel-2-pre-c1-l2a` — all with temporal extent from 2015-06-27 |
| Kolkata scenes, 2020 | 45 under 10 % cloud |
| Kolkata scenes, 2026 | 36 under 10 % cloud (Jan–Jul, year in progress) |
| CRS over Kolkata | EPSG:32645 (WGS 84 / UTM 45N) — consistent across both years |
| Asset access | anonymous S3, `Accept-Ranges: bytes`, HTTP 206 confirmed |

Planetary Computer also responded (200 in ~0.9 s) and is retained as the documented fallback,
but it requires SAS-token signing for asset access — an extra dependency and an extra failure
mode for no benefit given Earth Search works anonymously.

**Band mapping.** Earth Search exposes *semantic* asset keys, not raw band numbers — use these
directly, there is no B0x lookup to maintain:

| Purpose | Asset key | ESA band | Native res | Used by |
|---|---|---|---|---|
| Blue | `blue` | B02 | 10 m | RGB preview |
| Green | `green` | B03 | 10 m | NDWI |
| Red | `red` | B04 | 10 m | NDVI |
| NIR | `nir` | B08 | 10 m | NDVI, NDWI, NDBI |
| SWIR-1 | `swir16` | B11 | 20 m | NDBI |
| SWIR-2 | `swir22` | B12 | 20 m | — |
| Scene classification | `scl` | SCL | 20 m | cloud/shadow masking — **required, §5.2** |

**Range-read proof (2026-07-30).** The core architectural premise is confirmed, not assumed:
a single `red` band COG over Kolkata is **206.4 MB**; fetching the first 32 KB — enough for the
COG header and IFDs — took **1.34 s** and transferred **0.015 %** of the file, returning valid
TIFF magic (`II*\0`). Windowed reads over HTTP work from India with no credentials. This is the
assumption everything else in Bhoomi rests on, and it holds.

## 2.2 Bhoonidhi — portal access confirmed, API access still unknown

**Checked directly on the live portal, 2026-07-30:**

| Finding | Detail |
|---|---|
| **Portal account already exists** | Logged in as NAYAN ROY. Layer 1 is done — no registration needed. |
| **Indian Space Policy 2023** | Data of **5 m resolution and coarser is free and open to all users**. Resourcesat-2/2A LISS-III (23.5 m) and AWiFS (56 m) fall in this class. |
| Finer than 5 m | Open to Government Entities via a GE Declaration Form; priced for others. Commercial handling moved to **NSIL** on 2024-05-01. |
| **No documented API** | Nothing in the main navigation, Utilities menu, Help documents or FAQ mentions an API, STAC endpoint, or programmatic access. |
| Contact | `bhoonidhi@nrsc.gov.in` |

**This improves the P2 outlook materially.** The earlier assumption was that Indian EO data was
effectively out of reach. In fact the *data* is open — it is the *machine interface* that is
undocumented. Even with no API at all, open-category scenes can be obtained through the portal
and processed by Bhoomi's existing pipeline via the local-file path (§5.1), which is exactly why
that abstraction exists.

Note also that Resourcesat AWiFS/LISS-III are **not COGs on public object storage**, so the
windowed-HTTP-read model does not apply to them. They confirm the need for the download-and-stage
path rather than replacing the Sentinel-2 approach.

Draft request to NRSC: `docs/bhoonidhi-access-request.md`. **Unsent as of 2026-07-30.**

## 2.2.1 The remaining prerequisite

**Verified constraint (2026-07-27, against the live portal during Avlokan planning):**
Bhoonidhi API access requires a **static public IPv4 address whitelisted by NRSC
administrators**. This rules out laptops, dynamic residential IPs, and all serverless hosting.

Three consequences that must be designed for, not discovered:

1. **Hosting is decided by this.** The backend must run on a **fixed-IP VPS**, not
   Vercel/Render/Fly-style ephemeral compute. This is a *December* decision (Milestone 1
   deploys), not a March one. Do not build a deploy you will have to tear out.
2. **Bhoonidhi is not COG-over-HTTP.** Even with access, expect an order-and-download model
   over full scenes, not range requests. The processing layer must therefore support a second
   ingest path: *download scene → stage locally → process from local file*. Design
   `raster_utils.py` so the reader is an abstraction over "local path or HTTP URL" from day
   one (§5.1). This costs nothing now and saves a rewrite in March.
3. **The request needs a deadline.** Submit the whitelist/access request in **August 2026**.
   If there is no working access by **2027-01-15**, Bhoonidhi is cut and the P2 slot is filled
   by the §13.2 alternative. An open-ended admin request will otherwise consume March.

## 2.3 What "Indian EO" means if Bhoonidhi fails

Do not let the P2 differentiator depend on a single gated portal. Fallbacks, in order:

1. **Bhuvan WMS/WCS layers** — public, no whitelist. Weaker (serves rendered/derived layers,
   not raw bands) but demonstrably Indian EO and standards-based. Note from Avlokan: Bhuvan
   tokens expire in ~24 h, so any integration must be cache-backed.
2. **Sentinel-2 over Indian AOIs, framed honestly** — the flagship Kolkata demo is already
   this. The platform is India-focused in *what it analyses*, not only in whose satellite.
3. **§13.2 backup project** if the whole EO-access premise collapses.

---

# 3. Decisions register

## 3.1 Locked

| # | Decision | Rationale |
|---|---|---|
| D1 | **Sentinel-2 primary, Bhoonidhi P2-gated** | §2. Architecture depends on COG range reads. |
| D2 | **Backend is Python + FastAPI** | Every raster library is Python. A Node API + Python worker means maintaining two services and two dependency trees for no gain. |
| D3 | **One scene per analysis in V1; AOI must fit inside one scene** | The alternative (mosaicking across tile boundaries with seam handling) is real work that can be added later without redesign. Reject oversized AOIs with a clear message rather than silently mosaicking badly. Revisit as P1.5. |
| D4 | **Indices are computed at the coarsest native resolution of their input bands** | NDVI/NDWI at 10 m (B04/B03/B08). NDBI at **20 m** (B11 is 20 m; upsampling SWIR to 10 m invents detail that isn't in the sensor). Document this in the UI. |
| D5 | **Outputs live in object storage (S3/R2), not worker-local disk** | TiTiler reads COGs over HTTP range requests, so object storage is the natural fit; it also survives worker restarts and redeploys. `outputs.cog_uri` is a URL, never a filesystem path. |
| D6 | **Docker Compose exists from December, not February** | Reproducibility is needed *before* the worker/Redis/TiTiler layer lands, not after. |
| D7 | **Anonymous usage, rate-limited by IP** | See §1.4. |
| D8 | **Cloud masking (SCL) is part of every index from the first implementation** | §5.2. Retrofitting it after change detection is built means redoing the change math. |
| D9 | **Catalogue is Element84 Earth Search v1** *(resolves O1, 2026-07-30)* | Probed from Kolkata: 200 in ~1.1 s, anonymous asset access, no SAS signing. Planetary Computer works too but needs token-signing for every asset — an extra dependency and failure mode for no gain. Retained as documented fallback. |
| D10 | **Collection is `sentinel-2-l2a`** *(resolves O2, 2026-07-30)* | Probe showed `l2a` and `c1-l2a` identical on baseline, cloud cover, CRS and asset names for the same acquisition — but only `l2a` exposes `earthsearch:boa_offset_applied`. That explicit flag removes the single most dangerous ambiguity in the pipeline (§5.3). |
| D11 | **Demo pair is 2020-03-10 vs 2026-03-04 on tile 45QXF** *(2026-07-30, revised same day)* | Both EPSG:32645, cloud **0.000 %** and 0.00 %, six days apart in day-of-year — phenology matched, §5.4.4 seasonality confound near-eliminated. **Tile corrected from 45QXE to 45QXF:** central Kolkata (22.573 N) sits in the narrow overlap of both tiles, but 45QXE's top edge is 22.604 N, so any AOI of useful size falls off it. 45QXF spans 22.506–23.507 N and contains the whole **New Town / Rajarhat / Salt Lake** corridor — which is also where Kolkata's 2020–2026 urban expansion actually happened, making it a far stronger NDBI/NDVI change demo than the already-built-up city centre. |
| D13 | **Demo AOI is New Town / Rajarhat: `88.35, 22.55, 88.52, 22.68`** *(2026-07-30)* | ~17 × 14 km ≈ 250 km², inside the §8 500 km² cap, fully inside tile 45QXF, and centred on the actual change corridor. |
| D12 | **Python 3.14 is fine** *(2026-07-30)* | `rasterio 1.5.0` ships a `cp314` Windows wheel; downloaded and confirmed. No interpreter downgrade needed. |
| D14 | **Object storage is Cloudflare R2** *(resolves O4, 2026-07-31)* | Egress is the dominant cost *and the dominant risk*, because TiTiler re-reads the COG on every tile and the point of a portfolio project is that strangers look at it. R2 charges none. See §9.4 for the numbers and for why B2 and S3 lose. |

## 3.2 Open — with owners and deadlines

| # | Question | Decide by | How to decide |
|---|---|---|---|
| ~~O1~~ | ~~Earth Search vs Planetary Computer~~ | ✅ **Resolved 2026-07-30** → D9 | |
| ~~O2~~ | ~~`sentinel-2-l2a` vs `sentinel-2-c1-l2a`~~ | ✅ **Resolved 2026-07-30** → D10 | |
| O3 | Hosting provider for the fixed-IP VPS | 2026-11-30 | Needs ≥4 GB RAM (§8), a static IPv4, and Indian/Singapore region for latency. Budget cap: ₹700/month. |
| ~~O4~~ | ~~Object storage: R2 vs S3 vs B2~~ | ✅ **Resolved 2026-07-31** → D14, §9.4 | |
| O5 | Hand-roll OGC API – Processes vs mount pygeoapi | 2027-01-31 | Hand-rolling teaches more and keeps one service. Read pygeoapi's process-description schema either way — matching the standard's shape is most of the credibility. Default: hand-roll. |
| O6 | Whether Bhoonidhi access materialises | **2027-01-15** | Hard gate. See §2.2. |

---

# 4. Architecture

## 4.1 Component diagram

```
                            BROWSER
                               │
                    ┌──────────┴──────────┐
                    │  Next.js + MapLibre │
                    │  AOI draw · scenes  │
                    │  job poll · swipe   │
                    └──────────┬──────────┘
                               │ HTTPS
                    ┌──────────┴──────────┐
                    │   FastAPI backend   │
                    │  /search /jobs /ogc │
                    └─┬────────┬────────┬─┘
                      │        │        │
        ┌─────────────┘        │        └──────────────┐
        ▼                      ▼                       ▼
┌───────────────┐      ┌──────────────┐      ┌──────────────────┐
│  STAC client  │      │   PostGIS    │      │   Redis (queue   │
│ Earth Search  │      │ scenes/jobs  │      │   + rate limit)  │
│  (Bhoonidhi)  │      │   outputs    │      └────────┬─────────┘
└───────────────┘      └──────────────┘               │
                                                      ▼
                                          ┌───────────────────────┐
                                          │    RQ worker(s)       │
                                          │  Rasterio · NumPy     │
                                          │  windowed COG reads   │
                                          └───────────┬───────────┘
                                                      │ write
                                                      ▼
                                          ┌───────────────────────┐
                                          │  Object storage (R2)  │
                                          │   result COGs         │
                                          └───────────┬───────────┘
                                                      │ HTTP range
                                                      ▼
                                          ┌───────────────────────┐
                                          │       TiTiler         │
                                          │   XYZ / WMTS tiles    │
                                          └───────────┬───────────┘
                                                      │
                                                      └──→ MapLibre
```

## 4.2 Why the queue exists

A raster job takes seconds to minutes. HTTP requests that take minutes fail: proxies time out,
browsers give up, retries duplicate work, and one slow job blocks a worker thread that should
be serving searches. The queue makes job duration irrelevant to API responsiveness, and it is
also what makes OGC API – Processes' async execution model natural rather than bolted on.

## 4.3 Job state machine

```
QUEUED ──→ SEARCHING ──→ READING ──→ PROCESSING ──→ WRITING_COG ──→ COMPLETED
   │           │            │            │              │
   └───────────┴────────────┴────────────┴──────────────┴──────→ FAILED
                                                                    │
                                              (also) ──→ CANCELLED  │
                                                        TIMED_OUT ──┘
```

Each transition writes `progress` (0–100) and a timestamp. `FAILED` always carries a
user-readable `error_message` **and** a separate internal traceback field never returned by
the API.

---

# 5. Processing specification

This is the technical core of the project. Get this right; everything else is plumbing.

## 5.1 The reader abstraction

Every raster read goes through one function whose source may be an HTTP(S) COG URL or a local
staged file:

```python
# processing/raster_utils.py

def read_window(
    source: str,            # "https://…/B04.tif" or "/data/staged/scene/B04.tif"
    aoi_geom: BaseGeometry, # shapely, EPSG:4326
    target_crs: CRS,
    target_res: float,
    resampling: Resampling = Resampling.bilinear,
) -> tuple[np.ndarray, Affine]:
    """Read only the pixels intersecting aoi_geom. Never reads the full scene."""
```

Writing this abstraction on day one is what makes the Bhoonidhi download-path (§2.2) a
configuration change instead of a rewrite. The HTTP path relies on GDAL's `/vsicurl/` and
requires `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` and a sane
`CPL_VSIL_CURL_ALLOWED_EXTENSIONS` to avoid pathological request counts.

## 5.2 Cloud masking — mandatory

Sentinel-2 L2A ships an `SCL` (Scene Classification Layer) band at 20 m. NDVI computed over
cloud or cloud shadow is meaningless, and a two-date *difference* of two unmasked scenes
doubles the error into something that looks like real change. This is not a polish item.

**Mask out** these SCL classes before any index computation:

| SCL value | Class | Masked |
|---|---|---|
| 0 | No data | ✓ |
| 1 | Saturated / defective | ✓ |
| 2 | Dark area pixels (cast shadow) | ✓ |
| 3 | Cloud shadow | ✓ |
| 8 | Cloud, medium probability | ✓ |
| 9 | Cloud, high probability | ✓ |
| 10 | Thin cirrus | ✓ |
| 11 | Snow / ice | ✓ (context-dependent; configurable) |
| 4, 5, 6, 7 | Vegetation, bare, water, unclassified | kept |

> Verify these class values against the current Sen2Cor product spec in August — they are
> stable but worth confirming once rather than trusting from memory.

**Verified against real pixels, 2026-07-30** (`probes/verify_scl.py`, demo AOI, 881×728 @ 20 m).
No unexpected class values appeared, so the table above is correct as written:

| Class | 2020 share | 2026 share | Action |
|---|---|---|---|
| 2 — dark area / cast shadow | 0.30 % | 0.17 % | mask |
| 4 — vegetation | 27.67 % | **9.45 %** | keep |
| 5 — not vegetated (bare/built) | 61.73 % | **80.24 %** | keep |
| 6 — water | 9.36 % | 9.26 % | keep |
| 7 — unclassified | 0.95 % | 0.87 % | keep |

Cloud classes 8/9/10 are **entirely absent** from both scenes — masking removes only 0.30 % and
0.17 % respectively. The demo pair is genuinely clear, which is why D11 chose it. Masking still
ships as P0, because a user-drawn AOI on an arbitrary date will not be this lucky.

> The 2020→2026 shift in classes 4 and 5 looks like a dramatic result. **It is substantially
> overstated** — see §5.4.4.

Masked pixels become `NaN` in the float32 working array and are written as the COG's nodata
value. **Record the valid-pixel fraction** in the output metadata; a result that is 80 % cloud
should say so rather than render as a mostly-empty raster the user misreads.

## 5.3 Reflectance harmonization — the silent demo-killer

From **Processing Baseline 04.00 (25 January 2022)**, Sentinel-2 L2A products apply
`BOA_ADD_OFFSET = -1000`. Physical reflectance is therefore:

```
baseline <  04.00 :  reflectance = DN / 10000
baseline >= 04.00 :  reflectance = (DN - 1000) / 10000
```

The flagship demo compares **2020 against 2026** — it straddles this change. Subtracting a
constant from both bands **does not cancel** in a normalized difference:

```
NDVI_true  = (NIR - RED) / (NIR + RED)
NDVI_naive = (NIR' - RED') / (NIR' + RED')   where X' = X - 1000
           ≠ NDVI_true
```

The result is a plausible-looking vegetation shift across the whole scene that is pure
metadata artifact — the worst class of bug, because it produces a confident wrong answer.

### The date heuristic is wrong — verified 2026-07-30

An earlier draft of this section said: *"if the property is absent, derive from the acquisition
date against 2022-01-25."* **That rule is actively harmful and has been removed.** Probing real
STAC items over Kolkata shows the archive has been **retrospectively reprocessed**:

| Collection | Acquisition | `s2:processing_baseline` | `earthsearch:boa_offset_applied` |
|---|---|---|---|
| `sentinel-2-l2a` | **2020-03-10** | **05.00** | **`True`** |
| `sentinel-2-l2a` | 2026-03-04 | 05.12 | `True` |
| `sentinel-2-c1-l2a` | 2020-03-10 | 05.00 | *absent* |
| `sentinel-2-c1-l2a` | 2026-03-04 | 05.12 | *absent* |

A **2020 acquisition carries baseline 05.00 and has the offset applied.** A date-based rule
would classify it as pre-04.00, skip the offset, and produce exactly the silent cross-date
error this section exists to prevent — worse than doing nothing, because it introduces the bug
in the name of fixing it.

### What the flag actually means — measured 2026-07-30, and it is the opposite of the obvious reading

`earthsearch:boa_offset_applied: True` does **not** mean "the −1000 offset is sitting in these
pixels, go subtract it." It means **Earth Search has already applied the correction for you.**
The pixels are ready to use as `DN / 10000` with no further arithmetic.

Measured over the demo AOI (2.56 M pixels, `probes/verify_offset2.py`):

| Dataset | baseline | flag | red DN p50 | nir DN p50 |
|---|---|---|---|---|
| `S2A_45QXF_20200310_1_L2A` | 05.00 | `True` | 808 | 1783 |
| `S2A_45QXF_20200310_0_L2A` | 02.14 | `False` | 837 | 1778 |
| `S2B_45QXF_20260304_0_L2A` | 05.12 | `True` | 878 | 1851 |

If a +1000 offset were baked into the `True` rows, their DNs would sit ~1000 above the `False`
row. They do not — all three agree. And the decisive test, since NDVI is mathematically bounded
to [−1, 1] whenever both reflectances are positive:

| Formula | % of pixels outside [−1, 1] |
|---|---|
| `(N − R) / (N + R)` | **0.00 %** on all three datasets |
| `(N − R) / (N + R − 2000)` | **65.2 % / 63.7 % / 56.9 %** — physically impossible |

### Settled answer: no metadata field is reliable — detect from the pixels

Three successive readings of the metadata were each falsified by measurement. The final one
came from a 7-scene series over the demo AOI (2020–2026), where the flag means **two opposite
things**:

| Scene | flag | baseline | red DN p50 | Reality |
|---|---|---|---|---|
| 2022-02-13 | `False` | 04.00 | **1763** | offset genuinely **present** → subtract |
| 2025-03-04 | `False` | 05.11 | 862 | offset **absent** → must not subtract |

All three candidate metadata sources fail:

| Source | Verdict |
|---|---|
| `earthsearch:boa_offset_applied` | `False` means both "present" (2022) and "absent" (2025) |
| `raster:bands.offset` | reports `-0.1` uniformly, contradicting measurement on 2 of 3 checked |
| GeoTIFF `scales`/`offsets` tags | unset (`1.0` / `0.0`) |

**The detector, derived from physics rather than fitted to observations.** The offset is exactly
1000 DN, and reflectance cannot be meaningfully below about −0.02 (−200 DN). So a scene carrying
the offset has essentially **no** pixels below ~800 DN, while a scene without it has dark targets
— shadow, deep water — reaching near zero. Rule:

> **The offset is present when fewer than 1 % of valid pixels fall below 700 DN.**

Measured separation across all seven scenes: the offset-bearing scene had **0.00 %** of pixels
below 700 DN; the other six had **3.48 %–8.17 %**. All seven classify correctly, including the
two the metadata gets wrong.

**Implementation** (`processing/harmonize.py`):

1. `measure_offset_floor_in_scene(band_url)` reads a **decimated overview of the full tile**, not
   the AOI window. A small AOI of uniformly bright bare soil has no dark pixels either way and
   would be misread; the 110 km tile has the best chance of containing water or shadow. **How far
   it decimates turned out to matter as much as the threshold — see §5.3.1.**
2. `resolve_offset(evidence, properties)` combines them, in this order: a pre-04.00 baseline
   settles it; otherwise the pixels settle it **if they can**; otherwise metadata decides and the
   result carries a warning. Pixels take precedence where they speak — metadata has been observed
   wrong on 3 of 48 measured scenes, and the pixel test catches every one.
3. Fewer than 10,000 valid sample pixels → **fail**, do not judge the convention from a handful.
4. Neither conclusive pixels nor a metadata flag → **fail**. Guessing here shifts NDVI by ~0.24
   while leaving every value in range, which is worse than a visible error.

> **The pixel test is one-sided and cannot be made two-sided.** It proves the offset ABSENT or
> says nothing; `present=True` is only ever reached through metadata. See §5.3.1c — a bright
> tile with no dark target is genuinely indistinguishable from an offset-bearing one.

> **Open check:** `sentinel-2-c1-l2a` exposes no flag at all. The pixel test works there, but
> only when it is conclusive — with no flag to fall back on, an inconclusive scene fails outright
> rather than being guessed. Measure before ever using c1-l2a.

## 5.3.1 The threshold was calibrated at one sampling density and applied at another

**Found 2026-07-31, by the first real NDVI job failing.** The measured separation above —
0.00 % against 3.48 %–8.17 % — is real, but it was measured at (or near) full resolution, while
`detect_offset_in_scene` shipped with `decimation=32`. Overviews are built by **averaging**, and
averaging pulls dark pixels up toward their bright neighbours, so the dark tail the detector
measures shrinks as decimation grows. Re-measured over tile 45QXF, nine scenes 2020–2026:

| decimation | offset present | offset absent | where the 1 % threshold falls |
|---|---|---|---|
| 4 | 0.000 % | 1.927 %–2.955 % | below every absent scene — **clean** |
| 8 | 0.000 % | 1.207 %–1.982 % | 0.21 pp of margin — thin |
| 16 | 0.000 % | 0.751 %–1.442 % | **inside** the absent range — broken |
| 32 *(shipped)* | 0.000 % | 0.744 %–1.422 % | **inside** the absent range — broken |

At 32, **four of eight offset-absent scenes fell below the threshold** and were classified as
offset-bearing. `S2C_45QXF_20260227_0_L2A` measured 0.976 % — twenty-four thousandths of a
percentage point on the wrong side — and subtracting an offset that was not there produced
**93 % negative reflectance and a median NDVI of +1.703**.

Ground truth was established from physics rather than from the rule under test: surface
reflectance cannot be meaningfully negative, so the assumption that keeps NDVI inside [−1, 1] is
the true one. The 2022 scene needed a different discriminator, because subtracting 1000 from
values that all exceed 1000 leaves everything in range — there the **floor** settles it
(min 918 DN, versus min 1 DN on an offset-absent scene).

`DEFAULT_DECIMATION` is now **4**, with the table above recorded beside it. Cost: ~7 s against
~0.5 s, paid once per scene ever and cached (§8).

**Two consequences worth stating plainly.**

*Every offset decision recorded before this fix is untrustworthy* and cannot be repaired row by
row — you cannot tell which happened to be right. Migration `0003` nulls
`scenes.boa_offset_present`, and the JSON cache filename is versioned so old entries are ignored
rather than reused. A NULL costs one re-measurement; a wrong value silently shifts every index
computed from that scene.

*The guard is what caught this.* §5.3's own lesson — that `normalized_difference` must raise
rather than log — is the only reason this surfaced as a failed job instead of a plausible-looking
raster with a systematic bias. It fired on the first real scene it was pointed at. **The lesson
paid for itself within one commit of the code it was written to protect.**

**And a third lesson, new:** a calibration is only valid for the sampling that produced it. The
threshold, the statistic and the sample density are one instrument; documenting two of the three
left the constant that mattered most looking like an implementation detail. `decimation` was a
default argument in a signature, not a number anyone had reasoned about.

**Two engineering lessons, both bought expensively:**

- **A property name is a claim, not a fact.** Every plausible reading of this field was wrong at
  least once. Only pixels settled it.
- **A safety net a caller can mute is not a safety net.** `normalized_difference` logged the
  out-of-range excursion correctly during the failed run, but the calling script had raised the
  logging level and silenced it — so a median NDVI of 1.000 reached the analysis. It now raises
  `ImplausibleIndexError` when more than 1 % of pixels need clamping.

### The hazard is live in the catalogue — both versions of one acquisition exist

Probing 2020-03-10 over tile 45QXF returned **the same acquisition twice**:

| STAC ID | `s2:processing_baseline` | `boa_offset_applied` |
|---|---|---|
| `S2A_45QXF_20200310_**1**_L2A` | 05.00 | **`True`** |
| `S2A_45QXF_20200310_**0**_L2A` | 02.14 | **`False`** |

Same satellite, same moment, same ground — served twice, distinguished only by a version digit
in the ID. A user's search can return `_0_` for one date and `_1_` for another with nothing in
the UI to signal it.

**These are not two encodings of one answer.** Measurement shows median NDVI of **0.323**
(`_1_`, Sen2Cor 05.00) versus **0.309** (`_0_`, Sen2Cor 02.14) over the same 2.56 M pixels — a
real ~0.014 difference, because the *atmospheric correction algorithm itself* changed between
Sen2Cor versions, not merely the storage convention. Per-pixel differences reach ±3900 DN.

That has a direct consequence for §5.4.4: mixing processing baselines across the two dates puts
Sen2Cor version drift into the "change". Record `s2:processing_baseline` on every output (§5.6)
and warn in the UI when the two scenes differ.

> **Corrected 2026-07-31.** This rule used to read "a change-detection job **must not** mix
> processing baselines". That is unsatisfiable, and it forbade this project's own flagship demo.
> Measured over the D13 AOI: 2020 is served only at baseline **02.14 or 05.00**, and 2026 only at
> **05.12** — there is no matched pair across the span, because the baseline records when the
> product was *processed*, not when it was acquired. Any multi-year comparison on this collection
> mixes baselines by construction. The rule is therefore **must not mix them silently**, which is
> what the implementation does: warn, record both baselines in the output, and let the user
> decide. D11's pair is 05.00 against 05.12 and always was.

It also means the earlier idea of using the two versions as an exact-equality regression test
is **invalid** — they are legitimately different products. Keep the synthetic fixture test for
the offset logic, and use this pair instead as a *baseline-mismatch* test: assert the API
flags the mismatch rather than silently differencing them.

> Note the version digit is *not* an offset flag — `S2B_45QXF_20260304_**0**_L2A` is `_0_` and
> *does* have the offset applied. It means "version 0 of this item". Never infer from it.

**Silver lining for the demo:** the chosen pair (both `offset_applied = True`) is internally
consistent, so the flagship comparison is safe. The archive happened to absorb the risk. Do not
let that make the code lazy — a user drawing an AOI over a `_0_` scene still needs the rule.

**Regression test (write it in September, keep it in CI):** feed one item with
`boa_offset_applied: True` and one with `False`, carrying identical *physical* reflectance,
and assert the resulting NDVIs match within 1e-6. Add a third case with both properties absent
and assert it raises rather than silently defaulting.

## 5.3.1b Independently verified for one scene, 2026-07-31

`S2C_45QXF_20260227_0_L2A` — the scene the §5.3.1 fix actually changed, which measured 0.976 %
at the old decimation and was misclassified — checked four ways, none of which runs Bhoomi's
detector:

1. **Physics.** Lowest valid DN across NIR and red is **319**. An offset-bearing product cannot
   have valid pixels below ~800, because reflectance cannot be below about −0.02. The offset is
   absent, which is what the detector concluded.
2. **Independent recomputation.** NDVI computed from the raw bands with bare numpy and rasterio,
   importing nothing from `processing/` or `pipeline`, onto the grid Bhoomi's own output declares:
   **100.0000 % of 233,027 pixels agree to 1e-4**, maximum absolute difference 1.13e-07, which is
   float32 resolution.
3. **The other branch is visibly broken.** Assuming the offset present gives a median of **+0.961**
   with **48.0 %** of pixels outside [−1, 1]. There is no ambiguity between the two answers.
4. **Land cover behaves.** Classes picked from *raw band* thresholds, never from NDVI, so the test
   is not circular:

| class (from raw bands) | pixels | NDVI median | p10 | p90 |
|---|---|---|---|---|
| water — NIR-absorbing | 7,313 | **−0.086** | −0.184 | +0.014 |
| vegetation — NIR-bright, red-absorbing | 23,727 | **+0.587** | +0.515 | +0.682 |
| built-up — SWIR-bright | 33,167 | **+0.209** | +0.117 | +0.337 |

SCL, a classifier shipped with the product and not used to compute anything here, agrees: its
water class sits at −0.155, vegetation at +0.592, not-vegetated at +0.315.

A harmonization error shifts every value by roughly +0.24 or more. Water at −0.086 could not
survive that, and neither could the [−1, 1] bound. **The decision is correct for this scene.**

**What this does not establish.** One scene. The calibration still rests on ten scenes over one
tile with ~0.49 pp of margin (§5.3.2), and nothing here speaks to a different tile, a different
season, or a genuinely offset-bearing product — of which only one has ever been measured.

> **Superseded 2026-07-31 by §5.3.1c.** Widening the sample to 48 scenes across 8 regions showed
> the dark-fraction statistic does not generalise off tile 45QXF at all. The verification above
> still holds — it checks the *decision* for that scene, which the replacement rule reaches too —
> but the detector it was verifying has been replaced.

---

## 5.3.1c The calibration did not generalise, and the statistic was wrong

**Measured 2026-07-31, on the widest sample this project has taken.** §5.3.1b closed by saying a
wider sample "over more tiles and more of the world would be the honest next step". It was, and
it invalidated the detector.

**48 scenes: 8 regions × 6 years (2019–2026), baselines 02.11 through 05.12.** Arid tiles — Thar
Desert, Kutch salt flats — were included deliberately, because the rule counts *dark* pixels and
a tile with little water or shadow is the obvious way for it to fail.

It failed there, and not marginally:

| decimation | offset-absent scenes | called offset-present — **wrong** | error rate |
|---|---|---|---|
| 4 *(shipped)* | 47 | 17 | **36.2 %** |
| 8 | 47 | 17 | 36.2 % |
| 16 | 47 | 18 | 38.3 % |
| 32 | 46 | 21 | 45.7 % |

Decimation 4 is still the best of the four, so §5.3.1's fix was in the right direction. **But the
decimation was never the real problem.** By region, dark fraction at decimation 4:

| region | min … max over six years | |
|---|---|---|
| thar-desert | 0.0057 % … 0.0206 % | **entirely below the 1 % threshold** |
| delhi-urban | 0.0184 % … 0.3265 % | **entirely below the 1 % threshold** |
| kutch-saltflat | 0.0466 % … 27.10 % | straddles |
| kolkata-wetland | 0.1018 % … 3.09 % | straddles |
| ghats-forest | 0.6066 % … 60.35 % | straddles |
| sundarbans | 8.10 % … 30.66 % | clear |
| mumbai-coast | 41.97 % … 60.70 % | clear |

Every Thar and Delhi scene reads below 1 %. All are offset-**absent**. The detector would
subtract a phantom −1000 from each and shift NDVI by ~0.24, with nothing out of range to reveal
it — precisely the silent failure §5.3 exists to prevent.

**The statistic was measuring terrain, not the offset.** It worked on 45QXF because Kolkata is
wet. Ten scenes on one tile could not have shown this; that is the whole lesson.

### The one offset-bearing product, and how it was nearly missed

`chennai-coast 2022-02-04` (`S2A_44PMV_20220204_0_L2A`, baseline 04.00) is the only genuinely
offset-bearing scene in the sample. The automated ground-truth rule classified it **absent** on a
distribution minimum of 795 DN — defeated by **2 valid pixels out of 7,535,025**. Its p0.001 is
already 820 and its p0.1 is 1003.

Confirmed against its own tile, which is the check that settles it:

| | median NDVI |
|---|---|
| 2021 peer on 44PMV, offset absent | −0.1511 |
| target assuming **present** | **−0.1462** |
| target assuming absent | −0.0201 |

Same tile, six weeks apart in season. The present branch reproduces the peer; the absent branch
misses by 0.13. So the "present" class still rests on **two** scenes — this and the 2022 scene of
§5.3 — which remains the weakest part of the evidence.

### The replacement: a one-sided floor test, with metadata only as a fallback

An offset-bearing product cannot hold pixels below ~800 DN, because reflectance cannot sit below
about −0.02. Test the **0.1st percentile**, not the minimum, so a handful of outliers cannot
defeat it:

| rule | fires on | wrong when it fires |
|---|---|---|
| **floor p0.1 < 800 DN → offset absent** | 37 / 48 | **0** |
| fallback to metadata for the remaining 11 | 11 / 48 | **0** |
| metadata *alone*, all 48 | — | **3** |

**The test is one-sided, and this is forced by the data, not a design preference.** A high floor
is equally consistent with an offset-bearing product and with a bright tile containing no dark
target. The classes overlap completely up there:

```
offset ABSENT,  floor >= 800 DN : 922, 942, 983, 1094, 1464, 1777, 1814, 1938, 2045, 2048
offset PRESENT                  : 1003
```

1003 sits inside that range. A narrower "just above 800 means present" band fails too — Delhi
2019 is offset-absent at 1094. **So the pixel test proves ABSENT or says nothing, and
`present=True` is only ever reached through metadata.**

**The layering is the point.** Metadata alone gets three scenes wrong — Kolkata 2022-03-20, Delhi
2022-04-19 and Sundarbans 2022-03-22 all carry `boa_offset_applied: false` while their pixels are
plainly unshifted (floors of 240, 648 and 96 DN). Every one is caught by the pixel test, so the
flag is never consulted for them. Pixels first; metadata only where pixels are genuinely silent,
and then **the output carries a warning** (§7.5) rather than swallowing the uncertainty.

The 11 inconclusive scenes are inconclusive for a real reason, not a fixable one: a bright tile
with no water and no shadow contains no dark target, so the offset **cannot be determined from
its pixels at all**. Thar at p0.1 ≈ 2000 stays plausible whether or not 1000 is subtracted. That
case needs metadata plus a warning, not a cleverer threshold.

### Decimation, re-measured for the new statistic

The floor also drifts upward under averaging, so decimation still costs something:

| scene | dec 4 | dec 8 | dec 16 | dec 32 | |
|---|---|---|---|---|---|
| chennai 2022-02-04 *(present)* | 1003 | 1010 | 1021 | 1027 | stable |
| kolkata 2019-04-30 | 698 | 764 | 800 | 846 | **absent → inconclusive** |
| delhi 2022-04-19 | 648 | 798 | 1031 | 1299 | **absent → inconclusive** |
| sundarbans 2022-03-22 | 96 | 104 | 114 | 136 | stable |
| ghats 2022-03-12 | 238 | 252 | 278 | 354 | stable |
| thar 2022-04-30 | 1938 | 2012 | 2082 | 2175 | stable |

**But the failure mode differs in kind from the old rule.** The floor only ever moves *up*, and
the pixel test's only positive claim is "absent", so a coarser sample **loses a verdict rather
than inverting one**. The dark-fraction rule flipped scenes to the opposite answer.

`DEFAULT_DECIMATION` stays **4**, now for a concrete reason: Delhi 2022-04-19 is conclusive at 4
and 8 but not at 16, and its metadata claims the offset is present when it is not — so at 16 the
fallback would get that scene wrong.

### The cache now stores the measurement, not the verdict

`scenes.boa_offset_present` cached a *conclusion*, and that is why §5.3.1 cost a full cache purge
(migration `0003`, cache filename v2): a boolean does not carry the number that produced it, so
no row could be repaired and every scene had to be re-read over the network.

Migration `0005` replaces it with `boa_floor_dn DOUBLE PRECISION` — the raw p0.1, which does not
move when a threshold does. The verdict is derived on read. **A future recalibration now costs
nothing.** The JSON cache goes to v3 for the same reason; note that `bool` is a subclass of `int`
in Python, so v1/v2 entries are explicitly rejected rather than read as a floor of 0.0 DN, which
would silently prove every scene offset-absent.

Verified end to end by `probes/verify_offset_rule.py`, which replays all 48 scenes through the
shipped `resolve_offset`: **0 misclassified, against 17 for the rule it replaces.**

### The lesson

§5.3.1 concluded that "a calibration is only valid for the sampling that produced it". The wider
sample adds the sharper version: **a calibration is only valid for the population that produced
it.** Ten scenes on one wet tile produced a statistic that measured how much water a tile
contains. The arid tiles were added specifically to try to break it, and they did — which is the
only reason this was found before it reached a user's result rather than after.

---

## 5.3.2 Open risk — detection cost can exceed the job timeout

**Measured 2026-07-31, and not yet resolved.** §5.3.1 set `DEFAULT_DECIMATION = 4` and recorded
its cost as ~11 s warm, ~159 s on an unwarmed read. The tail is worse than that. On a freshly
started worker container, a single offset detection took **492 seconds** — eight minutes and
twelve seconds for one scene:

```
08:12:38  job starts
08:20:50  offset detection completes for scene 1   (492 s)
08:23:00  job killed at the 10-minute limit (PLAN.md 8), status timed_out
```

A two-date change job needs **two** detections, so a change job on two uncached scenes can
exceed the §8 timeout before it computes anything. The state machine handled it correctly —
`timed_out`, not `failed`, with the right message — and resubmitting after the first detection
was cached completed in **17 s**. So the failure is recoverable, and the cost is once per scene
ever. But a first-time user can hit a ten-minute timeout for no reason they can see.

**The margin is also thinner than §5.3.1 recorded.** That table gave 1.927 %–2.955 % for
offset-absent scenes at decimation 4. `S2A_45QXF_20200330_1_L2A` measures **1.490 %** — below
that range, still correctly classified, but with about **0.49 pp** of margin against the 1 %
threshold rather than the 0.93 pp claimed. Nine scenes is not many.

> **The margin paragraph above is obsolete (2026-07-31).** §5.3.1c replaced the statistic
> entirely: the 1 % dark-fraction threshold misclassified 36 % of offset-absent scenes once the
> sample left tile 45QXF. There is no margin to quote because there is no threshold on that
> statistic any more. **The cost problem below is unaffected** — it is a property of reading a
> decimated overview at all, not of which statistic is computed from it.

**Not fixed here, because the options trade against each other:**

| Option | Cost |
|---|---|
| Accept and document | A first job on a fresh scene can time out. Recoverable by resubmitting. |
| Decimation 8 | ~4× less data. Costs conclusiveness on 1 of the 6 scenes re-measured in §5.3.1c; never inverts a verdict. |
| Detect outside the job | Search is synchronous and user-facing; moving 8 minutes there is worse. |
| Raise the §8 timeout | Weakens the backstop the timeout exists to be. |

Wants a decision. **Decimation 8 is a materially safer trade than it was** now that a coarser
sample degrades to "inconclusive, warn and use metadata" rather than to a wrong answer — but it
would push Delhi-2022-04-19-like scenes onto a metadata fallback that is wrong for exactly that
scene, so it is not free.

### The recovery story was wrong. Fixed 2026-07-31.

This section said the timeout was "recoverable by resubmitting". **It was not.** Watching one
happen during the §7.6 work:

```
11:37:41  run_job(05142cb9…) starts        — NDVI on an uncached scene
11:48:41  killed horse pid 56
          Work-horse terminated unexpectedly; waitpid returned None
```

`tasks.py` records `timed_out` by catching RQ's `JobTimeoutException`, which RQ raises **inside**
the job. That only works while the job is running Python. Offset detection blocks inside GDAL's
HTTP read, and a Python signal handler cannot run during a C call — so RQ waited, gave up, and
**SIGKILLed** the work-horse. The process died with no chance to record anything, and the row
stayed at `reading` permanently.

That is worse than a failed job. `count_active` counts `reading`, so the client sat at its
one-job cap (§8) and **could never submit again** — not recoverable by resubmitting, not
recoverable at all without editing the database by hand. Which is exactly what had to be done to
carry on testing.

**Fixed with a reaper**, because the failure *is* the absence of the process that would have
reported it — nothing in-process can cover that, so something outside has to notice the silence.
`JobStore.reap_stalled` fails any active job whose `COALESCE(started_at, created_at)` is older
than `BHOOMI_STALLED_AFTER` (default 1500 s), and `create` runs the same statement inside the
advisory lock, in the same transaction as the count it corrects — so a ghost is cleared exactly
when it would otherwise block someone, and two concurrent submissions cannot see different
answers.

1500 s is deliberately well above the 10-minute limit: a job at 9 min 50 is *slow*, not dead, and
reaping a live one would mark a result failed while it was still being computed. Tested both
ways — a 3600 s-old ghost is reaped and stops blocking; a 590 s-old job is left alone and still
blocks, so the reaper cannot become a way around the concurrency cap.

---

> **UX gap found 2026-07-31.** The change picker offers only scenes from the *current* search,
> and §8 caps a search at 366 days. So the flagship 2020↔2026 comparison **cannot be expressed in
> the web interface at all** — only through the API. Either the picker needs its own search, or
> the date cap needs to not apply to it.
>
> ✅ **Fixed 2026-07-31 — the picker got its own search.** Of the two options, this is the one
> that costs nothing: §8's cap exists to bound *catalogue query cost*, and it still does, because
> each of the two searches is independently capped at 366 days. Only the interval between them is
> free — which is the thing that was never meant to be limited. Relaxing the cap instead would
> have widened every search to buy one feature.
>
> The comparison window is seeded from the primary one with the year moved back and **day-of-year
> preserved**, because §5.4.4 wants both dates in the same part of the year; the user can edit it.
> It searches at a looser cloud limit than the primary panel (40 % against 20 %): a second date is
> scarcer than a first, and a partly cloudy scene the user can see and reject beats an empty list —
> the valid-pixel fraction on the result then says what masking actually cost.
>
> Verified end to end against the running stack: a 2020..2026 *single* search is still refused
> (HTTP 400), while the pair drawn from two separate searches submits (HTTP 202) and completes.
> On the D13 AOI the result reproduces the recorded demo figures exactly — loss 9.747 %, gain
> 3.264 %, asymmetry 2.99, mean −0.0274 — which also confirms §5.3.1c's detector replacement did
> not move them.

## 5.4 The four analyses

All four share one pipeline; only the band selection and the arithmetic differ.

```
resolve assets from STAC item
        ↓
read_window() each required band + SCL, to a common grid   (§5.1)
        ↓
apply SCL mask → NaN                                        (§5.2)
        ↓
harmonize DN → reflectance                                  (§5.3)
        ↓
compute index, with a guarded denominator                   (§5.4.x)
        ↓
clip to AOI geometry exactly (rasterio.mask)
        ↓
write COG                                                   (§5.6)
```

### 5.4.1 NDVI — vegetation

```
NDVI = (NIR − RED) / (NIR + RED)          bands: B08, B04     res: 10 m
```
Range −1..1. Dense vegetation > 0.6; bare soil ~0.1–0.2; water negative.

### 5.4.2 NDWI — surface water (McFeeters)

```
NDWI = (GREEN − NIR) / (GREEN + NIR)      bands: B03, B08     res: 10 m
```
Water positive. Note in the docs that this is McFeeters' NDWI (water extent), not Gao's
NIR/SWIR formulation (vegetation water content) — reviewers notice.

### 5.4.3 NDBI — built-up

```
NDBI = (SWIR − NIR) / (SWIR + NIR)        bands: B11, B08     res: 20 m
```
Per D4, computed at 20 m. Built-up positive. Known weakness: NDBI confuses bare soil with
built-up; state this as a limitation rather than letting a reviewer find it.

### 5.4.4 Two-date change detection

```
Δ = INDEX(scene_B, date_B) − INDEX(scene_A, date_A)
```

Requires more care than the other three:

- **Both dates must land on the identical output grid.** Choose a target grid from the AOI:
  the UTM zone containing the AOI centroid, resolution per D4, origin snapped to a multiple of
  the resolution. Reproject both scenes to *that* grid, not to each other.
- **Union the masks.** A pixel is valid in the difference only if valid in *both* dates.
- **Same index, same parameters** on both sides — enforce this in the API, do not trust input.
- **Seasonality is a confound.** NDVI differs between March and September for reasons that
  have nothing to do with land-use change. Prefer date pairs from the same month across years,
  and surface the acquisition dates prominently in the UI so the user cannot misread a seasonal
  swing as urban expansion.
- **Never report SCL class counts as a change metric.** See the measurement below.

### Measured on the demo AOI, 2026-07-30 — and it changes what the demo may claim

Running both cross-checks over New Town/Rajarhat (`probes/verify_change.py`):

| Metric | 2020 | 2026 | Change |
|---|---|---|---|
| **SCL class 4** (vegetation) | 27.67 % | 9.45 % | **−18.2 pp — a −66 % relative collapse** |
| **NDVI > 0.4** (direct band ratio) | 40.71 % | 34.13 % | **−6.6 pp — a −16 % relative decline** |

**SCL overstates the loss by roughly 4×.** SCL is a *classifier*, and its version changed with
the processing baseline (Sen2Cor 05.00 → 05.12), so the class-4/class-5 decision boundary moved
underneath the comparison. NDVI has no classifier in the loop — it is arithmetic on two bands —
and it is the defensible metric.

**But real change is present, and this is how we know:** the loss is *asymmetric*. **9.73 %** of
pixels lost more than 0.2 NDVI while only **3.26 %** gained more than 0.2 — a 3:1 ratio.
Calibration drift or noise would move both directions roughly equally. Dense vegetation
(NDVI > 0.6) nearly halved, 15.75 % → 8.53 %. Water held steady at 9.36 % → 9.26 %, which is a
useful control: a global processing artifact would have shifted the wetlands too, and it did not.

> **Narrowed 2026-07-31.** The sentence above — "calibration drift or noise would move both
> directions roughly equally" — no longer carries the weight it was given. Measuring the spatial
> structure of *both* masks showed the gain is **18× more clustered than chance**, against the
> loss at 7×. Spatially organised movement is not noise, so the 3:1 compares two real signals of
> different size rather than signal against noise. **The conclusion survives; this particular
> route to it does not.** What still supports it: the water control in the same sentence, and the
> geometry described below. See the run record later in this document.

**Rules this imposes:**
1. The flagship demo reports **NDVI change**, never SCL class counts.
2. If the UI ever shows SCL statistics, it must state the baselines and warn on mismatch.
3. Report the loss/gain asymmetry alongside the mean — the mean NDVI shift is only −0.027, which
   understates a real change that is concentrated in a minority of pixels.
4. `docs/limitations.md` carries this comparison verbatim. Showing that we *checked* the
   headline number and found it inflated is worth more than the headline number.

### Spatial structure — the strongest evidence, added 2026-07-30

Rendering the loss mask (`examples/render_results.py`, `outputs/ndvi_loss_mask.png`) shows the
8.77 % of pixels that lost > 0.2 NDVI are **not uniformly distributed**:

- The western half of the AOI — the already built-up city and the Hooghly corridor — is almost
  blank. You cannot lose vegetation where there was none.
- Loss concentrates in the **eastern half**, as a dense granular scatter in the north-east plus
  **rectilinear, parcel-shaped patches** in the centre and south, several arranged in rows.
- Thin **linear features** trace road and canal alignments.

This matters more than the summary statistics. Sensor drift, atmospheric mis-correction or
Sen2Cor version change would speckle the loss **uniformly** across the frame. What appears
instead is geographically coherent and follows human geometry — parcels, roads, a development
front advancing eastward. That is the signature of real land-cover change.

### The limitation NDVI cannot resolve alone

Those geometric parcels are ambiguous: NDVI cannot distinguish **"field harvested"** from
**"field built on"**. Both take a vegetated pixel to a bare one. Cropping calendars shift
year to year even between two early-March dates.

**NDBI helps but does not fully resolve it** — an earlier draft of this section claimed
"NDVI falls + NDBI rises → construction". That is too strong. NDBI's known weakness is that it
**cannot separate bare soil from built-up**, and a harvested field *is* bare soil. So:

| Transition | NDVI | NDBI |
|---|---|---|
| vegetation → building | falls | rises |
| vegetation → bare soil (harvest/fallow) | falls | **also rises** |

Both produce the same two-index signature. NDBI upgrades the claim from "vegetation left" to
"the surface is now bare or impervious" — real progress, but not proof of urbanisation.

**Measured on the demo AOI, 2026-07-30** (`examples/kolkata_ndbi.py`, 20 m grid):

| NDVI group | share of AOI | mean ΔNDBI |
|---|---|---|
| lost > 0.2 NDVI | 8.16 % | **+0.1663** |
| stable (\|Δ\| ≤ 0.05) | 44.17 % | +0.0227 |
| gained > 0.2 NDVI | 2.19 % | **−0.0675** |

The relationship is cleanly monotonic, which is exactly the expected physical signature. AOI
median NDBI crossed zero, −0.030 → +0.005, and NDBI rose > 0.1 on 18.56 % of pixels against
4.02 % falling — a 4.61 : 1 asymmetry.

**The stable group is a free drift calibration.** If Sen2Cor version change were inflating NDBI
globally, it would show up there — and it does, at about **+0.02**. The loss group sits at
+0.166, roughly **7× above that floor**. So the signal clears its own noise estimate by a wide
margin. Build this control into the product: report ΔNDBI for NDVI-stable pixels alongside any
change result, as an empirical drift estimate for that specific scene pair.

### Settled by a 7-year series, 2026-07-30 — and it shrinks the claim again

Construction is permanent; harvest reverts. `examples/kolkata_timeseries.py` pulls the
lowest-cloud pre-monsoon scene for each year 2020–2026 (all 0.000 % cloud) and asks, for every
pixel in the 2020→2026 loss zone, how many intermediate years returned to within 0.1 of the 2020
value. A building never re-greens; a crop field does so most years.

| Result | Value |
|---|---|
| **Never recovered** — construction-like | **16.64 %** of loss pixels |
| Recovered at least once — crop-like | **83.36 %** |
| Control: stable zone never-recovered | 0.15 % |

**So most of the apparent vegetation loss is agricultural cycling, not urbanisation.** Permanent
conversion is **3.70 km² of a 256.9 km² AOI — 1.44 %.**

The control matters: only 0.15 % of NDVI-*stable* pixels fail the recovery test, so the test is
not manufacturing positives.

**Year-effect normalisation was necessary.** Whole-scene NDVI varies year to year (the stable
zone ranged −0.045 to +0.027 around 2020), and the raw onset breakdown attributed 69.8 % of
conversion to 2021 largely because 2021 was a low year overall. Normalising each year by the
stable-zone mean drops that to 58.4 %. **Always normalise a multi-date threshold by a
same-scene control region**; an absolute threshold silently absorbs the year effect.

| Onset year | Share of permanent conversion |
|---|---|
| 2021 | 58.4 % |
| 2022 | 21.4 % |
| 2023 | 10.3 % |
| 2024–2026 | 9.9 % |

### The chain of shrinking claims — this is the project's real story

| Method | Claimed loss |
|---|---|
| SCL class counts | −66 % vegetation |
| NDVI, two dates | −16 % relative |
| NDVI + NDBI, two dates | "85.9 % of loss is construction-like" |
| **NDVI, 7-year recovery test** | **16.6 % of loss is permanent = 1.44 % of AOI** |

Each step cut the headline by roughly 4×, and each cut came from a control the previous step
lacked. The final number is small — and it is the only one that would survive review.

`docs/limitations.md` should carry this table verbatim. A reviewer who sees a project *narrow*
its own claim four times under its own scrutiny learns more about the engineering than any
headline figure would tell them. This is also the honest argument for why Bhoomi computes rather
than displays: no viewer would have caught any of it.

## 5.5 Numerical hygiene

- Work in `float32`. `float64` doubles memory for no meaningful precision gain here.
- Guard every denominator: `np.where(np.abs(denom) < 1e-10, np.nan, denom)` — do not rely on
  `errstate` alone, and never let a divide-by-zero become `inf` in a written raster.
- Clamp index outputs to `[-1, 1]` after computation; values outside indicate a masking or
  harmonization bug and should be logged, not silently clipped in production without a counter.
- Nodata value in the COG: `-9999.0` (explicit), with `nodata` set in the profile so QGIS and
  TiTiler both honour it.

## 5.6 COG generation

```python
profile = {
    "driver": "COG",
    "dtype": "float32",
    "nodata": -9999.0,
    "compress": "DEFLATE",
    "predictor": 3,          # floating-point predictor
    "blocksize": 512,
    "overview_resampling": "average",
}
```

Use GDAL's `COG` driver (GDAL ≥ 3.1) or `rio-cogeo`. **Validate every output** with
`rio_cogeo.cog_validate()` before marking the job complete — an invalid COG will still open in
QGIS but will make TiTiler read badly, and the failure mode is "slow tiles" rather than an
error, which is hard to diagnose later.

Embed in the COG metadata: process name, source scene ID(s), acquisition date(s), index
formula, processing baseline(s) applied, valid-pixel fraction, Bhoomi version, generation
timestamp. A downloaded GeoTIFF should be self-describing — this is what makes it a research
product rather than a screenshot.

---

# 6. Data model

PostgreSQL 16 + PostGIS 3.4.

```sql
-- Cached STAC scene metadata. Cache, not source of truth.
CREATE TABLE scenes (
    id                BIGSERIAL PRIMARY KEY,
    external_id       TEXT NOT NULL,
    catalogue         TEXT NOT NULL,          -- 'earth-search' | 'bhoonidhi'
    collection        TEXT NOT NULL,          -- 'sentinel-2-l2a'
    satellite         TEXT,
    sensor            TEXT,
    acquired_at       TIMESTAMPTZ NOT NULL,
    cloud_cover       REAL,
    processing_baseline TEXT,                 -- recorded, but NOT trusted (§5.3)
    boa_floor_dn      DOUBLE PRECISION,      -- p0.1 of valid NIR DN; ~11 s to measure,
                                              -- so persist it: it is a property of the
                                              -- scene, not of the request (§8).
                                              -- The MEASUREMENT, not the verdict -- so a
                                              -- recalibration re-derives instead of
                                              -- purging (§5.3.1c, migration 0005)
    geometry          GEOMETRY(Polygon, 4326) NOT NULL,
    assets            JSONB NOT NULL,         -- band -> href
    properties        JSONB,
    cached_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (catalogue, external_id)
);
CREATE INDEX scenes_geom_idx     ON scenes USING GIST (geometry);
CREATE INDEX scenes_acquired_idx ON scenes (acquired_at DESC);

CREATE TYPE job_status AS ENUM (
    'queued','searching','reading','processing',
    'writing_cog','completed','failed','cancelled','timed_out'
);

CREATE TABLE jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process       TEXT NOT NULL,              -- 'ndvi'|'ndwi'|'ndbi'|'change'
    status        job_status NOT NULL DEFAULT 'queued',
    progress      SMALLINT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    aoi           GEOMETRY(Polygon, 4326) NOT NULL,
    aoi_area_km2  REAL NOT NULL,
    scene_ids     TEXT[] NOT NULL,            -- 1 for indices, 2 for change
    parameters    JSONB NOT NULL DEFAULT '{}',
    error_message TEXT,                       -- user-facing
    error_detail  TEXT,                       -- internal; never served
    client_ip     INET,                       -- rate limiting only, purge at 30 days
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ
);
CREATE INDEX jobs_status_idx  ON jobs (status);
CREATE INDEX jobs_created_idx ON jobs (created_at DESC);

CREATE TABLE outputs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id         UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    output_type    TEXT NOT NULL,             -- 'index_raster' | 'change_raster'
    cog_uri        TEXT NOT NULL,             -- URL, per D5 — never a local path
    bounds         GEOMETRY(Polygon, 4326) NOT NULL,
    crs            TEXT NOT NULL,             -- 'EPSG:32645'
    resolution_m   REAL NOT NULL,
    size_bytes     BIGINT,
    valid_fraction REAL,                      -- §5.2
    stats          JSONB,                     -- min/max/mean/stddev/histogram
    expires_at     TIMESTAMPTZ,               -- §8 cleanup
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Retention:** anonymous job outputs expire after **30 days**. A nightly task deletes expired
object-storage keys and their rows. Demo outputs are marked `expires_at = NULL` and pinned.

---

# 7. API specification

Base: `/api/v1`. JSON in, JSON out. All geometry is GeoJSON in EPSG:4326.

## 7.1 Health

```http
GET /health
→ 200 {"status":"ok","version":"0.4.0","queue_depth":3,"workers":1}
```

## 7.2 Scene search

```http
POST /api/v1/scenes/search
{
  "aoi": { "type":"Polygon", "coordinates":[[...]] },
  "start_date": "2026-01-01",
  "end_date":   "2026-01-31",
  "max_cloud":  20,
  "collection": "sentinel-2-l2a",
  "limit":      50
}
```
```jsonc
→ 200 {
  "count": 7,
  "scenes": [{
    "id": "S2A_45QXF_20260114_0_L2A",
    "collection": "sentinel-2-l2a",
    "satellite": "sentinel-2a",
    "acquired_at": "2026-01-14T04:36:21Z",
    "cloud_cover": 4.2,
    "processing_baseline": "05.11",
    "geometry": {...},
    "bbox": [...],
    "thumbnail": "https://…/thumbnail.jpg",
    "aoi_coverage": 1.0,        // fraction of AOI inside this scene — drives D3
    "available_processes": ["ndvi","ndwi","ndbi"]
  }]
}
```

`aoi_coverage < 1.0` means the AOI crosses a scene boundary. Per D3, the UI must surface this
and the job endpoint must reject it.

## 7.3 Create a job

```http
POST /api/v1/jobs
{
  "process": "ndvi",
  "scene_ids": ["S2A_45QXF_20260114_0_L2A"],
  "aoi": {...},
  "parameters": { "mask_snow": false }
}
```
```http
→ 202 { "job_id":"8f3e…", "status":"queued", "position_in_queue":2,
        "estimated_seconds":45,
        "links":[{"rel":"status","href":"/api/v1/jobs/8f3e…"}] }
```

For `"process": "change"`, `scene_ids` has exactly two entries (chronological) and
`parameters.index` selects which index to difference.

**Rejections** — `400` with a specific message, never a generic failure:

| Condition | Message |
|---|---|
| AOI exceeds cap (§8) | `"AOI is 1,240 km²; maximum is 500 km². Draw a smaller area."` |
| AOI crosses scene boundary | `"This AOI spans multiple scenes. Bhoomi V1 processes one scene at a time — reduce the AOI or pick a scene that fully contains it."` |
| Wrong `scene_ids` count | `"Process 'change' requires exactly 2 scenes; got 1."` |
| Rate limit | `429` + `Retry-After` |

## 7.4 Job status

```http
GET /api/v1/jobs/{job_id}
→ 200 { "job_id":"8f3e…", "process":"ndvi", "status":"processing",
        "progress":65, "message":"Computing index",
        "created_at":"…","started_at":"…","completed_at":null }
```

Frontend polls at 2 s while active. SSE is a nice-to-have, not P0.

## 7.5 Job result

```http
GET /api/v1/jobs/{job_id}/result
→ 200 {
  "job_id":"8f3e…",
  "outputs":[{
    "type":"index_raster",
    "cog":"https://cdn.bhoomi.…/outputs/8f3e….tif",
    "tiles":"https://tiles.bhoomi.…/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=…&rescale=-1,1&colormap_name=rdylgn",
    "download":"/api/v1/jobs/8f3e…/download",
    "bounds":[88.2,22.4,88.5,22.7],
    "crs":"EPSG:32645",
    "resolution_m":10,
    "valid_fraction":0.94,
    "stats":{"min":-0.31,"max":0.88,"mean":0.42,"stddev":0.19},
    "expires_at":"2026-12-30T00:00:00Z"
  }]
}
```
`404` while incomplete, with the current status in the body.

## 7.6 OGC API – Processes (February onward)

Implement **Part 1: Core**, async execution:

```
GET  /ogc/processes                       list processes
GET  /ogc/processes/{id}                  process description (inputs/outputs schema)
POST /ogc/processes/{id}/execution        → 201 + Location: /ogc/jobs/{jobId}
GET  /ogc/jobs                            job list
GET  /ogc/jobs/{jobId}                    status info
GET  /ogc/jobs/{jobId}/results            results
GET  /ogc/conformance                     declared conformance classes
GET  /conformance                         the same, on the path first published
```

This is a **thin standards-compliant façade over the same queue** — not a parallel
implementation. `/api/v1/jobs` and `/ogc/jobs/{id}` read the same `jobs` table.

Acceptance test for this feature: **a QGIS user, or a Python script using `owslib` or plain
`requests`, executes an NDVI process and loads the result — without opening the website.** If
that works, the standards claim is real.

> ✅ **Implemented and passing, 2026-07-31.** `examples/ogc_client.py` is that acceptance test
> written out: standard library only, no `requests` and no `owslib`. It constructs exactly one
> URL — the landing page — and reaches conformance, the process list, the input schema, the job
> and the raster by following link relations from there, polling the `self` link and fetching the
> result from the `results` document. Run against the stack it printed:
>
> **Corrected 2026-07-31 (second pass).** As first written it hardcoded `/conformance` and
> `/ogc/processes` rather than following links, so "knows no Bhoomi URL beyond the base" was
> not quite true — and the hardcoding hid a real defect. The landing page sits at `/ogc`, which
> makes `/ogc` the API root, so OGC API - Common puts the conformance declaration at
> `/ogc/conformance`; it was served only at `/conformance`, and `/ogc/conformance` returned 404
> to anything applying the standard's own path rule. Both paths answer now, the client discovers
> instead of assuming, and `test_conformance_sits_under_the_landing_page` pins it.
>
> ```
> 201 Created -> /ogc/jobs/4243ccf4-...   Preference-Applied: respond-async
> accepted 0% -> running 30% -> successful 100%
> ndvi: image/tiff; application=geotiff; profile=cloud-optimized
> wrote outputs/ogc_result.tif (951,148 bytes) — TIFF byte-order marker verified
> ```
>
> The raster is 519×449, EPSG:32645, 10 m, a valid COG with overviews, median **+0.3596** —
> identical to what the native route produces for that scene — carrying its provenance tags
> including `BHOOMI_BOA_OFFSET_BASIS = pixels`.
>
> **O5 resolved: hand-rolled.** `backend/api/routes/ogc.py` is ~400 lines against a second
> framework, its own config format and a second path to the database. The default in §3.2 was to
> hand-roll and nothing argued otherwise once the shared submitter existed.
>
> **The facade is literal.** Both doors call `backend/api/submit.py`; `/ogc` adds request and
> response shapes and nothing else. `tests/test_ogc.py` asserts the thing that would break if
> that ever stopped being true — the same job visible and identical through both APIs — plus that
> the AOI cap, the scene-count rule and the per-IP concurrency cap all apply through the
> standards door. A conformance class is declared **only** where it is implemented: no
> `sync-execute` (there is no synchronous mode), no `dismiss` (`DELETE /jobs/{id}` does not
> exist), no `callback`.
>
> **Two defects this work surfaced**, both fixed here and neither in the OGC layer itself:
>
> - **`/ogc/jobs` reads were on the wrong budget.** `is_poll` matched only `/api/v1/jobs`, so a
>   standards client polling its own job spent the 120/hour *search* budget while a native client
>   doing the identical thing spent the 1200/hour poll budget. Found by the acceptance script
>   crashing on a 429. The standard being ten times more expensive to use is the opposite of the
>   point.
> - **A killed work-horse left a job active forever.** See §5.3.2 — this one is serious.

---

# 8. Resource limits

Unbounded processing is the fastest way to take the deployment down. Every limit below is
enforced server-side and returns a specific error.

| Limit | Value | Rationale |
|---|---|---|
| Max AOI area | **500 km²** | ~5 M pixels at 10 m across 2 bands ≈ 40 MB float32 working set. Kolkata Metropolitan Area (~1,850 km²) exceeds this — the demo AOI is a *subset*, which is fine and honest. |
| Max output pixels | **50 M** | The real guard. Area alone ignores resolution; a 500 km² AOI at 10 m and at 20 m differ 4×. |
| Max date range (search) | 1 year | Bounds catalogue query cost. |
| Max scenes per search | 50 | Response size. |
| Job timeout | 10 min | Hard kill, status `timed_out`. |
| Concurrent jobs (global) | 2 | One VPS. Queue the rest — that is what the queue is for. |
| Concurrent jobs per IP | 1 | Prevents one client monopolising both workers. |
| Rate limit | 20 jobs / IP / hour | **Search implemented 2026-07-30** at 120/hour (`backend/api/ratelimit.py`): sliding-window log, in-memory, `Protocol` so Redis drops in for multi-worker. Jobs get the tighter 20/hour budget when they exist. `/health` and `/docs` are exempt — a health check returning 429 reads as an outage and orchestrators restart containers over it. **X-Forwarded-For is ignored unless `BHOOMI_TRUSTED_PROXY_HOPS` says a proxy of ours set it**; honouring it blindly would let any caller reset their own limit with one header. |
| Max output size | 200 MB | Refuse to write beyond this. |
| Output retention | 30 days | §6. Nightly cleanup. |
| Worker memory ceiling | 2 GB RSS | Container limit; OOM → `failed`, not a silent worker death. |

### Measured 2026-07-30 — the binding constraint is none of the above

`probes/benchmark_pipeline.py`, NDVI over four **non-overlapping** AOIs in tile 45QXF. (An
earlier version nested them around one centre, so each larger AOI reused GDAL-cached blocks and
one run finished in 0.1 s. Non-overlapping AOIs model what separate user requests actually cost.)

| AOI km² | Mpixels | seconds | peak RSS |
|---|---|---|---|
| 10.5 | 0.10 | 4.0 | 84 MB |
| 41.9 | 0.42 | 6.3 | 86 MB |
| 167.7 | 1.68 | 4.7 | 91 MB |
| **461.8** | **4.62** | **17.2** | **115 MB** |

Fit: **`seconds ≈ 3.2 + 2.8 × Mpixels`**. Cost is dominated by a fixed ~3 s of connection setup
and COG header reads, not by throughput — which is why the small AOIs look "slow" per pixel.

**Three limits are revealed as non-binding:**

| Limit | Value | Reality at the 500 km² cap |
|---|---|---|
| Job timeout | 10 min | job takes **~17 s**; the timeout allows ~21,000 km² |
| Worker memory | 2 GB | peak **115 MB** at ~6.8 MB/Mpixel |
| Max output pixels | 50 M | never binds at Sentinel-2 resolution — 500 km² is 5 Mpx at 10 m |

Keep all three: the timeout is a backstop for pathological cases (degraded network, retry
storms), the memory ceiling turns an OOM into a `failed` status rather than a silent worker
death, and the pixel cap becomes the operative guard only for finer data — 500 km² of 2.5 m
imagery would be 80 Mpx. But **stop describing them as the design constraint**, and the earlier
claim that "memory is the binding constraint" is withdrawn: it is off by more than an order of
magnitude.

**The actual constraint is output size and egress.** A 500 km² NDVI COG is ~20 MB; storage and
repeated TiTiler reads are what scale with AOI, not compute. The 500 km² cap stays for V1
because it keeps outputs small and the 30-day retention affordable — not because anything
technical prevents raising it. Revisit it against real R2 costs (D14), not against runtime.

**Two concrete numbers this produces:**

1. **`estimated_seconds` (§7.3)** = `3.2 + 2.8 × Mpixels` for a single index; roughly double it
   for two-date change, plus offset detection if uncached.
2. **Offset detection costs ~6.0 s per scene** — over a third of a maximum-size job, and more
   than the entire compute for a small one. It is a property of the scene, not of the request.

   **Persisted as of 2026-07-30** (`cache.py`). Measured across two separate interpreter
   processes: **8.08 s cold, 0.02 s warm** — a ~400× saving on the repeat, and the saving
   survives a worker restart, which the previous in-process dict did not.

   The backend is a `Protocol` with a JSON-file implementation. When the `scenes` table lands
   (§6), a `PostgresOffsetCache` implements the same three methods and the default changes;
   nothing else moves. `BHOOMI_CACHE_DIR=` (empty) selects the in-memory backend, which is what
   the test suite wants.

R6 (worker OOM at the cap) is **closed**: tested at the cap, 115 MB against a 2 GB ceiling.

---

# 9. Infrastructure and deployment

## 9.1 Services

| Service | Image / runtime | Notes |
|---|---|---|
| `frontend` | Next.js (node:22-alpine) | Static-ish; can also go to Vercel independently |
| `backend` | FastAPI + uvicorn (python:3.12-slim + GDAL) | |
| `worker` | Same image, `rq worker` entrypoint | 1 replica initially, scale to 2 |
| `postgres` | `postgis/postgis:16-3.4` | Named volume |
| `redis` | `redis:7-alpine` | Queue + rate limiting |
| `titiler` | `ghcr.io/developmentseed/titiler:latest` | Pin the tag, don't track `latest` in prod |
| `caddy` | `caddy:2` | TLS + reverse proxy |

`docker compose up` must bring up the complete environment from a clean clone, with seeded
demo data. This is a **December deliverable** (D6).

## 9.2 Hosting

Per §2.2, the backend needs a **static IPv4** to keep the Bhoonidhi path open. Target: a
4 GB / 2 vCPU VPS in an Indian or Singapore region, ~₹600–700/month (O3). The frontend may sit
on Vercel separately — it has no IP constraint and gets a CDN for free.

Object storage holds outputs: **Cloudflare R2** (D14, §9.4), for zero egress, because TiTiler
re-reads these COGs on every tile request.

## 9.3 Demo reliability

**Precompute the Kolkata demo outputs and pin them** (`expires_at = NULL`) before recording
anything. Serve them through a `/demo` route that needs no worker. A portfolio demo that
depends on a live worker responding within 90 seconds *will* fail during the one viewing that
matters — an interviewer with a shared screen, on conference wifi.

The live path stays available for anyone who wants to draw their own AOI. Both exist.

## 9.4 Object storage — O4 resolved, 2026-07-31 → **Cloudflare R2**

### What this project actually stores

Measured across seven real NDVI outputs on the deployment: **~41 KB per km²** at 10 m
(940 KB at 23 km², 1.37 MB at 33 km²). The §8 cap of 500 km² therefore lands at **~20 MB**,
which confirms §8's earlier estimate. With 30-day retention (§6), a realistic portfolio load of a
few hundred jobs a month at modest AOIs is **1–3 GB stored** — small enough that storage price is
not the decision.

**Egress is.** TiTiler re-reads the COG over range requests for every tile, and the entire point
of a portfolio project is that strangers look at it. Storage is a rounding error; transfer is not.

| Monthly egress | R2 | B2 | S3 (Mumbai) |
|---|---|---|---|
| 30 GB — a demo people visit | **$0** | ~$0.21 | ~$3.3 (≈₹290) |
| 500 GB — a demo that gets *attention*, or a scraper | **$0** | ~$4.9 | ~$55 (≈₹4,700) |

The expected-value gap is a few dollars. The **tail** gap is a bill roughly seven times the entire
₹700/month hosting budget in O3, arriving unannounced, because somebody hotlinked a tile URL or a
crawler walked the zoom levels. That asymmetry is the decision: R2 removes the tail rather than
pricing it.

### Why not the other two

**B2 is disqualified on latency, not price.** Its regions are US East, US West, EU Central and
Canada East — **no Asia-Pacific**, and the region is fixed at account creation and cannot be
changed. TiTiler issues many small, often sequential range reads per tile; 150–250 ms of RTT per
read from an India or Singapore VPS compounds into an unusable map. For a project whose stated
purpose is Indian EO, storing the outputs in Virginia is the wrong shape regardless of the bill.

**S3 wins on latency and loses on everything else.** `ap-south-1` is in Mumbai, which is the best
possible answer for a VPS in India — this is the one real argument against R2 and it should be
recorded as such. But AWS changed the S3 free tier in **July 2025**: new accounts get $200 of
credits for six months instead of a permanent free allowance. This project runs Aug 2026–Mar 2027
and is meant to *outlive* that as portfolio evidence. A bucket that silently begins billing
mid-project, owned by a student, is the wrong default — and a forgotten S3 bucket accruing egress
is a well-worn way to lose money quietly.

R2 also sits behind Cloudflare's CDN natively, so tiles can be cached at an Indian PoP. Getting
the same from S3 means adding CloudFront: more configuration, and egress billed anyway.

### Why deciding now is safe

**R2 speaks the S3 API.** The implementation is `boto3` and GDAL's `/vsis3/` with an endpoint
override in either case, and `backend/storage.py` is already a `Storage` Protocol with the local
backend behind it. Choosing wrong costs an endpoint, a key pair and a bucket name — not a
rewrite. That is what makes it reasonable to settle this on measured *sizes* plus published
*prices*, rather than waiting to benchmark a provider we would have had to sign up for anyway.

### The one thing not measured

**R2 read latency from an India/Singapore VPS.** It cannot be measured without an account, and it
is the single number that could argue for S3 instead. Two things bound the risk: the CDN sits in
front for tile reads, and the Protocol makes switching cheap. **Measure it once the account
exists** — a `probes/` script timing a windowed read of a 20 MB COG from the VPS — and reopen this
if it is bad.

### Implemented 2026-07-31, verified against MinIO

`backend/storage.py` gained `S3Storage` — named for the API, not the vendor, because R2, S3,
MinIO and B2 differ only by an endpoint and a key pair. `BHOOMI_S3_BUCKET` being set is what
switches outputs off local disk, the same way `BHOOMI_DATABASE_URL` switches the scene cache: one
setting to get wrong rather than two that can disagree.

Verified end to end with **MinIO standing in for R2** — which is not a compromise, because R2 *is*
the S3 API, and that is the whole reason D14 was safe to decide before benchmarking. A real NDVI
job: the COG landed in the bucket (941,730 bytes) and **not** on the local volume, `/download`
streamed it back byte-identical, TiTiler read it through `/vsis3/`, and the raster rendered in
the browser with no console errors.

**What that does not prove is anything about R2 itself** — latency, durability, or the free tier
behaving as documented. Those need the account. The gap is narrow and named.

Two details the S3 path forced:

- **`url_for` returns None for a private bucket, deliberately.** A presigned URL satisfies
  "fetchable" and then expires *inside* `outputs.cog_uri`, which lives 30 days (§6). So a private
  bucket has no stable public URL, `cog_uri` falls back to the API's `/download`, and that route
  streams the object through. Setting `BHOOMI_S3_PUBLIC_BASE_URL` (a bucket's public r2.dev
  address or a custom domain) restores the direct URL and lets the tile server drop its
  credentials entirely.
- **botocore's default checksum behaviour breaks R2.** Recent versions send checksum trailers
  that R2 rejects; `request_checksum_calculation="when_required"` keeps one client working
  against R2, MinIO and S3 alike.

### What this unblocks, and one prerequisite

Settling O4 is what makes the O3 deployment *safe*: §11 records that TiTiler is bound to
127.0.0.1 only because it currently reads a filesystem. Once `tile_source()` returns an https URL,
that exposure is gone rather than hidden, and the tile server can face the internet.

**Prerequisite:** R2 requires a payment method on file even for the free tier. Nothing is charged
under 10 GB storage / 1 M Class A / 10 M Class B operations per month — this project's expected
load sits an order of magnitude inside all three — but the card has to be added before the bucket
can be created.

---

# 10. Repository structure

```
bhoomi/
├── frontend/
│   ├── app/                     # Next.js app router
│   ├── components/              # AOIDrawer, SceneList, JobProgress, SwipeCompare
│   ├── lib/map/                 # MapLibre setup, draw controls, layer mgmt
│   └── lib/api/                 # typed client for §7
├── catalogue/                   # pure client, no web deps  [moved, see below]
│   ├── base.py                  # Scene, SearchQuery, Catalogue protocol
│   ├── earthsearch.py           # primary catalogue (D9)
│   └── bhoonidhi.py             # P2, download-path ingest (§2.2)
├── pipeline.py                  # composition: catalogue + processing
├── backend/
│   ├── api/
│   │   ├── routes/              # health, scenes, jobs, ogc
│   │   ├── deps.py
│   │   └── errors.py            # specific messages per §7.3
│   ├── db/                      # SQLAlchemy models, alembic migrations
│   ├── queue/                   # RQ setup, job enqueue
│   └── ogc/                     # OGC API – Processes façade
├── processing/                  # pure, importable, no web deps
│   ├── raster_utils.py          # read_window, grid alignment, reprojection
│   ├── masking.py               # SCL masking (§5.2)
│   ├── harmonize.py             # baseline offset (§5.3)
│   ├── indices.py               # ndvi, ndwi, ndbi
│   ├── change.py                # two-date differencing
│   └── cog.py                   # write + validate
├── tests/
│   ├── unit/                    # processing, no network
│   ├── fixtures/                # small synthetic GeoTIFFs
│   └── integration/             # API + queue, network-marked
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── data-sources.md          # licensing + attribution (§14)
│   ├── processing.md            # the science: formulas, masking, harmonization
│   └── limitations.md
├── docker-compose.yml
├── docker-compose.dev.yml
├── .env.example
├── LICENSE                      # Apache-2.0
└── README.md
```

**`processing/` must not import from `backend/`.** Keeping it a pure library means it is
unit-testable without a database, reusable from a notebook, and directly demonstrable to
someone who wants to see the science without running the stack.

### Deviation from the original layout, 2026-07-30 — `catalogue/` moved out of `backend/`

The first draft placed the catalogue client under `backend/catalogue/`. That was wrong for the
same reason `processing/` is top-level: a STAC client has **no web-framework dependency**, is
independently testable, and is useful from a notebook. Burying it under `backend/` would have
made the December API a prerequisite for using it.

The layering is now three flat levels, each importing only downward:

| Layer | Knows about | Depends on |
|---|---|---|
| `catalogue/` | what scenes exist, where their bands live | nothing but stdlib + shapely |
| `processing/` | masking, harmonisation, indices, COGs | numpy + rasterio |
| `pipeline.py` | how to combine them | both of the above |
| `backend/` | HTTP, jobs, persistence | `pipeline` |

`catalogue/` and `processing/` **do not import each other** — the only module that knows both is
`pipeline.py`. That is the seam the FastAPI worker calls in January, so the web layer adds HTTP
and nothing else. It is also what lets Bhoonidhi arrive later as a second `Catalogue`
implementation without touching any raster code.

---

# 11. Roadmap

Eight months, running alongside Minor Project-I and [[Avlokan]]. Effort is deliberately
uneven — light through autumn, heavy December–February.

---

## AUGUST 2026 — Fundamentals + de-risking

*Effort: light. Nothing is being built yet, but three things are being de-risked.*

**Learn:** raster vs vector · bands and spectral response · spatial resolution · CRS, EPSG,
UTM zones (Kolkata is 45N/45Q) · reprojection and resampling · GeoTIFF internals · Sentinel-2
mission and product levels (L1C vs L2A) · NDVI/NDWI/NDBI physical meaning.

**Do:**
- Install QGIS. Manually download one Sentinel-2 L2A scene over Kolkata. Open it, inspect the
  bands, compute NDVI with the raster calculator, and look at it. **You must see the number
  before you automate the number.**
- Repeat with a 2020 scene. Compare the NDVIs. Notice the offset problem from §5.3 in real
  data — this makes it concrete rather than a paragraph in a plan.
- Work through IIRS remote-sensing e-learning material.

**De-risk (the real work of this month):**
- [x] ~~Resolve **O1**~~ — done 2026-07-30. Earth Search: 200 in ~1.1 s from Kolkata, anonymous
      asset access, no VPN. → **D9**
- [x] ~~Resolve **O2**~~ — done 2026-07-30. **45 scenes under 10 % cloud over Kolkata in 2020,
      36 in 2026.** Demo pair identified and verified. → **D10, D11**
- [x] ~~Prove windowed HTTP reads~~ — done 2026-07-30, ahead of its November slot. 32 KB of a
      206 MB COG in 1.34 s, HTTP 206, valid TIFF header. **§2.1**
- [x] ~~Confirm the toolchain runs on this machine~~ — Python 3.14.6, `rasterio 1.5.0` cp314
      wheel confirmed, git, node present. → **D12**
- [ ] **Submit the Bhoonidhi access request.** *Nayan — only you can do this.* Record the date
      here; the 2027-01-15 deadline in §2.2 runs from it.
- [x] ~~Install **QGIS**~~ — 3.44.12 LTR installed 2026-07-30. **Docker Desktop** and standalone
      **GDAL** still outstanding (QGIS bundles GDAL, so the CLI is available via OSGeo4W shell).
- [x] ~~Confirm the SCL class table (§5.2)~~ — done 2026-07-30 against real pixels rather than
      the spec. Table correct, no unexpected class values. → **§5.2**
- [x] ~~Validate the demo AOI actually shows change~~ — done 2026-07-30. Real vegetation loss
      confirmed with a 3:1 loss/gain asymmetry, and SCL found to overstate it 4×. → **§5.4.4**

**Learn-by-doing, now with concrete targets:** open `S2A_45QXE_20200310_1_L2A` and
`S2B_45QXE_20260304_0_L2A` in QGIS, compute NDVI on each with the raster calculator, and
difference them. That is the flagship demo, done by hand, before any code exists. **You must
see the number before you automate the number.**

**Exit criterion:** ~~O1 and O2 answered in writing~~ ✅ — remaining: an NDVI raster of Kolkata
made by hand in QGIS that you can explain, and the Bhoonidhi request submitted with a date.

---

## SEPTEMBER 2026 — Rasterio, in public

*Effort: light. Minor Project-I is the priority.*

**Learn:** Rasterio (windowed reads, `WarpedVRT`, `rasterio.mask`) · NumPy masked arrays ·
Shapely · PyProj.

**Build — as a public repository, not a scratch script:**

```
input.tif → python → ndvi.tif
```

Grow it to a small CLI: read local bands, apply an SCL mask, harmonize, compute an index,
write a validated COG. This is `processing/` in embryo — the same functions, later imported by
the backend unchanged.

**Why public:** four months of learning with nothing shipped is the shape that decays. A
public repo with real commits from September costs nothing extra and is a fallback artifact if
December slips.

**Exit criterion:** ~~a CLI produces a COG that opens correctly in QGIS and passes
`cog_validate`.~~ ✅ **Met early, 2026-07-30.**

`processing/` is built as a library rather than a CLI — the same functions the backend will
import in January, with no web dependencies. Delivered:

| Module | Responsibility |
|---|---|
| `harmonize.py` | DN → reflectance, with the measured flag semantics (§5.3) |
| `masking.py` | SCL cloud/shadow masking, verified class table (§5.2) |
| `raster_utils.py` | `Grid`, AOI snapping, UTM selection, windowed reads local **or** HTTP |
| `indices.py` | NDVI/NDWI/NDBI, guarded denominator, clamping with a logged counter |
| `change.py` | two-date differencing, mask union, baseline/season compatibility warnings |
| `cog.py` | COG writing, validation, provenance tags |

**35 unit tests pass**, including the §5.3 regression test (identical physical reflectance in
both encodings → identical NDVI within 1e-6) and a test that *documents* the mishandled case
producing 0.538 instead of 0.778 while still sitting inside [−1, 1].

`examples/kolkata_change.py` runs the flagship demo end to end and reproduces the independently
measured numbers from `probes/verify_change.py`:

| Metric | Probe (no masking) | Library (masked, snapped grid) |
|---|---|---|
| 2020 median NDVI | 0.323 | 0.327 |
| 2026 median NDVI | 0.303 | 0.305 |
| NDVI > 0.4, 2020 → 2026 | 40.71 % → 34.13 % | 40.29 % → 33.37 % |
| loss : gain asymmetry | 2.98 : 1 | **3.46 : 1** |

Three valid COGs written and validated; 99.6 %/99.7 % valid pixels after masking.

> **The baseline warning fires on the flagship demo itself** — 05.00 vs 05.12. That is correct
> behaviour and must not be suppressed: even the best available pair carries some Sen2Cor drift.
> The 3.46:1 asymmetry is what makes the result defensible anyway, not the absence of drift.

**Remaining for September:** wire STAC search to the library so a scene ID alone drives the
pipeline (currently the example supplies item properties by hand). That is the October–November
work, so September is effectively banked.

---

## OCTOBER–NOVEMBER 2026 — STAC, and the reader abstraction

*Effort: low. Minor Project-I still first.*

**Learn:** the STAC spec — Catalog, Collection, Item, Asset, `bbox`, `datetime`, `properties`
· STAC API search (`POST /search`, CQL2 filters) · `pystac-client`.

**Build:**
- CLI: search a catalogue by AOI + date + cloud, print matching scenes as a table.
- **Wire the two together** — search → pick a scene → read its bands *over HTTP by URL* →
  produce an NDVI COG. This is `read_window()` (§5.1) doing its real job and is the single most
  important technical proof in the project. If windowed HTTP reads work, Bhoomi works.
- Benchmark it. Record how long a 100 km² NDVI takes end to end. That number sets the
  §8 timeout and the "estimated_seconds" in §7.3.

**Also in November:** resolve **O3** (hosting), provision the VPS, point a domain at it. Do
this before December so Milestone 1 deploys onto something that already exists.

**Exit criterion:** ~~one command takes an AOI + date range and returns an NDVI COG, reading
only the needed windows over HTTP.~~ ✅ **Met early, 2026-07-30.**

`examples/search_and_process.py` takes an AOI polygon and a date range and returns a validated
COG, with no scene ID or asset URL supplied by hand. Measured on the demo AOI: **19 scenes
matched**, the 45QXE candidates correctly reported **38.3 % coverage** and were rejected under
D3 while 45QXF reported **100 %**, and the resulting NDVI median was **0.324** against 0.327
from the hand-wired path. The 0.003 gap is expected and the new value is the better one — the
earlier path resampled twice (source → local clip → snapped grid) where this resamples once.

**Scene deduplication added the same day.** The search returned the same acquisition twice at
different baselines (`20200330_0_` at 02.14 and `_1_` at 05.00). Selecting on cloud cover alone
would choose between them effectively at random, and two such picks across dates would place a
Sen2Cor version change *inside* a change-detection result — the §5.4.4 hazard entering through
scene selection rather than through arithmetic. `deduplicate_by_acquisition()` now keeps one
scene per acquisition, preferring the newest baseline, and ranks baselines numerically so
`05.10` correctly outranks `05.09`.

**Benchmark done, 2026-07-30** (`probes/benchmark_pipeline.py`). `seconds ≈ 3.2 + 2.8 ×
Mpixels`; a maximum-size 500 km² NDVI job takes ~17 s at 115 MB peak RSS. Runtime and memory are
both non-binding by roughly two orders of magnitude — see §8. **October–November is complete.**

---

## DECEMBER 2026 — Milestone 1: live scene search

*Effort: heavy. Semester break.*

The first month with a website.

**Frontend:** Next.js + TypeScript + MapLibre GL · polygon draw/edit/delete with live area
readout and the §8 cap enforced client-side · date-range picker · cloud slider · scene results
list with footprints drawn on the map and thumbnails · clicking a scene highlights its footprint.

**Backend:** FastAPI · `GET /health` · `POST /api/v1/scenes/search` · PostGIS with the §6
schema and alembic migrations · scene metadata caching · structured logging.

**Infra (D6):** `docker-compose.yml` bringing up frontend + backend + postgres. **Deploy it.**

**Exit criterion:** a stranger opens a public URL, draws a polygon over Kolkata, picks a date
range, and sees real Sentinel-2 footprints. No processing yet — but it is live, and it is real.

### Progress, 2026-07-30 — API and frontend both working locally

| Piece | State |
|---|---|
| `GET /health`, `POST /api/v1/scenes/search`, `/docs` | ✅ built, 93 tests |
| Next.js + MapLibre, polygon draw/edit/clear, live area readout | ✅ built |
| Date range, cloud slider, scene list, footprints on map | ✅ built |
| `docker-compose.yml` (D6) | ✅ built — backend, frontend, postgres |
| PostGIS scene cache + alembic | ✅ built, 39 tests (16 against real PostGIS) |
| Public deploy | blocked on **O3** (VPS) |

Verified by driving the real UI: drew a 58.3 km² AOI over Rajarhat, searched, got 11 scenes with
footprints rendered and partial-coverage scenes dashed and labelled.

**The polygon draw is hand-written against MapLibre** rather than using a draw library — about
150 lines, no version-coupling risk, and V1 needs exactly one polygon. §28 applies.

### Deduplication had to move into the API — and its key was wrong

Using the UI immediately exposed what the API tests had not: the scene list showed the same
acquisition twice, at baselines 02.14 and 05.00, differing only in cloud cover by 0.1 pp. A user
choosing between those on cloud percentage is precisely how a Sen2Cor version change ends up
inside a change-detection result. `deduplicate_by_acquisition()` existed but was only wired into
`search_best()`, not into search. It now defaults on, with `deduplicate: false` to opt out.

Wiring it up then revealed the key itself was fragile. It keyed on exact timestamp plus bbox, and
**reprocessed versions can differ by one millisecond**:

```
S2A_45QXE_20200330_0_L2A   04:52:25.488000Z   baseline 02.14
S2A_45QXE_20200330_1_L2A   04:52:25.489000Z   baseline 05.00
S2A_45QXF_20200330_0/1     04:52:10.902000Z   identical -> collapsed correctly
```

So the 45QXF pair collapsed and the 45QXE pair did not, from the same overpass. The key now uses
**`grid:code` (`MGRS-45QXE`) and the timestamp truncated to the second** — the MGRS square is
also a better spatial identifier than bbox, which shifts between reprocessings as nodata masking
changes. Live result: 22 scenes → 11, all baseline 05.00.

**Both bugs were only visible from the running UI**, not from the API tests, which used fixtures
with tidy identical timestamps. Real catalogue data is untidy in ways fixtures are not.

### The scene cache stores scenes by identity, and refuses to answer searches

`scenes` (§6) is now live behind alembic, written through on every search. The tempting second
feature — answering the *next* search from PostGIS with `ST_Intersects` instead of calling STAC —
is deliberately not built, and the reason is worth recording because the query looks so
reasonable.

Knowing the table holds *some* scenes intersecting an AOI is not knowing it holds **all** of
them. Making that a cache hit needs a ledger of which (bbox, date-range, cloud) windows have
actually been fetched, plus an expiry policy per window. Without one, a hit silently returns a
subset, and the failure is invisible in exactly the case that matters most: a user picking the
two dates for a change-detection pair would be choosing from the scenes that happen to have been
cached, not the scenes that exist. A missing scene does not look like an error — it looks like a
date with no imagery. Against a ~1.1 s catalogue query (D9), that is a bad trade. Search-from-
cache needs the ledger first; it is not a `SELECT` away.

What the cache is *for* is January: `POST /jobs` receives `scene_ids` (§7.3) and the worker must
turn them into band hrefs. Verified against live Earth Search — 29 scenes searched, 29 rows
cached, and `store.get(id)` alone rebuilds a Scene whose `href("nir")` is the real S3 COG URL.

**Two things the upsert protects.** `boa_floor_dn` appears in neither the INSERT nor the
UPDATE column list, so a repeat search cannot overwrite an ~11-second pixel measurement (§5.3) with
NULL — the one column here that is expensive and not re-derivable from the catalogue. And the
footprint column being `GEOMETRY(Polygon, 4326)` means a MultiPolygon footprint is skipped with a
warning rather than aborting the batch it arrived in.

**Cache writes are allowed to fail quietly** — logged, not raised — because a failed write costs
latency on a later request and can never change an answer: everything served still comes from the
catalogue on the same request that wrote it. That is the *opposite* call from §5.3's, where the
mutable warning was the only signal that a served number was wrong, and the difference between
the two is the whole distinction. A Postgres outage degrades Bhoomi to its no-database
configuration, which is supported and tested, rather than taking search down.

### Alembic's `fileConfig` turns off the application's loggers

Caught by two tests that asserted a warning was logged and found `caplog` empty. `fileConfig`
defaults to `disable_existing_loggers=True`, so running a migration **in-process** silently
switches off every logger created before it — including `backend.db.scenes`. Harmless in the
normal path, where alembic is its own process, and invisible until something in-process runs a
migration and then wonders where its logs went. `env.py` now passes `disable_existing_loggers=False`.

Same shape as the §5.3 lesson: the failure was in the *reporting* channel, so nothing failed
loudly. Only a test that asserted on the log caught it.

### "Degrades gracefully" was false for 130 seconds

Found by the test suite being slow, not by a failing assertion. The two tests that point the
store at an unreachable host **passed** — and took 130 s each, turning a 5 s suite into a 4½
minute one. The cause was not the tests: `create_engine` had no `connect_timeout`, so an
unreachable database fell back to the OS TCP timeout.

Which means the degradation story in the section above was not true as written. Search *would*
have survived a Postgres outage, eventually — after every request blocked for two minutes, which
a user and a load balancer both read as "the API is down". Failing softly is worth nothing if it
fails slowly; the timeout is what converts an outage of the cache into an outage of only the
cache. Now 5 s, `BHOOMI_DB_CONNECT_TIMEOUT`, with a test that asserts the failure arrives fast
rather than merely arriving.

**Worth noticing that a green test suite was the evidence.** Both tests asserted the right
behaviour and both passed. The only signal was the wall clock — which is exactly the signal that
gets ignored when a suite is run for its exit code.

---

## JANUARY 2027 — Milestone 2: the processing engine

**This is the make-or-break month.** Everything before it is preparation; everything after is
extension. If January lands, the project is a success even if February and March are thin.

**Build:**
- Redis + RQ, worker container, the §4.3 state machine with progress reporting.
- `POST /api/v1/jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/result`.
- Port the September/November `processing/` library in as-is — it should need no changes,
  which is the payoff for keeping it web-free.
- Object storage (O4) for outputs.
- TiTiler, wired to serve tiles from those COGs.
- Frontend: analysis picker → submit → live progress → result renders as a tinted layer with a
  colormap and a legend.
- **All §8 limits enforced**, with the specific error messages from §7.3.

**Order of work within the month:** queue plumbing with a fake 10-second job first, *then* real
processing. Debugging distributed job state and raster math simultaneously is how weeks
disappear.

**Exit criterion:** on the public deployment, draw a polygon → pick a scene → click NDVI →
watch progress → see the processed raster on the map. End to end, by a stranger.

### Progress, 2026-07-31 — the plumbing, with nothing in the pipe

| Piece | State |
|---|---|
| `jobs` + `outputs` tables, migration 0002 | ✅ |
| Redis + RQ, worker container, §4.3 machine with progress | ✅ |
| `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/result` | ✅ |
| §7.3 rejections, §8 caps (AOI, scene count, concurrency, 20/hour) | ✅ |
| **NDVI / NDWI / NDBI as real processes, COG out** | ✅ — see below |
| Object storage (D14 — R2; **implemented**, awaiting a real bucket) | partly |
| **Two-date change detection** | ✅ — see below |

The only registered process is **`fake`**: it sleeps ~10 s, walks all five stages, and produces
no raster. Submitting `ndvi` today returns `400 Unknown process 'ndvi'. Available: fake.` That
is deliberate — accepting it and reporting `completed` with nothing behind it would put a lie in
the status field, which is the one field the whole design asks the user to trust.

Verified with a worker in a separate process: submit → `202` + `Location` → poll → all six
states observed in order (`queued, searching, reading, processing, writing_cog, completed`) →
`200` with `outputs: []`. The empty array is the honest answer, not a placeholder.

> **Since 2026-07-31, `fake` is registered but unlisted** (`ProcessSpec.public=False`). Once the
> real indices existed it was still being advertised by `/ogc/processes`, so a client discovering
> the server saw `change, fake, ndbi, ndvi, ndwi` with nothing to distinguish the one that
> computes nothing — and the same list appeared in the `available` array of every
> unknown-process error. It stays in the registry, stays executable, and the delivery tests still
> submit it; it is simply no longer offered. `names()` returns the public list, and
> `names(include_hidden=True)` the whole registry for diagnostics.

**The state machine is enforced in SQL, not in Python.** `advance` puts the legality check in
the UPDATE's `WHERE` clause, so when two workers race, the database picks the winner and the
loser updates zero rows and is told. A `SELECT` then `UPDATE` would let both believe they won.
The case that matters: a retry landing after a success would move a `completed` job back to
`processing`, and nothing would ever move it again.

**Concurrency caps needed a lock, not a count.** `count active, then insert` is check-then-act:
two submissions arriving together both see one active job, both decide there is room, and both
insert — defeating precisely the limit being enforced. Count and insert now share a transaction
behind `pg_advisory_xact_lock`.

**`client_ip INET` rejects `request.client.host`.** That value is whatever the ASGI server
reports, and it is not always an address — a test client says `testclient`, a unix-socket
deployment has no peer, a misconfigured proxy can put a hostname there. Postgres rejected it and
turned every submission into a 500: a crash caused by *how the client connected*, not by
anything it asked for. Non-addresses are now stored NULL, which also switches off the per-IP cap
for that caller — a limit cannot be applied to an identity we do not have — while the global cap
still bounds the box.

**The worker cannot start on Windows.** RQ's default worker runs each job in a forked child, and
`os.fork` does not exist there, so `python -m backend.queue.worker` failed on the dev machine
while working in the container. It now selects `SimpleWorker` when fork is absent, with the
tradeoff stated in the code: jobs run in-process, so a GDAL segfault takes the worker with it,
and the timeout is a timer rather than a killed child. Development only; the containers fork.

**Redis carries no persistence and is not asked to.** The `jobs` table is the source of truth
(§6), so a lost queue costs in-flight work — which the state machine already has to survive —
rather than history.

### Progress, 2026-07-31 — real NDVI, end to end on the deployment

`ndvi`, `ndwi` and `ndbi` are registered processes calling `pipeline.compute_index`. Verified on
the compose stack against live Sentinel-2: submit → poll → `completed` in **11 s** → a
940 KB Cloud-Optimized GeoTIFF over `/download`.

```
scene   S2C_45QXF_20260227_0_L2A   AOI ~25 km2, New Town / Rajarhat
ndvi    median +0.332   range -0.295 .. +0.811   valid_fraction 0.99998
COG     EPSG:32645, 10 m, 519x449, tiled 512, deflate, overviews [2]
        validate_cog -> (True, []),  nodata -9999 declared, 4 pixels masked
tags    BHOOMI_BOA_OFFSET_PRESENT=False  BHOOMI_SOURCE_SCENES=S2C_45QXF_20260227_0_L2A
```

**§11's prediction held.** "Port the `processing/` library in as-is — it should need no changes,
which is the payoff for keeping it web-free." Nothing under `processing/` or `catalogue/` changed
to make the queue drive it. The only new code is the genuinely web-shaped part: publishing the
file and describing it for §7.5. The one edit to `processing/` was a *bug fix* found by running
it (§5.3.1), not an accommodation to the caller.

**Storage is a seam, not a decision (D5, O4).** `backend/storage.py` is a Protocol with a
local-filesystem backend; `cog_uri` is a URL in both cases, because LocalStorage returns None from
`url_for` and the caller falls back to the API's `/download` route. When O4 resolves, one class
changes. **LocalStorage is single-host by construction** — the worker writes and the API serves,
so compose gives them a shared volume. That constraint is the argument for settling O4 before the
deployment has more than one box.

**Two bugs that only a real output could expose**, both invisible through the entire fake-process
phase because `fake` produces no file:

- The named volume was **root-owned** while the container runs as uid 10001. Docker seeds a fresh
  volume from whatever the image has at that path, so the fix is to create the directory *in the
  image*, owned by the runtime user, before the mount.
- `/tmp` and the mounted volume are **different devices**, so `shutil.move` fell back to copying
  the whole COG. Storage now exposes `scratch_dir()` and the raster is built inside the
  destination filesystem, making the publish a rename.

`estimated_seconds` for an index is the §8 fit, `3.2 + 2.8 × Mpixels`, plus offset detection when
the scene has not been measured — omitting that term understates a first-time job several times
over.

### Progress, 2026-07-31 — the analysis picker, and January's exit criterion

The frontend now closes the loop: draw → search → pick a scene → pick a process → submit → live
progress → stats and a download. Driven in a real browser, not asserted from a test: 31.4 km²
over Rajarhat, 28 scenes, NDVI on `S2C_45QXF_20260108_0_L2A` finished with **median +0.468,
mean +0.447, 100 % valid**, EPSG:32645 at 10 m.

Polling is at 2 s per §7.4. A determinate bar, not a spinner: the server reports real progress
(§4.3), and a spinner would claim we do not know when in fact we are told. Failures show the
API's own message — those are written to be actionable ("this AOI is only 38 % inside scene X"),
so replacing them with "Processing failed" would discard the most useful thing the backend makes.

**The map draws the output extent, not the raster.** A dashed outline, because rendering pixels
needs the tile server. It earns its place anyway: the snapped output grid is slightly larger than
the drawn AOI, and seeing that is the difference between trusting the result and guessing.

**§11's exit criterion is met except for one clause.** "Draw a polygon → pick a scene → click
NDVI → watch progress → see the processed raster on the map" — everything but the last clause,
which is TiTiler. And "on the public deployment", which is O3.

### Progress, 2026-07-31 — TiTiler, and the last clause

The raster now renders on the map. Driven in a browser: NDVI over Rajarhat, 7 Feb 2026,
**median +0.542, mean +0.500, 99.7 % valid** — and the tiles show field boundaries, roads and
drainage, not a flat wash. A `Legend` gives the ramp, both ends and an opacity slider (verified
at 21 %, basemap showing through).

**Everything in §11's exit criterion now works except "on the public deployment" (O3).**

**Tiles are served from the volume, not through the API.** TiTiler reads the COG directly at a
path both containers mount. Routing tiles through `/download` would have been closer to the
eventual object-storage shape, but a single map view is dozens of tile requests and each one
would cost a database lookup and a poll-budget charge — the same collision §7.4 and §8 already
had once. `Storage.tile_source()` is the seam: a path today, an https URL when O4 lands, and the
tile server stops touching a filesystem.

> **TiTiler is bound to `127.0.0.1` deliberately.** It opens whatever path its `url` parameter
> names, so a publicly reachable tile server pointed at a filesystem is an arbitrary-file-read.
> That is acceptable on one box behind loopback and **not** acceptable on the O3 VPS. O4 removes
> the exposure rather than mitigating it. **Do not publish the tile server before O4 is settled.**

**Rescale is fixed at [−1, 1], not per-image.** TiTiler will happily stretch each tile to its own
min/max, which looks better and means less: two dates of the same AOI would get different scales,
so a visual comparison would measure the stretch rather than the ground. Normalised indices are
bounded by construction, so a fixed range is both honest and comparable — and it is what makes
February's swipe comparison mean anything.

**`raster-resampling: nearest`.** These are measured 10 m values; smoothing between them invents
intermediate readings that were never taken.

**The colour ramp is duplicated** in `frontend/components/Legend.tsx`, because the browser cannot
ask matplotlib what `rdylgn` looks like. That is a second hand-mirrored contract alongside
`lib/api.ts` — worth folding into the February OpenAPI generation.

**One bug:** the image serves on port **80** under gunicorn, not 8000 like the backend, so the
first mapping produced a container that was up and unreachable.

### Progress, 2026-07-31 — two-date change detection

`change` is a registered process taking two scenes and `parameters.index` (§7.3). Verified
against live Sentinel-2 over Rajarhat, 2020-03-10 → 2026-02-27:

```
mean -0.0878   median -0.0745   valid 0.9997
loss 19.19 %   gain 2.07 %   ->  asymmetry 9.3 : 1   (threshold 0.2)
COG  change_raster, EPSG:32645 @ 10 m, valid, tags carry BOTH baselines
```

**That number is not a finding, and the output says so.** The pair spans processing baselines
05.00 and 05.12, so §5.3 applies directly: part of that 9:1 is Sen2Cor version drift rather than
ground. The job ran, warned, and wrote the warning into `BHOOMI_WARNINGS` and both baselines into
`BHOOMI_PROCESSING_BASELINES`. §5.4.4's own measured figure — 3:1 on a *baseline-matched* pair —
remains the defensible one. A clean headline needs a matched pair, which is exactly what D11
picked and why.

**A mismatch warns rather than refuses**, per §5.3's "flag the mismatch". Refusing would make
whole year-pairs unusable for a confound a user may reasonably accept once told. The frontend
also warns *before* the job is spent, from the two scenes' baselines, so nobody discovers it
after waiting.

`_change_stats` reports loss and gain fractions and their ratio beside the mean, per §5.4.4
rule 3 — and maps an infinite ratio (loss with no gain at all) to null, because `inf` is not JSON
and the row would fail to insert.

The API enforces what §5.4.4 says not to trust to input: exactly two scenes, **two different**
scenes (differencing a scene with itself is zero everywhere — a valid answer to an invalid
question, which a user would read as "nothing changed"), and an index that can actually be
differenced.

### The seasonality check was measuring the wrong thing

Found by the first real change job warning when it should not have. `check_scene_compatibility`
compared **calendar month names**, which is wrong in both directions: 27 February and 10 March
are eleven days apart and warned, while 1 March and 31 March are thirty days apart and did not.

D11 already reasons correctly — it justifies the demo pair as "six days apart in day-of-year" —
so the plan's own working standard was never the month. The check now measures day-of-year
distance, circular so 31 December and 1 January are one day apart rather than 364, with a
tolerance of **21 days**: tighter than §5.4.4's "same month across years", because a same-month
pair can still be a full month of growth.

Same shape as §5.3.1's lesson, in miniature: a proxy stood in for the quantity actually being
reasoned about, and it went unnoticed until real data crossed the boundary.

### The demo pair, run through the stack — 2026-07-31

D11's pair over D13's AOI, submitted as an ordinary job rather than a probe script:

```
S2A_45QXF_20200310_1_L2A  ->  S2B_45QXF_20260304_0_L2A
251.5 km2, both 0.000 % cloud, 17 s, valid 0.9966

mean -0.02744   median -0.01297
loss  9.747 %   gain 3.264 %   ->  asymmetry 2.99 : 1
```

**This reproduces §5.4.4's measured figures to three significant figures** — the plan recorded
mean −0.027, loss 9.73 %, gain 3.26 %, ratio 3:1, measured on 2026-07-30 by
`probes/verify_change.py`. Reaching the same numbers through an entirely different path (queue →
worker → process registry → object storage) is the strongest evidence so far that the stack does
what the library does.

**It is not evidence that §5.3.1's detector fix was right.** Both of these scenes measured
1.279 % and 1.258 % dark fraction at the old decimation of 32 — comfortably above the 1 %
threshold — so the old detector classified them correctly too. The fix changed *other* scenes,
`S2C_45QXF_20260227_0_L2A` at 0.976 % among them. What this run does show is that the fix did not
perturb the demo, which is worth knowing separately.

### Spatial coherence, measured rather than asserted

§5.4.4 calls the spatial structure of the loss "the strongest evidence", on the argument that
sensor drift would speckle uniformly while real change follows human geometry. That had been
argued from looking at a rendered PNG. Measured on the change raster, as the fraction of adjacent
pixel pairs where **both** are in the mask, against the same pixels shuffled:

| mask | observed | shuffled control | clustering |
|---|---|---|---|
| loss > 0.2 | 6.590 % | 0.948 % | **7.0×** |
| gain > 0.2 | 1.938 % | 0.106 % | **18.4×** |

Loss is seven times more clustered than chance. That is the §5.4.4 claim, now a number.

**But gain is clustered too — more so.** That refines the asymmetry argument rather than
supporting it. §5.4.4 reasons that "noise moves both directions roughly equally", so a lopsided
ratio is evidence of real change. If the gain were noise it would be spatially unstructured, and
it is emphatically not: at 18× it is *more* spatially organised than the loss. So the 3:1 is not
signal against noise — it is **two real signals of different size**, one of which is presumably
cropping cycles, water and regrowth. The asymmetry still says something, but it says "more land
lost vegetation than gained it", not "the gain is measurement error".

That is a smaller claim than the one §5.4.4 makes, and it is the one the data supports.

**Three bugs, all found by using it rather than by testing it:**

- **§7.4 and §8 contradicted each other.** Polling at 2 s against a 120/hour budget is four
  minutes of polling before a user is locked out of the *whole API* — search included. It fired
  on the first UI run: a 429 for search while a job was still going. The search budget exists to
  stop Bhoomi amplifying requests at Earth Search; a status poll is one indexed local read.
  Polls now have their own 1200/hour allowance (`BHOOMI_POLL_LIMIT`).
- **`Worker.count()` undercounts.** Two workers running, health reporting one. Every worker runs
  `clean_worker_registry` at startup, so replicas starting together race and one prunes the
  other's registration. Membership does not affect consumption — both were taking jobs — but the
  number is what an operator reads. Counting live heartbeat keys instead trades a persistent
  undercount for a few seconds of overcount after a restart, which is the better error.
- **A four-column stats grid does not fit a 300 px sidebar.** It clipped the last value and gave
  the panel a horizontal scrollbar. Two columns, and `overflow-x: hidden` on the sidebar so no
  single wide child can do it again.

**And a correction to §5.3.1's cost.** Decimation 4 was recorded there as ~7 s from the host. In
the worker container, against a warm connection, it is **~11 s** against ~2.2 s at 8 — and an
*unwarmed* read has been seen at 159 s, with a first job at 65 s end to end. The 11 s is the
steady state and 4 still stands: it buys ~0.93 pp of margin against 8's 0.21 pp on the highest-risk
decision in the project, paid once per scene into a cache every worker shares. But
`OFFSET_DETECTION_SECONDS` was 6.0 and is now 11.0, because a UI that says "about 10 s" for a job
that takes a minute is worse than one that says nothing.

---

## FEBRUARY 2027 — Milestone 3: research-grade

**Analysis:** NDWI · NDBI (at 20 m, D4) · two-date change detection with the §5.4.4 grid and
mask discipline.

**Frontend:** before/after swipe comparison · layer opacity · colormap selection · legend with
real units · GeoTIFF download.

**Standards:** OGC API – Processes Part 1 Core (§7.6), and the acceptance test that matters —
**execute a process from a Python script with no browser involved.**

**Hardening:** error handling with specific messages · retry on transient catalogue failures ·
job cancellation · structured logging with job IDs · nightly cleanup of expired outputs ·
`processing/` unit tests including the §5.3 harmonization regression test.

**Exit criterion:** the Kolkata NDVI-change-2020-vs-2026 result renders in a swipe comparison,
and the same result is reproducible from a 15-line Python script hitting `/ogc`.

> **Swipe done 2026-07-31. The `/ogc` half is not.**
>
> **What it needed that was missing.** A difference raster cannot be un-differenced: +0.3 could be
> bare ground becoming scrub or forest becoming denser forest, and only the two dates tell them
> apart. A change job published *one* raster, so there was nothing to swipe between. It now
> publishes three — the difference, then `earlier_<index>` and `later_<index>` — which costs no
> extra computation, because `compute_change` already holds both index arrays when it forms the
> difference. It costs two more COG writes and two more objects under the 30-day retention.
>
> **Three consequences worth recording.**
>
> *`key_for` is no longer one key per job.* It takes an optional variant; `None` keeps the job id
> alone as the key, so every `cog_uri` already written stays resolvable. `?output=earlier|later`
> selects a side on the download route, against a **closed** set — the value reaches a storage key,
> and an unbounded one there is a path the caller chooses.
>
> *The colour ramp follows the raster, not the job.* Rendering an NDVI raster with the change ramp
> would put a healthy field in the brown "vegetation lost" half of the scale — wrong in the one way
> a ramp can be, by inverting the reading. `tiles.render_key` derives it from `output_type`, which
> is why the per-date outputs carry the index in their name.
>
> *A failed side must not lose the difference.* The two per-date rasters are published inside a
> `try`, because a job that threw away a completed analysis when a supplementary render failed
> would be trading the answer for a picture.
>
> **The swipe itself is two MapLibre instances**, cameras synchronised, the top one CSS-clipped to
> the left of a draggable handle. MapLibre cannot clip one layer against a screen-space line, and
> `raster-opacity` cross-fades rather than wipes — a cross-fade of two similar rasters shows
> nothing. So the divide has to happen outside the canvas, which means two canvases and a second
> set of basemap tiles. Keyboard-operable, because a drag-only control is unusable to anyone who
> cannot drag.
>
> **Verified against the running stack**, not just typechecked: the demo job returns three outputs,
> all three serve real PNG tiles from TiTiler at z11 (44 107 / 57 634 / 55 729 bytes — distinct
> rasters, not one file served thrice), the change raster renders `brbg` while both index rasters
> render `rdylgn`, all three share one 1762×1457 grid so the swipe registers pixel-for-pixel, and
> each carries its own scenes in `BHOOMI_SOURCE_SCENES`. `?output=bogus` and `?output=../etc/passwd`
> both return 400. Earlier NDVI median **0.3241**, later **0.3029** — the earlier figure matching
> the 0.323 recorded in §5.3 for that scene from an independent measurement. The change statistics
> are unmoved: loss 9.747 %, gain 3.264 %, asymmetry 2.99, mean −0.0274.

---

## MARCH 2027 — Milestone 4: portfolio release

**Data:** Bhoonidhi integration if O6 resolved by 2027-01-15; otherwise the §2.3 fallback,
documented honestly rather than quietly dropped.

**Documentation:** README answering the six questions in §15 · ~~architecture diagram~~
✅ [`docs/architecture.md`](docs/architecture.md), 2026-07-31 — five Mermaid diagrams, so they
render on GitHub and show up in a diff rather than going stale as a PNG nobody regenerates. Every
claim checked against the code: the dependency table is measured from real imports, and the state
machine was corrected after `allowed_transitions` disagreed with the first draft ·
~~API docs (FastAPI's OpenAPI plus prose)~~ ✅ [`docs/api.md`](docs/api.md), 2026-07-31 — the prose
half; `/docs` remains the generated half. Every request and response in it was captured from a
running stack rather than written from memory, and a check confirms every routed endpoint is
documented and every documented endpoint is routed
(FastAPI's OpenAPI plus prose) · ~~`docs/processing.md` explaining the science~~ ✅ [`docs/processing.md`](docs/processing.md),
2026-07-31 — every constant cross-checked against the code by a script · installation
from clean clone · `docs/limitations.md` — every known weakness, stated plainly.

**Demo:** precompute and pin the Kolkata outputs (§9.3). Record ~90 seconds:

> *"Urban and vegetation change in Kolkata, 2020 to 2026, from Sentinel-2 — processed on
> demand, served as a Cloud-Optimized GeoTIFF through OGC API – Processes."*

Show the swipe. Show the QGIS import. Show the Python client. Do not show the codebase.

**Exit criterion:** §15's definition of success, verified by someone other than you, on a
machine other than yours.

---

# 12. Priority ladder

When time runs short — and it will, in January — cut from the bottom. Never from the top.

**P0 — the project does not exist without these**
Map · AOI drawing · STAC scene search · Sentinel-2 · **SCL cloud masking** ·
**reflectance harmonization** · NDVI · async job queue · COG output · TiTiler tiles ·
live public deployment

> Masking and harmonization are P0, not polish. Without them the outputs are *confidently
> wrong*, which is worse than absent — a reviewer who spots it discounts everything else.

**P1 — makes it credible**
PostGIS caching · NDWI · NDBI · two-date change detection · before/after swipe ·
GeoTIFF download · Docker Compose · resource limits · error handling

**P1.5 — if January and February go well**
Multi-scene mosaicking (relaxing D3) · SSE progress instead of polling

**P2 — the research differentiator**
Bhoonidhi integration · OGC API – Processes · Indian EO demonstration · QGIS interoperability

**P3 — future, explicitly out of scope**
U-Net LULC (§16) · more satellites · workflow chaining · time-series

Do not sacrifice P0 to reach P2. A working NDVI pipeline with no OGC layer is a real project;
an OGC façade over a broken pipeline is a demo that fails under one question.

---

# 13. Risk register

| # | Risk | Trigger / check | Response |
|---|---|---|---|
| R1 | **Bhoonidhi access never arrives** | No working access by **2027-01-15** | Cut it. Fill P2 with §2.3 fallback. Document the constraint in the README — the *finding* is itself a legitimate result about Indian SDI accessibility. |
| ~~R2~~ | ~~No usable 2020 + 2026 Kolkata scene pair~~ | ✅ **Closed 2026-07-30** | 45 scenes < 10 % cloud in 2020, 36 in 2026. Pair fixed as D11. Risk did not materialise. |
| R3 | **Harmonization bug ships silently** | §5.3 regression test in CI from September | **Partially realised already** — the plan's own date-based fallback rule was wrong and would have caused this exact bug. Caught by probing real data on 2026-07-30, not by reasoning. Lesson: verify metadata assumptions against live items, never from documentation alone. |
| R4 | **January slips** | Not end-to-end by **2027-01-31** | Cut all of P1 immediately. NDVI-only, live, beats four indices in a branch. Move NDWI/NDBI to February. |
| R5 | **Aug–Nov learning decays with nothing shipped** | Check monthly: is there a public commit? | September's public repo is the mitigation. Enforce it. |
| ~~R6~~ | ~~Worker OOM on large AOIs~~ | ✅ **Closed 2026-07-30** | Tested at the 500 km² cap: **115 MB peak against a 2 GB ceiling**, ~6.8 MB/Mpixel. Runtime 17 s against a 10-minute timeout. The risk was real but the margin is two orders of magnitude; see §8. |
| R7 | **Minor Project-I collides with December–January** | Known deadline conflict | Aug–Nov are deliberately light for this reason. If it collides anyway, R4 applies. |
| R8 | **Cost overrun on VPS + storage** | Monthly billing check | ₹700/month cap (O3). R2's zero egress (O4) is the main defence, since TiTiler re-reads COGs continuously. |
| R9 | **Live demo fails during an interview** | — | §9.3 precomputed pinned demo. Non-negotiable before recording. |
| R10 | **Two projects compete** ([[Avlokan]]) | Ongoing | They target the same audience via different routes. If one must give, Bhoomi has the heavier engineering and the clearer standards story — but decide deliberately, not by drift. |

## 13.2 Backup project

If EO data access collapses entirely — Bhoonidhi gated *and* the Sentinel-2 path somehow
unworkable — pivot to **"Indian Geospatial Service Observatory"**: discover and monitor public
OGC services (WMS/WFS/WCS/CSW) across Indian government geoportals, checking availability,
latency, `GetCapabilities` validity, metadata completeness, supported CRS and formats, with a
30-day uptime dashboard.

Much less raster computation, no archive access needed, still squarely Web GIS and Spatial Data
Infrastructure. **Decision point: 2027-01-15**, same as O6. Do not run both.

---

# 14. Licensing, attribution, safety

- **Code:** Apache-2.0.
- **Sentinel-2:** Copernicus data, free and open under the Copernicus licence. Attribute:
  *"Contains modified Copernicus Sentinel data [year]."* Required wording — use it verbatim.
- **Bhoonidhi / NRSC:** review the current terms **before** any public deployment touches it.
  Do not assume redistributable. Prefer serving *derived analytical products* over
  redistributing source imagery — this is both safer and closer to the point of the project.
- **Attribution in three places:** the UI footer, the README, and the **COG metadata** — so
  attribution survives download.
- **Credentials:** environment variables only. `.env` in `.gitignore` from the first commit.
  Never in the frontend bundle. Never in logs.
- **Client IPs:** stored only for rate limiting, purged at 30 days, never served by the API.
- **`docs/limitations.md` is a feature.** NDBI/bare-soil confusion, seasonal confounds in
  change detection, single-scene constraint, cloud-mask imperfection. Stating limitations is
  what distinguishes a research prototype from a demo.

---

# 15. Definition of success

By **2027-03-31**, a stranger can:

1. Open the public Bhoomi site
2. Draw a polygon
3. Pick a date range
4. See matching Sentinel-2 scenes
5. Choose NDVI
6. Submit
7. Watch the job progress
8. See the processed raster on the map
9. Download a valid GeoTIFF that opens correctly in QGIS

**Research-grade success adds:**

10. Two-date change detection with a swipe comparison
11. OGC API – Processes execution from a script, with no browser
12. Bhoonidhi integration, *or* a documented account of why it was not possible
13. Outputs that are scientifically defensible — masked, harmonized, and honest about
    valid-pixel fraction

**The README must immediately answer:**

- What problem does Bhoomi solve?
- How is it different from a normal GIS viewer?
- How does the architecture work?
- Which geospatial standards does it implement?
- What data does it support?
- How does another developer run it?

## 15.1 Deliverables

Live site · public GitHub repo · README · architecture diagram · API docs · Docker Compose ·
screenshots · 90-second demo video · a worked example analysis · technical report

---

# 16. V2 — the research extension

**Explicitly not a V1 dependency.** This exists to make the internship/research pitch concrete,
not to be built by March.

**Proposal:** *Server-side semantic segmentation of Indian Earth Observation imagery through
OGC API – Processes.*

```
Sentinel-2 scene → preprocessing → U-Net → LULC raster → COG → OGC process
                                                                    ↓
                                              /processes/lulc-classification
```

Classes: water · vegetation · agriculture · built-up · bare land.

The value is not the U-Net — it is that Bhoomi already has a **process registry** with a
standards-compliant interface, so an ML model becomes just another registered process, callable
from QGIS or a script. That framing is the pitch:

> *"I built an open-source server-side EO processing prototype with an OGC-compliant process
> registry, and I want to extend it with machine-learning-based land-use classification for
> Indian Earth Observation imagery."*

Not *"I want to learn GIS."*

---

# 17. Development principle

> **Build the pipeline, not the UI.**

The valuable engineering is:

```
STAC search → spatial query → windowed raster read → CRS handling →
cloud masking → harmonization → index computation → COG → tile serving → OGC API
```

A plain interface over a rigorous geospatial backend beats a beautiful interface over a weak
one. Every hour spent on UI polish before January is an hour stolen from the only part of this
project that is hard to build and hard to fake.

---

*Plan v1.0 — 2026-07-30. Revise §3 in place as decisions resolve; do not let this document
drift out of date silently.*
