"""Build and validate a data-driven BOA-offset detector.

None of the exposed metadata is reliable for this collection:
  - earthsearch:boa_offset_applied=False means "offset present" for the 2022
    scene but "offset absent" for the 2025 scene.
  - raster:bands.offset reports -0.1 uniformly, contradicting measurement.
  - The GeoTIFF's own scale/offset tags are unset (1.0 / 0.0).

Physics gives a discriminator instead. Clear water has NIR reflectance of about
0.01, so over SCL water pixels the NIR digital number should be ~100 if no
offset is present and ~1100 if it is. The gap is an order of magnitude.
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

SCENES = {
    2020: "S2A_45QXF_20200310_1_L2A",
    2021: "S2A_45QXF_20210315_2_L2A",
    2022: "S2B_45QXF_20220213_0_L2A",
    2023: "S2A_45QXF_20230213_0_L2A",
    2024: "S2A_45QXF_20240309_0_L2A",
    2025: "S2C_45QXF_20250304_0_L2A",
    2026: "S2B_45QXF_20260304_0_L2A",
}
WATER_CLASS = 6
# Halfway between the two expected regimes (~100 vs ~1100).
THRESHOLD_DN = 600.0


def fetch(sid):
    b = json.dumps({"collections": ["sentinel-2-l2a"], "ids": [sid], "limit": 1}).encode()
    r = urllib.request.Request("https://earth-search.aws.element84.com/v1/search",
                               data=b, headers={"Content-Type": "application/json"},
                               method="POST")
    return json.load(urllib.request.urlopen(r, timeout=90))["features"][0]


grid = grid_for_aoi((88.35, 22.55, 88.52, 22.68), 20.0)

print(f"{'year':<6}{'flag':>7}{'baseline':>10}{'nir@water':>11}{'p1 all':>9}"
      f"{'detected':>11}{'metadata says':>15}")
for year, sid in SCENES.items():
    item = fetch(sid)
    props = item["properties"]
    nir = raster_utils.read_to_grid(item["assets"]["nir"]["href"], grid,
                                    Resampling.average).astype(float)
    scl = raster_utils.read_scl_to_grid(item["assets"]["scl"]["href"], grid)

    valid = nir > 0
    water = valid & (scl == WATER_CLASS)
    nir_water = float(np.median(nir[water])) if water.sum() > 100 else float("nan")
    p1 = float(np.percentile(nir[valid], 1))

    detected = nir_water > THRESHOLD_DN if np.isfinite(nir_water) else p1 > THRESHOLD_DN
    flag = props.get("earthsearch:boa_offset_applied")
    # Metadata rule from the old harmonize.py: flag False + baseline >= 4 -> present.
    meta_says = (flag is False) and float(props["s2:processing_baseline"]) >= 4.0

    agree = "" if detected == meta_says else "   <-- METADATA WRONG"
    print(f"{year:<6}{str(flag):>7}{props['s2:processing_baseline']:>10}"
          f"{nir_water:>11.0f}{p1:>9.0f}{str(detected):>11}{str(meta_says):>15}{agree}")
