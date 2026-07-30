"""Clip small local subsets of the Bhoomi demo scenes for hand-inspection in QGIS.

Reads ONLY the AOI window from each 206 MB COG over HTTP -- this is the same
windowed-read technique the Bhoomi worker will use (PLAN.md 5.1), done by hand.

AOI: New Town / Rajarhat / Salt Lake, north-east Kolkata -- the corridor where
the 2020-2026 urban expansion actually happened.
"""
import os
import time

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# GDAL tuning for reading COGs over HTTP -- without these it makes far too many requests
os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
os.environ["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] = ".tif"
os.environ["GDAL_HTTP_MULTIPLEX"] = "YES"
os.environ["VSI_CACHE"] = "TRUE"

AOI_WGS84 = (88.35, 22.55, 88.52, 22.68)  # lon_min, lat_min, lon_max, lat_max
OUT_DIR = ROOT / "data"

BASE = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/45/Q/XF"
SCENES = {
    # label            scene id                      offset applied?
    "2020": (f"{BASE}/2020/3/S2A_45QXF_20200310_1_L2A", True),
    "2026": (f"{BASE}/2026/3/S2B_45QXF_20260304_0_L2A", True),
    # same acquisition as 2020, but the ORIGINAL un-reprocessed version:
    "2020_raw": (f"{BASE}/2020/3/S2A_45QXF_20200310_0_L2A", False),
}
BANDS = {"red": "B04", "nir": "B08"}

os.makedirs(OUT_DIR, exist_ok=True)

for label, (scene_url, offset_applied) in SCENES.items():
    for band_name, band_file in BANDS.items():
        url = f"{scene_url}/{band_file}.tif"
        out = os.path.join(OUT_DIR, f"{label}_{band_name}.tif")

        t0 = time.time()
        with rasterio.open(url) as src:
            bounds_utm = transform_bounds("EPSG:4326", src.crs, *AOI_WGS84)
            window = from_bounds(*bounds_utm, transform=src.transform)
            data = src.read(1, window=window)          # <- only these pixels cross the network
            profile = src.profile.copy()
            profile.update(
                driver="GTiff",
                height=data.shape[0],
                width=data.shape[1],
                transform=src.window_transform(window),
                compress="deflate",
            )
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(data, 1)

        dt = time.time() - t0
        mb = os.path.getsize(out) / 1e6
        print(f"{label:9} {band_name:4} {data.shape[1]}x{data.shape[0]} px  "
              f"{mb:5.1f} MB  in {dt:5.1f}s   offset_applied={offset_applied}")

print(f"\nWritten to {OUT_DIR}")
