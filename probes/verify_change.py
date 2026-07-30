"""Cross-check: does NDVI corroborate the land-cover shift SCL reports?

SCL says vegetation fell 27.7% -> 9.5% of the AOI between 2020 and 2026.
But SCL is a *classifier*, and its version changed (Sen2Cor 05.00 -> 05.12),
so part of that could be classifier drift rather than ground change.

NDVI is a direct band ratio with no classifier in the loop. If NDVI shows a
much smaller shift than SCL does, SCL is overstating it.
"""
import numpy as np
import rasterio

D = r"D:\Bhoomi\data"


def band(n):
    with rasterio.open(rf"{D}\{n}.tif") as s:
        return s.read(1).astype("float64")


def ndvi(year):
    n, r = band(f"{year}_nir"), band(f"{year}_red")
    v = (n > 0) & (r > 0)
    out = np.full(n.shape, np.nan)
    out[v] = (n[v] - r[v]) / (n[v] + r[v])
    return out


a, b = ndvi(2020), ndvi(2026)

print("--- NDVI distribution ---")
print(f"  {'':6}{'median':>9}{'p75':>9}{'p90':>9}{'p95':>9}")
for lbl, x in [("2020", a), ("2026", b)]:
    print(f"  {lbl:6}{np.nanmedian(x):9.3f}{np.nanpercentile(x,75):9.3f}"
          f"{np.nanpercentile(x,90):9.3f}{np.nanpercentile(x,95):9.3f}")

print("\n--- vegetated area share, by threshold (NDVI is a direct ratio) ---")
print(f"  {'threshold':<12}{'2020':>9}{'2026':>9}{'change':>10}")
for t in (0.3, 0.4, 0.5, 0.6):
    fa = np.nanmean(a > t) * 100
    fb = np.nanmean(b > t) * 100
    print(f"  NDVI > {t:<5}{fa:8.2f}%{fb:8.2f}%{fb - fa:+9.2f} pp")

print("\n--- compare against what SCL claimed ---")
print(f"  SCL class 4 (vegetation) 27.67% -> 9.45%   = -18.22 pp  (-66% relative)")
f40a, f40b = np.nanmean(a > 0.4) * 100, np.nanmean(b > 0.4) * 100
print(f"  NDVI > 0.4               {f40a:5.2f}% -> {f40b:5.2f}%   "
      f"= {f40b - f40a:+.2f} pp  ({(f40b - f40a) / f40a * 100:+.0f}% relative)")

d = b - a
valid = ~np.isnan(d)
print(f"\n--- per-pixel NDVI difference (2026 - 2020) ---")
print(f"  mean {np.nanmean(d):+.4f}   median {np.nanmedian(d):+.4f}")
print(f"  pixels that lost  > 0.2 NDVI: {np.nanmean(d < -0.2) * 100:5.2f}%")
print(f"  pixels that gained> 0.2 NDVI: {np.nanmean(d > 0.2) * 100:5.2f}%")
