"""Third-date test, extended: does the NDVI loss persist or revert?

Construction is permanent; harvest reverts. Two dates cannot tell them apart
(PLAN.md 5.4.4). Seven can.

Runs from the cached 7-scene series (probes/cache_series.py). The reflectance
convention is determined PER SCENE from the pixels, not from metadata -- the
first attempt at this analysis trusted the metadata and silently corrupted the
2025 scene to a median NDVI of 1.000.
"""

from __future__ import annotations

import json
import sys

import numpy as np

sys.path.insert(0, r"D:\Bhoomi")

from processing import apply_mask, harmonize, indices, masking  # noqa: E402

CACHE = r"D:\Bhoomi\data\series_cache.npz"
OUT = r"D:\Bhoomi\outputs"
YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

z = np.load(CACHE, allow_pickle=True)
props = json.loads(str(z["properties"]))

series: dict[int, np.ndarray] = {}
print("year  date        baseline  flag    offset?  source   median NDVI")
for y in YEARS:
    p = props[str(y)]
    nir_dn, red_dn, scl = z[f"{y}_nir"], z[f"{y}_red"], z[f"{y}_scl"]

    # Determine the convention from the pixels. In production this uses
    # detect_offset_in_scene() against a full-tile overview; the cached AOI is
    # known to contain water and shadow, so the array form is safe here.
    present = harmonize.detect_offset_in_array(nir_dn.astype(np.float32))

    invalid = masking.scl_mask(scl) | (nir_dn == 0) | (red_dn == 0)
    nir = apply_mask(harmonize.to_reflectance(nir_dn, p, present), invalid)
    red = apply_mask(harmonize.to_reflectance(red_dn, p, present), invalid)
    series[y] = indices.ndvi(nir, red)

    flag = str(p.get("earthsearch:boa_offset_applied"))
    meta = harmonize._metadata_offset_present(p)
    src = "pixels" if meta == present else "PIXELS*"
    print(f"{y}  {p['datetime'][:10]}  {p['s2:processing_baseline']:>8}  {flag:>5}"
          f"{str(present):>9}  {src:>7}   {np.nanmedian(series[y]):.3f}")
print("  * metadata disagreed and was overruled")

a, b = series[2020], series[2026]
valid = np.isfinite(a) & np.isfinite(b)
loss = valid & ((b - a) < -0.2)
stable = valid & (np.abs(b - a) <= 0.05)
print(f"\nloss zone: {int(loss.sum()):,} px    stable zone: {int(stable.sum()):,} px")

print("\n--- mean NDVI trajectory ---")
print(f"  {'year':<7}{'loss zone':>12}{'stable zone':>14}{'whole AOI':>12}")
for y in YEARS:
    x = series[y]
    print(f"  {y:<7}{np.nanmean(x[loss]):12.3f}{np.nanmean(x[stable]):14.3f}"
          f"{np.nanmean(x[valid]):12.3f}")

# Recovery test: how many intermediate years returned to within 0.1 of 2020?
mid = [2021, 2022, 2023, 2024, 2025]
stack = np.stack([series[y] for y in mid])
recovered = np.nansum(stack >= (a - 0.1), axis=0)

n = int(loss.sum())
print(f"\n--- recovery test on {n:,} loss pixels ---")
print("  (of 2021-2025, how many years returned to within 0.1 of the 2020 value)")
for k in range(6):
    c = int(((recovered == k) & loss).sum())
    print(f"    {k} year(s): {c:>8,}  {c / n * 100:5.2f}%")

never = int(((recovered == 0) & loss).sum())
print(f"\n  never recovered  (construction-like): {never / n * 100:5.2f}%")
print(f"  recovered >= once (crop-like)       : {(n - never) / n * 100:5.2f}%")

ns = int(stable.sum())
print(f"  control -- stable zone never-recovered: "
      f"{int(((recovered == 0) & stable).sum()) / ns * 100:5.2f}%")

# Onset year among the never-recovered pixels.
perm = loss & (recovered == 0)
first = np.full(a.shape, -1, dtype=int)
for y in YEARS[1:]:
    hit = (first < 0) & (series[y] < (a - 0.2)) & perm
    first[hit] = y
total = int(perm.sum())
print(f"\n--- onset year, among the {total:,} never-recovered pixels ---")
for y in YEARS[1:]:
    c = int((first == y).sum())
    if c:
        print(f"    {y}: {c:>8,}  {c / total * 100:5.2f}%")

np.save(rf"{OUT}\_series_ndvi.npy", np.stack([series[y] for y in YEARS]))
