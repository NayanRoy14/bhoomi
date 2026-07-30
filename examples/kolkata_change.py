"""Worked example: NDVI change over New Town / Rajarhat, Kolkata, 2020 -> 2026.

This is the flagship demo (PLAN.md 6) executed through the processing library,
and it doubles as an end-to-end check: the numbers it prints must match those
measured independently in probes/verify_change.py.

Band data is read from the local clips in D:\\Bhoomi\\data (produced by
probes/clip_demo_aoi.py). The remote path is exercised separately at the end.
"""

from __future__ import annotations

import logging
import sys

import numpy as np

sys.path.insert(0, r"D:\Bhoomi")

from processing import (  # noqa: E402
    apply_mask, change, cog, grid_for_aoi, indices, masking,
    raster_utils, to_reflectance, valid_fraction,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DATA = r"D:\Bhoomi\data"
OUT = r"D:\Bhoomi\outputs"
AOI = (88.35, 22.55, 88.52, 22.68)  # D13: New Town / Rajarhat

SCENES = {
    2020: {
        "id": "S2A_45QXF_20200310_1_L2A",
        "properties": {
            "earthsearch:boa_offset_applied": True,
            "s2:processing_baseline": "05.00",
            "datetime": "2020-03-10T04:42:43Z",
        },
    },
    2026: {
        "id": "S2B_45QXF_20260304_0_L2A",
        "properties": {
            "earthsearch:boa_offset_applied": True,
            "s2:processing_baseline": "05.12",
            "datetime": "2026-03-04T04:46:54Z",
        },
    },
}


def compute_ndvi(year: int, grid: raster_utils.Grid) -> tuple[np.ndarray, float]:
    props = SCENES[year]["properties"]

    nir_dn = raster_utils.read_to_grid(rf"{DATA}\{year}_nir.tif", grid)
    red_dn = raster_utils.read_to_grid(rf"{DATA}\{year}_red.tif", grid)
    scl = raster_utils.read_scl_to_grid(rf"{DATA}\{year}_scl.tif", grid)

    # DN 0 is nodata in Sentinel-2 L2A; fold it into the mask.
    invalid = masking.scl_mask(scl) | (nir_dn == 0) | (red_dn == 0)
    kept = valid_fraction(invalid)

    nir = apply_mask(to_reflectance(nir_dn, props), invalid)
    red = apply_mask(to_reflectance(red_dn, props), invalid)

    return indices.ndvi(nir, red), kept


def main() -> None:
    grid = grid_for_aoi(AOI, resolution=10.0)
    print(f"\ngrid: {grid.width}x{grid.height} @ {grid.resolution} m  "
          f"{grid.crs}  ({grid.pixel_count:,} px)\n")

    ndvi_2020, valid_2020 = compute_ndvi(2020, grid)
    ndvi_2026, valid_2026 = compute_ndvi(2026, grid)

    print("--- NDVI (compare against probes/verify_change.py) ---")
    for year, arr, kept in [(2020, ndvi_2020, valid_2020), (2026, ndvi_2026, valid_2026)]:
        print(f"  {year}  median {np.nanmedian(arr):6.3f}   p95 {np.nanpercentile(arr, 95):6.3f}"
              f"   NDVI>0.4 {np.nanmean(arr > 0.4) * 100:5.2f}%   valid {kept * 100:5.2f}%")

    diff, warnings = change.difference(
        ndvi_2020, ndvi_2026,
        SCENES[2020]["properties"], SCENES[2026]["properties"])
    stats = change.change_stats(diff, threshold=0.2, warnings=warnings)

    print("\n--- change 2020 -> 2026 ---")
    print(f"  mean {stats.mean:+.4f}   median {stats.median:+.4f}")
    print(f"  lost >0.2 : {stats.loss_fraction * 100:5.2f}%")
    print(f"  gained>0.2: {stats.gain_fraction * 100:5.2f}%")
    print(f"  asymmetry : {stats.asymmetry:.2f}:1  "
          f"({'real signal' if stats.asymmetry > 1.5 else 'consistent with noise'})")
    for w in stats.warnings:
        print(f"  WARNING: {w}")

    print("\n--- outputs ---")
    for name, arr, year in [("ndvi_2020", ndvi_2020, 2020), ("ndvi_2026", ndvi_2026, 2026)]:
        path = cog.write_cog(
            rf"{OUT}\{name}.tif", arr, grid,
            metadata=cog.default_metadata(
                "ndvi", [SCENES[year]["id"]], "(nir - red) / (nir + red)",
                baselines=[SCENES[year]["properties"]["s2:processing_baseline"]]))
        ok, msgs = cog.validate_cog(path)
        print(f"  {path.name:14} valid COG: {ok}  {msgs if msgs else ''}")

    path = cog.write_cog(
        rf"{OUT}\ndvi_change.tif", diff, grid,
        metadata=cog.default_metadata(
            "change", [SCENES[2020]["id"], SCENES[2026]["id"]],
            "ndvi(2026) - ndvi(2020)",
            baselines=["05.00", "05.12"], valid_fraction=stats.valid_fraction))
    ok, msgs = cog.validate_cog(path)
    print(f"  {path.name:14} valid COG: {ok}  {msgs if msgs else ''}")

    print("\n--- remote read path (the Bhoonidhi-agnostic abstraction) ---")
    url = ("https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
           "45/Q/XF/2026/3/S2B_45QXF_20260304_0_L2A/B04.tif")
    info = raster_utils.describe(url)
    print(f"  {info['width']}x{info['height']} @ {info['resolution']} m  "
          f"{info['crs']}  overviews={len(info['overviews'])}  "
          f"(header only -- no pixels transferred)")


if __name__ == "__main__":
    main()
