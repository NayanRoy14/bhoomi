"""Does NDBI corroborate the NDVI loss as construction, or is it harvest?

NDVI cannot tell "field harvested" from "field built on" -- both take a
vegetated pixel to a bare one (PLAN.md 5.4.4). NDBI can:

    NDVI falls AND NDBI rises  -> construction
    NDVI falls AND NDBI flat   -> harvest / seasonal fallow

Both indices are computed on a 20 m grid here, because B11 (SWIR) is natively
20 m and upsampling it would invent detail the sensor never recorded (D4).
The joint product is limited by its coarsest band -- that is the honest choice.

SWIR is read directly from the remote COGs; NIR/RED/SCL come from the local
clips. Same read_to_grid call either way.
"""

from __future__ import annotations

import logging
import sys

import numpy as np
from rasterio.enums import Resampling

sys.path.insert(0, r"D:\Bhoomi")

from processing import (  # noqa: E402
    apply_mask, change, cog, grid_for_aoi, indices, masking,
    raster_utils, to_reflectance,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

DATA, OUT = r"D:\Bhoomi\data", r"D:\Bhoomi\outputs"
AOI = (88.35, 22.55, 88.52, 22.68)
BASE = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/45/Q/XF"

SCENES = {
    2020: {
        "id": "S2A_45QXF_20200310_1_L2A",
        "swir": f"{BASE}/2020/3/S2A_45QXF_20200310_1_L2A/B11.tif",
        "properties": {"earthsearch:boa_offset_applied": True,
                       "s2:processing_baseline": "05.00",
                       "datetime": "2020-03-10T04:42:43Z"},
    },
    2026: {
        "id": "S2B_45QXF_20260304_0_L2A",
        "swir": f"{BASE}/2026/3/S2B_45QXF_20260304_0_L2A/B11.tif",
        "properties": {"earthsearch:boa_offset_applied": True,
                       "s2:processing_baseline": "05.12",
                       "datetime": "2026-03-04T04:46:54Z"},
    },
}


def load_year(year: int, grid):
    """Return (ndvi, ndbi) for one year on the shared 20 m grid."""
    props = SCENES[year]["properties"]

    # Downsampling 10 m -> 20 m: average, not bilinear. Averaging is what a 20 m
    # detector would have measured; bilinear just interpolates four samples.
    nir_dn = raster_utils.read_to_grid(rf"{DATA}\{year}_nir.tif", grid, Resampling.average)
    red_dn = raster_utils.read_to_grid(rf"{DATA}\{year}_red.tif", grid, Resampling.average)
    swir_dn = raster_utils.read_to_grid(SCENES[year]["swir"], grid, Resampling.average)
    scl = raster_utils.read_scl_to_grid(rf"{DATA}\{year}_scl.tif", grid)

    invalid = masking.scl_mask(scl) | (nir_dn == 0) | (red_dn == 0) | (swir_dn == 0)

    nir = apply_mask(to_reflectance(nir_dn, props), invalid)
    red = apply_mask(to_reflectance(red_dn, props), invalid)
    swir = apply_mask(to_reflectance(swir_dn, props), invalid)

    return indices.ndvi(nir, red), indices.ndbi(swir, nir)


def main() -> None:
    grid = grid_for_aoi(AOI, resolution=20.0)
    print(f"grid: {grid.width}x{grid.height} @ {grid.resolution} m  {grid.crs}\n")

    ndvi_a, ndbi_a = load_year(2020, grid)
    ndvi_b, ndbi_b = load_year(2026, grid)

    print("--- index medians (20 m grid) ---")
    print(f"  {'':6}{'NDVI':>9}{'NDBI':>9}")
    for lbl, v, b in [("2020", ndvi_a, ndbi_a), ("2026", ndvi_b, ndbi_b)]:
        print(f"  {lbl:6}{np.nanmedian(v):9.3f}{np.nanmedian(b):9.3f}")

    d_ndvi, _ = change.difference(ndvi_a, ndvi_b)
    d_ndbi, warnings = change.difference(
        ndbi_a, ndbi_b, SCENES[2020]["properties"], SCENES[2026]["properties"])

    s_ndbi = change.change_stats(d_ndbi, threshold=0.1, warnings=warnings)
    print(f"\n--- NDBI change 2020 -> 2026 ---")
    print(f"  mean {s_ndbi.mean:+.4f}   median {s_ndbi.median:+.4f}")
    print(f"  rose >0.1 : {s_ndbi.gain_fraction * 100:5.2f}%")
    print(f"  fell >0.1 : {s_ndbi.loss_fraction * 100:5.2f}%")
    print(f"  asymmetry (gain:loss) {1 / s_ndbi.asymmetry:.2f}:1")

    # The decisive test: threshold-free. Split pixels by what NDVI did, then ask
    # what NDBI did in each group.
    valid = np.isfinite(d_ndvi) & np.isfinite(d_ndbi)
    lost = valid & (d_ndvi < -0.2)
    stable = valid & (np.abs(d_ndvi) <= 0.05)
    gained = valid & (d_ndvi > 0.2)

    print("\n--- mean NDBI change, grouped by what NDVI did ---")
    print(f"  {'NDVI group':<28}{'n':>10}{'share':>8}{'mean d-NDBI':>14}")
    for lbl, m in [("lost  >0.2 NDVI", lost),
                   ("stable (|d| <= 0.05)", stable),
                   ("gained>0.2 NDVI", gained)]:
        n = int(m.sum())
        share = n / int(valid.sum()) * 100 if valid.any() else 0
        print(f"  {lbl:<28}{n:>10,}{share:7.2f}%{np.nanmean(d_ndbi[m]):+14.4f}")

    # Of the NDVI-loss pixels, how many also show a built-up signal?
    built = lost & (d_ndbi > 0.05)
    print(f"\n--- interpretation of the {int(lost.sum()):,} NDVI-loss pixels ---")
    print(f"  NDBI also rose >0.05 (construction-like) : "
          f"{built.sum() / lost.sum() * 100:5.2f}%")
    print(f"  NDBI flat or falling (harvest-like)      : "
          f"{(lost & ~built).sum() / lost.sum() * 100:5.2f}%")

    path = cog.write_cog(
        rf"{OUT}\ndbi_change.tif", d_ndbi, grid,
        metadata=cog.default_metadata(
            "change", [SCENES[2020]["id"], SCENES[2026]["id"]],
            "ndbi(2026) - ndbi(2020)", baselines=["05.00", "05.12"],
            valid_fraction=s_ndbi.valid_fraction))
    ok, msgs = cog.validate_cog(path)
    print(f"\nwrote {path.name}  valid COG: {ok} {msgs if msgs else ''}")

    np.save(rf"{OUT}\_d_ndvi_20m.npy", d_ndvi)
    np.save(rf"{OUT}\_d_ndbi_20m.npy", d_ndbi)


if __name__ == "__main__":
    main()
