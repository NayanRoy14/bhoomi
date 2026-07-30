"""Download the 7-scene series once and cache it, so detector variants can be
evaluated offline instead of re-fetching ~9 minutes of pixels per attempt.

Ground truth, established from DN medians (probes output 2026-07-30): the BOA
offset is present in the 2022 scene only. 2022 is baseline 04.00 -- the first
baseline to apply the offset -- and is an original, non-reprocessed product.
"""

from __future__ import annotations

import json
import sys
import urllib.request

import numpy as np
from rasterio.enums import Resampling

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))

from processing import grid_for_aoi, raster_utils  # noqa: E402

CACHE = ROOT / "data" / "series_cache.npz"
SCENES = {
    2020: "S2A_45QXF_20200310_1_L2A",
    2021: "S2A_45QXF_20210315_2_L2A",
    2022: "S2B_45QXF_20220213_0_L2A",
    2023: "S2A_45QXF_20230213_0_L2A",
    2024: "S2A_45QXF_20240309_0_L2A",
    2025: "S2C_45QXF_20250304_0_L2A",
    2026: "S2B_45QXF_20260304_0_L2A",
}


def fetch(sid):
    b = json.dumps({"collections": ["sentinel-2-l2a"], "ids": [sid], "limit": 1}).encode()
    r = urllib.request.Request("https://earth-search.aws.element84.com/v1/search",
                               data=b, headers={"Content-Type": "application/json"},
                               method="POST")
    return json.load(urllib.request.urlopen(r, timeout=90))["features"][0]


grid = grid_for_aoi((88.35, 22.55, 88.52, 22.68), 20.0)
arrays, props = {}, {}

for year, sid in SCENES.items():
    item = fetch(sid)
    a = item["assets"]
    arrays[f"{year}_nir"] = raster_utils.read_to_grid(a["nir"]["href"], grid, Resampling.average)
    arrays[f"{year}_red"] = raster_utils.read_to_grid(a["red"]["href"], grid, Resampling.average)
    arrays[f"{year}_scl"] = raster_utils.read_scl_to_grid(a["scl"]["href"], grid)
    props[str(year)] = item["properties"]
    print(f"cached {year}  {item['properties']['datetime'][:10]}", flush=True)

np.savez_compressed(CACHE, properties=json.dumps(props), **arrays)
print(f"\nwrote {CACHE}")
