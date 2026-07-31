# Processing

The science, and the decisions inside it. What turns two satellite bands into a number you can
defend.

[`architecture.md`](architecture.md) covers where the code lives; [`api.md`](api.md) covers how to
call it; [`limitations.md`](limitations.md) covers what the results cannot tell you. This document
is the middle: **what actually happens to the pixels, and why each step is there.**

Everything here lives in `processing/`, which imports nothing else from this project. It runs in a
notebook with no server and no database, which is how every number in `limitations.md` was
produced and re-checked.

---

## The order of operations

```
AOI + scene
   │
   ├─ 1. build the output grid          snapped to the resolution, in UTM
   ├─ 2. read bands onto that grid      HTTP range requests, only this window
   ├─ 3. decide the reflectance scale   from the pixels, not the metadata
   ├─ 4. mask cloud and shadow          from the scene's own SCL band
   ├─ 5. compute the index              guarded against implausible output
   └─ 6. write a COG                    with provenance in the tags
```

**The order is not arbitrary.** Masking before the index rather than after means a cloudy pixel
never contributes to a ratio. Harmonizing before masking means the mask is applied to real
reflectance. And the grid comes first because every later step reprojects *onto* it rather than
onto each other — which is what makes two dates comparable pixel-for-pixel.

---

## 1. The grid

Output lands in the scene's UTM zone, at 10 m or 20 m depending on the index, with the origin
**snapped to a multiple of the resolution**:

```python
left = math.floor(left / resolution) * resolution
```

That one line is why two AOIs computed months apart land on the same pixel centres. Without it,
differencing two dates would compare pixels offset by a fraction of a cell — a misregistration
that looks exactly like change.

UTM rather than Web Mercator because area and distance matter here. Web Mercator's scale error
reaches 1.5× at 50° latitude; over Kolkata it is small but non-zero, and the AOI cap is stated in
km².

**Resampling follows direction.** Averaging when downsampling — which approximates what a coarser
detector would have measured — and bilinear otherwise. Bilinear interpolates four samples, which
is the wrong operation for aggregation: it invents a value where averaging summarises one.

---

## 2. Reading

Sentinel-2 tiles are ~1 GB. An analysis needs two or three bands over a few square kilometres, so
rasterio issues HTTP range requests against the COGs on AWS and reads only the bytes the window
covers. Nothing is downloaded.

A pixel of 0 is nodata and becomes invalid — the mask starts as the union of "no data in any band
this index needs".

---

## 3. Reflectance: DN → physical values

Sentinel-2 L2A ships integer digital numbers. Surface reflectance is:

```
reflectance = DN / 10000              (no offset)
reflectance = (DN - 1000) / 10000     (offset present)
```

From Processing Baseline **04.00**, ESA began applying `BOA_ADD_OFFSET = -1000`. Whether a given
product carries it **cannot be read from metadata** — three separate fields claim to answer and
all three have been observed wrong.

Getting this wrong does not crash anything. It shifts every value by ~0.24 while leaving
everything inside its valid range: no error, no obviously wrong picture, just a systematically
false answer.

**Bhoomi decides from the pixels, one-sidedly.** Reflectance cannot sit below about −0.02, so an
offset-bearing product cannot hold valid pixels below ~800 DN. Take the **0.1st percentile** of
valid NIR:

| floor | conclusion |
|---|---|
| below 800 DN | offset **absent** — proved |
| at or above 800 DN | **nothing follows** |

The second row is the important one. A high floor is equally consistent with an offset-bearing
product and with a bright tile that simply contains no dark target — no water, no shadow, no dense
canopy. Measured across 48 scenes, the classes overlap completely up there: offset-absent scenes
sit at 922–2048 DN and the one offset-*present* scene sits at 1003, inside that range.

So the pixel test proves "absent" or says nothing. Where it says nothing, the decision falls back
to metadata **and the output carries a warning**. The percentile matters too — the offset-present
scene holds exactly 2 valid pixels out of 7,535,025 below 800 DN, so a rule reading `min()`
misclassifies it on rounding-level outliers.

Full evidence in [`limitations.md`](limitations.md) and `PLAN.md` §5.3.1c. **This is the
highest-risk code in the project**, and it has been wrong twice.

---

## 4. Cloud masking

Not optional. An unmasked cloud is a bright, high-reflectance object that drags an index toward
whatever clouds happen to look like in those bands — and it does it silently.

Masking uses the **SCL** band shipped with every L2A product, a per-pixel scene classification:

| class | meaning | |
|---|---|---|
| 0 | no data | **masked** |
| 1 | saturated or defective | **masked** |
| 2 | dark area / cast shadow | **masked** |
| 3 | cloud shadow | **masked** |
| 4 | vegetation | kept |
| 5 | not vegetated | kept |
| 6 | water | kept |
| 7 | unclassified | kept |
| 8 | cloud, medium probability | **masked** |
| 9 | cloud, high probability | **masked** |
| 10 | thin cirrus | **masked** |
| 11 | snow / ice | **masked** by default |

**Water is kept, deliberately.** It is a real surface with a real index value — NDWI would be
meaningless without it, and NDVI over water is legitimately negative.

**Class 7 is kept, also deliberately.** "Unclassified" means the classifier declined, not that the
pixel is bad. Masking it would let a classifier's uncertainty silently shrink the sample.

**Snow masking is switchable** (`mask_snow: false`), because a snow study needs the snow.

Masked pixels become `NaN`, and **`valid_fraction` is recorded on every output**. A result that is
80 % cloud says so rather than rendering as a mostly-empty raster the reader misinterprets.

> A scene without an SCL band is processed **unmasked**, and the result carries a warning saying
> so. Refusing would be defensible; producing an unmasked raster that does not admit it would not.

---

## 5. The indices

All three are normalized differences — `(a − b) / (a + b)` — which is deliberate. The ratio is
insensitive to multiplicative effects that hit both bands equally: illumination angle, some
atmospheric attenuation, topographic shading. A plain difference would track brightness as much as
surface type.

| index | formula | bands | resolution |
|---|---|---|---|
| **NDVI** | `(NIR − Red) / (NIR + Red)` | B08, B04 | 10 m |
| **NDWI** | `(Green − NIR) / (Green + NIR)` | B03, B08 | 10 m |
| **NDBI** | `(SWIR16 − NIR) / (SWIR16 + NIR)` | B11, B08 | 20 m |

**NDVI** — chlorophyll absorbs red; leaf structure reflects NIR. The gap between them is the
signal. Dense vegetation > 0.6, bare soil ~0.1–0.2, water negative.

**NDWI** — water absorbs NIR strongly and reflects green comparatively well. Positive over open
water. It is a *water* index, not a moisture index: it responds to surface water, not to soil
moisture under vegetation.

**NDBI** — built surfaces are brighter in SWIR than in NIR; vegetation is the reverse.

**NDBI is computed at 20 m and not upsampled.** SWIR is a 20 m band. Resampling it to 10 m would
invent detail the sensor never recorded — the array would be four times larger and carry no more
information, while looking as though it did.

### The guard

`normalized_difference` clamps to [−1, 1], because L2A reflectance can be slightly negative over
dark targets and a handful of out-of-range pixels is physically ordinary.

**But if more than 1 % of finite pixels need clamping, it raises `ImplausibleIndexError`.** That
is a bug signature, not dark water. Both inputs are reflectances in roughly [0, 1], so the ratio
is mathematically confined to [−1, 1]; mass escaping it means the inputs are not what they claim
to be — nearly always a mis-harmonized offset.

This guard is the reason the harmonization bug of §5.3.1 surfaced as a failed job rather than as a
plausible-looking raster with a systematic bias. It fired on the first real scene it was pointed
at.

Two smaller pieces of hygiene:

- **The denominator is checked against `1e-10` and becomes NaN**, not infinity. `a + b` near zero
  means both bands are near zero — no signal — and a division producing ±inf would propagate into
  statistics and render as an extreme.
- **float32 throughout.** float64 would double memory for precision the sensor never had: 12-bit
  quantisation is ~3.5 decimal digits.

---

## 6. Change detection

```
change = index(later) − index(earlier)
```

Both dates are read onto **one grid derived from the AOI** — never onto each other. Reprojecting
date B onto date A's grid would resample it twice and bias the comparison toward whichever came
first.

The masks are unioned, which `NaN` gives for free: a pixel cloudy on either date is excluded from
both. The alternative — comparing a clear pixel against a cloudy one — produces change that is
entirely artifact.

**The pair is sorted chronologically** before differencing, so submitting the two ids either way
round gives the same sign rather than a flipped one.

### What is reported, and why not just the mean

| field | |
|---|---|
| `mean`, `median` | The central shift |
| `loss_fraction` | Pixels that fell more than `threshold` (default 0.2) |
| `gain_fraction` | Pixels that rose more than `threshold` |
| `asymmetry` | `loss / gain` |
| `valid_fraction` | After the union mask |

**The mean alone is misleading when change is concentrated.** On the demo AOI the mean NDVI shift
is only **−0.027**, while **9.75 %** of pixels lost more than 0.2 and **3.26 %** gained more than
0.2. A reader given the mean would conclude "almost nothing happened". A reader given the
fractions sees a real change in a minority of pixels.

### What the asymmetry does *not* say

This is worth stating plainly, because the project got it wrong first.

The ratio was originally presented on the argument that *noise moves both directions roughly
equally*, so a lopsided ratio is evidence of real change. **That argument does not hold.**
Measuring the spatial structure of both masks against a shuffled control:

| mask | observed clustering |
|---|---|
| loss > 0.2 | **7.0×** chance |
| gain > 0.2 | **18.4×** chance |

The gain is *more* spatially organised than the loss. Spatially organised movement is not noise —
so the ratio compares **two real signals of different size**, the gain presumably cropping cycles,
water and regrowth.

The asymmetry supports the plainer claim: *more of the area moved down than up*. It does not
support "the gain is measurement error".

### Compatibility warnings

Two conditions are flagged before the job runs and recorded on the output. Both **warn rather than
refuse** — refusing would make multi-year comparison impossible, which is the main thing anyone
wants this for.

**Processing baseline mismatch.** The baseline records when a product was *processed*, not when it
was acquired, and the archive is retrospectively reprocessed. Over the demo area 2020 is served
only at baseline 02.14 or 05.00 and 2026 only at 05.12 — **no matched pair exists across the
span**. Part of any long comparison is Sen2Cor version drift rather than ground change. There is
an empirical handle on its size: ΔNDBI over NDVI-*stable* pixels sat at about +0.02 while the loss
group sat at +0.166, roughly 7× above that floor.

**Seasonal separation.** NDVI differs between March and September for reasons that have nothing to
do with land use. Bhoomi warns when two acquisitions are more than **21 days apart in day-of-year**,
measured circularly so 31 December and 1 January are one day apart — an earlier version compared
month names, which made 1 January and 31 January "the same" and 31 January and 1 February
"different".

---

## 7. Output

Cloud-Optimized GeoTIFF: float32, DEFLATE with predictor 3, 512-pixel blocks, overviews built by
averaging, `NaN` written as **−9999** with nodata declared.

**Validated before the job is marked complete.** An invalid COG still opens in QGIS but makes a
tile server read badly, so the failure would surface later as "tiles are slow" — very hard to
trace back.

Provenance travels in the tags, so a file that has been moved still says where it came from:

```
BHOOMI_PROCESS               ndvi
BHOOMI_FORMULA               (nir - red) / (nir + red)
BHOOMI_SOURCE_SCENES         S2B_45QXF_20260304_0_L2A
BHOOMI_PROCESSING_BASELINES  05.12
BHOOMI_BOA_OFFSET_PRESENT    False
BHOOMI_BOA_OFFSET_BASIS      pixels
BHOOMI_VALID_FRACTION        0.9999
BHOOMI_GENERATED             2026-07-31T12:25:29+00:00
BHOOMI_ATTRIBUTION           Contains modified Copernicus Sentinel data
```

Read off a real output, not transcribed from the writer.

`BHOOMI_BOA_OFFSET_BASIS` is the one to read: `pixels` means the reflectance convention was proved
from the data, `metadata` means the pixels could not decide.

---

## 8. Reproducing any of it

`processing/` needs no server, no database and no queue:

```python
import rasterio, numpy as np
from processing import harmonize, indices, masking

with rasterio.open(nir_url) as src:
    nir = src.read(1).astype(np.float32)

evidence = harmonize.measure_offset_floor(nir)
decision = harmonize.resolve_offset(evidence, scene_properties)
reflectance = harmonize.to_reflectance(nir, scene_properties, decision.present)
```

`probes/` holds the scripts behind the empirical claims — including
`probes/verify_offset_rule.py`, which replays all 48 calibration scenes through the shipped
resolver and is a required CI job.

Every figure quoted here is traced to its source in
[`limitations.md` §7](limitations.md#7-where-these-numbers-come-from), including which have been
independently re-run and which have not.
