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

## 3.2 Open — with owners and deadlines

| # | Question | Decide by | How to decide |
|---|---|---|---|
| ~~O1~~ | ~~Earth Search vs Planetary Computer~~ | ✅ **Resolved 2026-07-30** → D9 | |
| ~~O2~~ | ~~`sentinel-2-l2a` vs `sentinel-2-c1-l2a`~~ | ✅ **Resolved 2026-07-30** → D10 | |
| O3 | Hosting provider for the fixed-IP VPS | 2026-11-30 | Needs ≥4 GB RAM (§8), a static IPv4, and Indian/Singapore region for latency. Budget cap: ₹700/month. |
| O4 | Object storage: Cloudflare R2 vs AWS S3 vs Backblaze B2 | 2026-12-31 | R2 has no egress fees, which matters because TiTiler will read these COGs repeatedly. Default to R2 unless testing says otherwise. |
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

1. `detect_offset_in_scene(band_url)` reads a **decimated overview of the full tile** — a few
   hundred KB — not the AOI window. A small AOI of uniformly bright bare soil has no dark pixels
   either way and would be misread; the 110 km tile reliably contains water or shadow.
2. Pixels take precedence. Metadata is retained **only as a cross-check that logs on
   disagreement**, so drift in the provider's conventions stays visible.
3. Fewer than 10,000 valid sample pixels → **fail**, do not judge the convention from a handful.

> **Open check:** `sentinel-2-c1-l2a` exposes no flag at all. The detector works there too, since
> it reads pixels — but measure before ever using c1-l2a.

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

That has a direct consequence for §5.4.4: **a change-detection job must not mix processing
baselines across its two dates**, or part of the "change" is Sen2Cor version drift. Record
`s2:processing_baseline` on every output (§5.6) and warn in the UI when the two scenes differ.

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
    processing_baseline TEXT,                 -- drives §5.3 harmonization
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
GET  /conformance                         declared conformance classes
```

This is a **thin standards-compliant façade over the same queue** — not a parallel
implementation. `/api/v1/jobs` and `/ogc/jobs/{id}` read the same `jobs` table.

Acceptance test for this feature: **a QGIS user, or a Python script using `owslib` or plain
`requests`, executes an NDVI process and loads the result — without opening the website.** If
that works, the standards claim is real.

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
| Rate limit | 20 jobs / IP / hour | Redis token bucket. |
| Max output size | 200 MB | Refuse to write beyond this. |
| Output retention | 30 days | §6. Nightly cleanup. |
| Worker memory ceiling | 2 GB RSS | Container limit; OOM → `failed`, not a silent worker death. |

**Memory, not area, is the binding constraint.** Windowed reads keep it bounded; the pixel cap
is what actually enforces it. Test at the cap before December, not after.

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

Object storage (O4) holds outputs; default to Cloudflare R2 for zero egress, because TiTiler
re-reads these COGs on every tile request.

## 9.3 Demo reliability

**Precompute the Kolkata demo outputs and pin them** (`expires_at = NULL`) before recording
anything. Serve them through a `/demo` route that needs no worker. A portfolio demo that
depends on a live worker responding within 90 seconds *will* fail during the one viewing that
matters — an interviewer with a shared screen, on conference wifi.

The live path stays available for anyone who wants to draw their own AOI. Both exist.

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

**Still outstanding for this milestone:** benchmark the runtime and use it to set the §8 job
timeout and the `estimated_seconds` field in §7.3.

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

---

## MARCH 2027 — Milestone 4: portfolio release

**Data:** Bhoonidhi integration if O6 resolved by 2027-01-15; otherwise the §2.3 fallback,
documented honestly rather than quietly dropped.

**Documentation:** README answering the six questions in §15 · architecture diagram · API docs
(FastAPI's OpenAPI plus prose) · `docs/processing.md` explaining the science · installation
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
| R6 | **Worker OOM on large AOIs** | Test at the §8 cap in December, not January | Pixel cap is the real guard. Lower it if testing says so — a smaller working limit is fine. |
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
