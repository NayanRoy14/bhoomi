"""SUPERSEDED -- kept as the record of a hypothesis that measurement refuted.

Retained because PLAN.md 5.3 cites the sequence of wrong answers, and because the
run itself is what falsified the hypothesis. Do not treat its framing as correct.

Two claims below turned out to be false:

1. "Only the encoding differs" -- it does not. The two versions are different
   Sen2Cor outputs (02.14 vs 05.00), so surface reflectance genuinely differs
   per pixel by up to +-3900 DN. Median NDVI 0.309 vs 0.323.
2. "A correct implementation must return the SAME answer for both" -- it must
   not. They are legitimately different products.

The offset question itself was settled later by probes/eval_detectors.py: no
metadata field is reliable, and the convention must be detected from the pixels.

---

2020_*      = S2A_45QXF_20200310_1_L2A  (baseline 05.00, boa_offset_applied=True)
2020_raw_*  = S2A_45QXF_20200310_0_L2A  (baseline 02.14, boa_offset_applied=False)
"""
import numpy as np
import rasterio

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

D = ROOT / "data"


def band(name):
    with rasterio.open(D / f"{name}.tif") as s:
        return s.read(1).astype("float64")


n_off, r_off = band("2020_nir"), band("2020_red")       # offset applied
n_raw, r_raw = band("2020_raw_nir"), band("2020_raw_red")  # no offset

valid = (n_off > 0) & (r_off > 0) & (n_raw > 0) & (r_raw > 0)
print(f"valid pixels: {valid.sum():,} of {valid.size:,}\n")

print("--- raw DN comparison (is the difference really a flat 1000?) ---")
for lbl, a, b in [("red", r_off, r_raw), ("nir", n_off, n_raw)]:
    d = (a - b)[valid]
    print(f"  {lbl}: offset-version minus raw-version -> "
          f"mean {d.mean():8.2f}   min {d.min():7.1f}   max {d.max():7.1f}   "
          f"pixels exactly 1000: {(d == 1000).mean() * 100:5.1f}%")


def ndvi(n, r, offset_applied):
    den = (n + r - 2000) if offset_applied else (n + r)
    den = np.where(np.abs(den) < 1e-10, np.nan, den)
    return (n - r) / den


correct_off = ndvi(n_off, r_off, True)[valid]   # correct handling, offset data
correct_raw = ndvi(n_raw, r_raw, False)[valid]  # correct handling, raw data
naive_off = ndvi(n_off, r_off, False)[valid]    # WRONG: forgot the offset

print("\n--- NDVI over the same ground, three ways ---")
for lbl, arr in [("correct, offset data ", correct_off),
                 ("correct, raw data    ", correct_raw),
                 ("NAIVE (forgot offset)", naive_off)]:
    print(f"  {lbl}  mean {np.nanmean(arr):6.4f}   "
          f"p10 {np.nanpercentile(arr, 10):6.4f}   p90 {np.nanpercentile(arr, 90):6.4f}")

agree = np.abs(correct_off - correct_raw)
error = np.abs(correct_off - naive_off)
print(f"\n  correct-vs-correct  : max abs difference {np.nanmax(agree):.6f}  <- should be ~0")
print(f"  correct-vs-naive    : mean abs error {np.nanmean(error):.4f}, "
      f"max {np.nanmax(error):.4f}  <- the silent bug")
