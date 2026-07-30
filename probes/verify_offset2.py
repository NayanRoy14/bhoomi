"""Decisive test: which NDVI formula is physically valid for each encoding?

NDVI is mathematically bounded to [-1, 1] whenever both reflectances are positive.
Any formula producing values outside that range is wrong for that data.
"""
import numpy as np
import rasterio

D = r"D:\Bhoomi\data"


def band(name):
    with rasterio.open(rf"{D}\{name}.tif") as s:
        return s.read(1).astype("float64")


SETS = {
    "2020 (_1_, baseline 05.00, flag=True) ": ("2020_nir", "2020_red"),
    "2020 (_0_, baseline 02.14, flag=False)": ("2020_raw_nir", "2020_raw_red"),
    "2026 (_0_, baseline 05.12, flag=True) ": ("2026_nir", "2026_red"),
}

print("--- raw DN percentiles (if +1000 were baked in, these would sit ~1000 higher) ---")
for lbl, (nn, rn) in SETS.items():
    n, r = band(nn), band(rn)
    v = (n > 0) & (r > 0)
    print(f"  {lbl}  red p05/p50/p95 = "
          f"{np.percentile(r[v],5):6.0f}/{np.percentile(r[v],50):6.0f}/{np.percentile(r[v],95):6.0f}"
          f"   nir p05/p50/p95 = "
          f"{np.percentile(n[v],5):6.0f}/{np.percentile(n[v],50):6.0f}/{np.percentile(n[v],95):6.0f}")

print("\n--- NDVI validity: %% of pixels outside the mathematically possible [-1, 1] ---")
print(f"  {'dataset':40} {'plain (N-R)/(N+R)':>20} {'offset (N-R)/(N+R-2000)':>26}")
for lbl, (nn, rn) in SETS.items():
    n, r = band(nn), band(rn)
    v = (n > 0) & (r > 0)
    n, r = n[v], r[v]
    out = {}
    for tag, den in [("plain", n + r), ("offset", n + r - 2000)]:
        den = np.where(np.abs(den) < 1e-10, np.nan, den)
        x = (n - r) / den
        out[tag] = np.nanmean((x < -1) | (x > 1)) * 100
    print(f"  {lbl:40} {out['plain']:19.2f}% {out['offset']:25.2f}%")

print("\n--- median NDVI using the plain formula (sanity: vegetated delta ~0.3-0.6) ---")
for lbl, (nn, rn) in SETS.items():
    n, r = band(nn), band(rn)
    v = (n > 0) & (r > 0)
    n, r = n[v], r[v]
    x = (n - r) / (n + r)
    print(f"  {lbl}  median {np.median(x):6.3f}   p05 {np.percentile(x,5):6.3f}   "
          f"p95 {np.percentile(x,95):6.3f}")
