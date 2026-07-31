# Limitations

What Bhoomi cannot tell you, and where its headline numbers came from.

This document exists because of one result. The first vegetation-loss figure this project
produced was **−66 %**. The number that survived every subsequent control was **1.44 %**. Nothing
was wrong with the arithmetic at any step — each figure was the honest output of the method used
to get it. What changed was the method, four times, each time because a control was added that
the previous step lacked.

Showing that is worth more than the headline. A satellite analysis that produces a large number
and stops is indistinguishable from one that is wrong.

---

## The headline, narrowed four times

| Method | Claimed vegetation loss | What the next step added |
|---|---|---|
| SCL class counts | **−66 %** relative | SCL is a *classifier*, and its version changed between the two dates |
| NDVI, two dates | **−16 %** relative | NDVI cannot tell a harvested field from a built one |
| NDVI + NDBI, two dates | "85.9 % of loss is construction-like" | NDBI cannot tell bare soil from built-up either |
| NDVI, 7-year recovery test | **16.6 % of loss is permanent = 1.44 % of the AOI** | — |

Each step cut the headline by roughly 4×. The last number is small, and it is the only one that
survives scrutiny.

---

## 1. SCL class counts are not a measurement

The Scene Classification Layer is a *classifier* shipped with the product, and its behaviour
changed between the two dates — Sen2Cor 05.00 against 05.12. The class-4/class-5 decision
boundary moved underneath the comparison.

| Metric | 2020 | 2026 | Change |
|---|---|---|---|
| **SCL class 4** (vegetation) | 27.67 % | 9.45 % | **−18.2 pp — a −66 % relative collapse** |
| **NDVI > 0.4** (direct band ratio) | 40.71 % | 34.13 % | **−6.6 pp — a −16 % relative decline** |

**SCL overstates the loss by roughly 4×.** NDVI has no classifier in the loop — it is arithmetic
on two bands — and it is the defensible metric.

Bhoomi therefore never reports SCL class counts as a change metric. SCL is used only as a
*mask*, which is what it is fit for.

---

## 2. The loss/gain asymmetry is not a noise test

Over the demo pair, **9.75 %** of pixels lost more than 0.2 NDVI while **3.26 %** gained more
than 0.2 — a ratio of 2.99 : 1. (The first measurement of this, by a standalone script, gave
9.73 % and 3.26 %; the figures here are from re-running it through the job queue, and the two
agree to three significant figures.)

This was originally presented on the argument that *noise moves both directions roughly
equally*, so a lopsided ratio is evidence of real change.

**That argument does not hold, and the claim has been narrowed.** Measuring the spatial structure
of both masks — how often adjacent pixels are both in the mask, against the same pixels shuffled:

| mask | observed | shuffled control | clustering |
|---|---|---|---|
| loss > 0.2 | 6.590 % | 0.948 % | **7.0×** |
| gain > 0.2 | 1.938 % | 0.106 % | **18.4×** |

The gain is *more* spatially organised than the loss. Spatially organised movement is not noise,
so the ratio compares **two real signals of different size** — the gain presumably cropping
cycles, water and regrowth.

The asymmetry still supports "more of the area moved down than up". It does **not** support "the
gain is measurement error".

What does still control for a global artifact:

- **Water held steady**, 9.36 % → 9.26 %. A processing artifact would have shifted the wetlands
  too, and it did not.
- **The loss has human geometry.** It concentrates in the eastern half as rectilinear
  parcel-shaped patches, several in rows, with thin linear features tracing roads and canals.
  The already built-up western half is almost blank — you cannot lose vegetation where there was
  none. Sensor drift would speckle uniformly.

---

## 3. NDVI cannot distinguish "harvested" from "built on"

Both take a vegetated pixel to a bare one, and cropping calendars shift year to year even
between two early-March dates.

**NDBI helps and does not resolve it.** Its known weakness is that it cannot separate bare soil
from built-up — and a harvested field *is* bare soil:

| Transition | NDVI | NDBI |
|---|---|---|
| vegetation → building | falls | rises |
| vegetation → bare soil (harvest, fallow) | falls | **also rises** |

Both produce the same two-index signature. NDBI upgrades the claim from "vegetation left" to
"the surface is now bare or impervious" — real progress, not proof of urbanisation.

An earlier draft of this project claimed "NDVI falls + NDBI rises → construction". That was too
strong and has been withdrawn.

---

## 4. What settled it, and what that cost the headline

Construction is permanent; harvest reverts. Taking the lowest-cloud pre-monsoon scene for each
year 2020–2026 and asking, for every pixel in the loss zone, how many intermediate years
returned to within 0.1 of the 2020 value:

| Result | Value |
|---|---|
| **Never recovered** — construction-like | **16.64 %** of loss pixels |
| Recovered at least once — crop-like | 83.36 % |
| Control: stable-zone never-recovered | 0.15 % |

**Most of the apparent vegetation loss is agricultural cycling, not urbanisation.** Permanent
conversion is **3.70 km² of a 256.9 km² AOI — 1.44 %.**

The control matters: only 0.15 % of NDVI-*stable* pixels fail the recovery test, so the test is
not manufacturing positives.

**Year-effect normalisation was necessary.** Whole-scene NDVI varies year to year, and the raw
onset breakdown attributed 69.8 % of conversion to 2021 largely because 2021 was a low year
overall. Normalising each year by a same-scene stable-zone control drops that to 58.4 %. An
absolute threshold silently absorbs the year effect.

---

## 5. Limitations of the data itself

### Processing baselines cannot be matched across years

Sentinel-2 products carry a processing baseline recording *when they were processed*, not when
they were acquired. Over the demo area, 2020 is served only at baseline **02.14 or 05.00**, and
2026 only at **05.12**.

**No matched pair exists across a multi-year span.** Any long-baseline comparison mixes
processing versions by construction, and part of the measured change is Sen2Cor version drift.

Bhoomi does not refuse these jobs — refusing would make multi-year comparison impossible, which
is the main thing anyone wants it for. It warns before the job runs, records both baselines in
the output GeoTIFF's tags, and writes the warning into the file itself.

There is an empirical handle on the size of this drift: **ΔNDBI over NDVI-stable pixels** sat at
about +0.02 while the loss group sat at +0.166 — roughly 7× above the drift floor.

### Seasonality is a confound

NDVI differs between March and September for reasons that have nothing to do with land use.
Bhoomi warns when two acquisitions are more than 21 days apart in day-of-year, measured
circularly so that 31 December and 1 January are one day apart.

Prefer pairs from the same part of the year. The demo pair is six days apart in day-of-year.

### Reflectance harmonization is the highest-risk code in the project

From Processing Baseline 04.00, Sentinel-2 L2A applies `BOA_ADD_OFFSET = -1000`. Whether a given
product has it in its pixels **cannot be read from metadata**: three separate fields claim to
answer and all three have been observed wrong. Bhoomi detects it from the pixels.

Getting it wrong does not crash anything. It silently shifts NDVI by ~0.24 while leaving every
value inside its valid range.

The detector has now been wrong **twice**, both times found only by pointing it at data it had
not seen.

**First**, its threshold was calibrated at one sampling density and applied at another,
misclassifying four of eight scenes tested (`PLAN.md` §5.3.1).

**Second, and worse: the statistic itself was measuring the wrong thing.** It counted how many
pixels fell below a dark threshold — which works only where dark pixels exist. Widening the
sample from ten scenes on one wet tile to **48 scenes across 8 regions and 6 years** showed it
misclassifying **36 % of offset-absent scenes**:

| region | dark fraction, min … max over six years | |
|---|---|---|
| thar-desert | 0.0057 % … 0.0206 % | **entirely below the 1 % threshold** |
| delhi-urban | 0.0184 % … 0.3265 % | **entirely below the 1 % threshold** |
| sundarbans | 8.10 % … 30.66 % | clear |
| mumbai-coast | 41.97 % … 60.70 % | clear |

Every Thar and Delhi scene is offset-**absent**, and every one would have been called
offset-present — shifting its NDVI by ~0.24 with nothing out of range to reveal it. The rule
worked on Kolkata because Kolkata is wet. It was measuring terrain.

**What replaced it** is a one-sided test: an offset-bearing product cannot hold pixels below
~800 DN, so a low floor *proves* the offset absent, while a high floor proves nothing — a bright
desert tile and an offset-bearing scene look identical up there, and in the measured sample the
one offset-present scene (floor 1003 DN) sits *inside* the range of offset-absent ones (922 to
2048 DN). So the pixels can only ever say "absent", and where they are silent the decision falls
back to metadata **and the result carries a warning saying so**. Across the 48 scenes: 0
misclassified, against 17 for the rule it replaces.

The decision has also been **independently verified for one scene**. NDVI recomputed from raw
bands without any Bhoomi code agrees with Bhoomi's output on 100 % of 233,027 pixels; the
alternative branch puts 48 % of pixels outside the mathematically possible range; and water,
vegetation and built-up — classified from raw bands, not from NDVI — land at −0.09, +0.59 and
+0.21 respectively, which a harmonization error could not produce. See `PLAN.md` §5.3.1b.

**What is still weak.** Only **two** genuinely offset-bearing products have ever been measured,
so the "present" side of the calibration rests on almost nothing — and one of those two was
nearly misclassified by an automated rule that read the distribution minimum, defeated by 2
outlier pixels out of 7.5 million. Eleven of the 48 scenes cannot be decided from pixels at all
and depend on a metadata field this project has caught lying on 3 of 48. **This remains the part
of Bhoomi most likely to still be wrong.**

Detection is also expensive and highly variable: about 11 seconds warm, but **492 seconds** has
been observed on a cold container. It is paid once per scene and then cached, but a first job on
two uncached scenes can exceed the 10-minute limit and be stopped. Resubmitting works. See
`PLAN.md` §5.3.2 — this is a known, unresolved operational risk.

### Cloud masking depends on a band that is not always there

Masking uses SCL. A scene without an SCL band is processed **unmasked**, and the result carries a
warning saying so. The valid-pixel fraction is recorded on every output — a result that is 80 %
cloud says so rather than rendering as a mostly-empty raster.

Warnings travel with the result: they are stored on the output row, returned by the API, and
shown above the statistics in the web interface — above, because they change how the numbers
should be read, and reading them second is too late.

---

## 6. Limitations of scope

- **One scene per analysis.** An AOI crossing a scene boundary is refused rather than silently
  mosaicked. Mosaicking with seam handling is real work and is not done.
- **Maximum AOI 500 km².** Not a technical ceiling — it keeps outputs small and the 30-day
  retention affordable.
- **Outputs expire after 30 days.**
- **NDBI is computed at 20 m**, not 10. SWIR is a 20 m band; upsampling it would invent detail
  the sensor never recorded.
- **Optical only.** Cloud cover limits usable dates, which matters in monsoon months.
- **No NRSC Bhoonidhi data yet.** API access requires a static public IPv4 whitelisted by NRSC
  administrators, which is not yet in place.

---

## 7. Where these numbers come from

Reproducibility matters more than the numbers, so here is the provenance of each.

| Claim | Source | Independently re-run? |
|---|---|---|
| Demo change: mean −0.0274, loss 9.747 %, gain 3.264 %, ratio 2.99 : 1 | `probes/verify_change.py`, 2026-07-30 | **Yes** — reproduced 2026-07-31 through the job queue, agreeing to three significant figures |
| Spatial clustering 7.0× / 18.4× | measured 2026-07-31 on the change raster | first measurement |
| SCL 27.67 % → 9.45 %; NDVI > 0.4 40.71 % → 34.13 % | `probes/verify_change.py`, 2026-07-30 | not re-run |
| ΔNDBI by NDVI group; 4.61 : 1 asymmetry | `examples/kolkata_ndbi.py`, 2026-07-30 | not re-run |
| 7-year recovery: 16.64 % permanent, 1.44 % of AOI | `examples/kolkata_timeseries.py`, 2026-07-30 | not re-run |
| Baselines available per year | queried live, 2026-07-31 | — |
| Offset detector: 36 % error rate, 48-scene sample, 0 errors after the fix | measured 2026-07-31; replayed through the shipped code by `probes/verify_offset_rule.py` | **Yes** — the probe runs the shipped resolver, not the analysis script |

The demo pair is `S2A_45QXF_20200310_1_L2A` → `S2B_45QXF_20260304_0_L2A`, both 0.000 % cloud,
over the New Town / Rajarhat AOI.

Every empirical claim in `PLAN.md` is re-runnable from `probes/`.

---

## 8. What is not yet deployed

- **Not publicly hosted.** Everything above was run locally or in Docker.
- **The tile server is bound to loopback.** While outputs live on a filesystem it will open
  whatever path it is given, so exposing it publicly would be an arbitrary-file-read. Object
  storage removes this rather than mitigating it.
- **Object storage is implemented but has no live bucket**, and has been verified only against a
  local S3-compatible server. Nothing here demonstrates anything about the chosen provider's
  latency or durability.
