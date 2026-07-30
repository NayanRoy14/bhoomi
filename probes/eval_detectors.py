"""Evaluate BOA-offset detector variants against the cached 7-scene series.

IMPORTANT CAVEAT: the series contains exactly ONE offset-bearing scene (2022).
A single positive example cannot validate a fitted threshold -- any cut between
the 2022 statistic and the next-highest one would score perfectly. So the goal
here is NOT to fit a threshold to the data. It is to check whether a threshold
derived from PHYSICS separates the classes with margin.

The physics: the offset is exactly 1000 DN and reflectance cannot be
meaningfully below about -0.02 (-200 DN). Therefore

  offset present -> essentially no valid pixels below ~800 DN
  offset absent  -> dark targets (shadow, deep water) reach near 0 DN

So "does this scene have a meaningful population of very dark pixels?" answers
the question without reference to any observed threshold.
"""

from __future__ import annotations

import json

import numpy as np

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CACHE = ROOT / "data" / "series_cache.npz"
YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

#: Established from DN medians: only the 2022 scene carries the raw offset.
TRUTH = {y: (y == 2022) for y in YEARS}

#: Physically motivated, not fitted: 1000 DN offset minus ~200 DN of plausible
#: negative reflectance leaves 800; 700 keeps a margin below that.
DARK_DN = 700.0
DARK_MIN_FRACTION = 0.01

z = np.load(CACHE, allow_pickle=True)
props = json.loads(str(z["properties"]))

print("Scene statistics (valid pixels only)\n")
print(f"{'year':<6}{'flag':>7}{'base':>7}{'p0.1':>8}{'p1':>7}{'p5':>7}"
      f"{'%<700':>8}{'nir p50':>9}")

stats = {}
for y in YEARS:
    nir = z[f"{y}_nir"].astype(float)
    valid = nir > 0
    v = nir[valid]
    s = {
        "p01": np.percentile(v, 0.1),
        "p1": np.percentile(v, 1),
        "p5": np.percentile(v, 5),
        "frac_dark": float((v < DARK_DN).mean()),
        "p50": np.median(v),
    }
    stats[y] = s
    p = props[str(y)]
    print(f"{y:<6}{str(p.get('earthsearch:boa_offset_applied')):>7}"
          f"{p['s2:processing_baseline']:>7}{s['p01']:>8.0f}{s['p1']:>7.0f}"
          f"{s['p5']:>7.0f}{s['frac_dark'] * 100:>7.2f}%{s['p50']:>9.0f}")


def report(name, values, predicate):
    """Print class separation for one statistic."""
    pos = [values[y] for y in YEARS if TRUTH[y]]
    neg = [values[y] for y in YEARS if not TRUTH[y]]
    correct = all(predicate(values[y]) == TRUTH[y] for y in YEARS)
    print(f"  {name:<26} offset-scene={pos[0]:<10.3f} "
          f"others=[{min(neg):.3f} .. {max(neg):.3f}]  "
          f"{'SEPARATES' if correct else 'FAILS'}")


print("\nDetector candidates\n")
report("p1 of NIR", {y: stats[y]["p1"] for y in YEARS}, lambda v: v > 800)
report("p0.1 of NIR", {y: stats[y]["p01"] for y in YEARS}, lambda v: v > 800)
report("fraction NIR < 700", {y: stats[y]["frac_dark"] for y in YEARS},
       lambda v: v < DARK_MIN_FRACTION)

print("\nChosen rule: offset is PRESENT when fewer than "
      f"{DARK_MIN_FRACTION:.0%} of valid pixels fall below {DARK_DN:.0f} DN.\n")

print(f"{'year':<6}{'detected':>10}{'truth':>8}{'metadata rule':>15}{'':>4}")
for y in YEARS:
    detected = stats[y]["frac_dark"] < DARK_MIN_FRACTION
    p = props[str(y)]
    flag = p.get("earthsearch:boa_offset_applied")
    meta = (flag is False) and float(p["s2:processing_baseline"]) >= 4.0
    mark = "ok" if detected == TRUTH[y] else "WRONG"
    meta_mark = "" if meta == TRUTH[y] else "  <- metadata wrong"
    print(f"{y:<6}{str(detected):>10}{str(TRUTH[y]):>8}{str(meta):>15}  {mark}{meta_mark}")

# Consistency check: NDVI medians must not contain an impossible outlier.
print("\nResulting NDVI medians\n")
print(f"{'year':<6}{'offset applied':>16}{'median NDVI':>13}")
for y in YEARS:
    nir = z[f"{y}_nir"].astype(np.float32)
    red = z[f"{y}_red"].astype(np.float32)
    valid = (nir > 0) & (red > 0)
    apply_offset = stats[y]["frac_dark"] < DARK_MIN_FRACTION
    shift = -0.1 if apply_offset else 0.0
    n = nir[valid] / 10000.0 + shift
    r = red[valid] / 10000.0 + shift
    ndvi = (n - r) / np.where(np.abs(n + r) < 1e-10, np.nan, n + r)
    print(f"{y:<6}{str(apply_offset):>16}{np.nanmedian(ndvi):>13.3f}")
