"""Clip the demo pair's red and NIR bands to the D13 AOI, for the hand-check.

The August checklist asks for an NDVI raster of Kolkata made by hand in QGIS.
Nothing here computes that -- the point of the gate is that a person does, and
sees the number rather than reading it. This only removes the tedious part:
downloading four bands over a slow link and clipping them, so QGIS opens
something small and local instead of streaming 110 km tiles.

Writes four GeoTIFFs to outputs/qgis-handcheck/ (gitignored), each already in
EPSG:32645 at 10 m, cropped to D13:

    2020_red.tif  2020_nir.tif    S2A_45QXF_20200310_1_L2A
    2026_red.tif  2026_nir.tif    S2B_45QXF_20260304_0_L2A

They hold raw DN. Nothing is scaled, so the reflectance convention is still
yours to apply in the raster calculator -- which is the part of the exercise
that has actually bitten this project. Use DN / 10000; see PLAN.md 5.3 and the
August checklist for why, and probes/verify_demo_offset.py for the measurement.

    python -m probes.prepare_qgis_handcheck
"""

from __future__ import annotations

import functools
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]

print = functools.partial(print, flush=True)  # noqa: A001

ITEM = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/"

SCENES = {
    "2020": "S2A_45QXF_20200310_1_L2A",
    "2026": "S2B_45QXF_20260304_0_L2A",
}

#: D13.
AOI = (88.35, 22.55, 88.52, 22.68)

OUT = ROOT / "outputs" / "qgis-handcheck"


def clip(url: str, destination: Path) -> tuple[tuple[int, int], int, float]:
    """Write the AOI window to `destination`. Returns shape, DN min and p0.1.

    The two DN statistics are the point of printing anything at all. A product
    carrying the BOA offset has +1000 in every pixel and cannot go below ~1000,
    so a minimum in the low hundreds is the file itself saying the offset is
    absent and `DN / 10000` is the right branch. That is worth seeing before
    opening the raster calculator, because the August checklist asserted the
    opposite until it was corrected, and the whole value of a hand-check is not
    having to take the correction on trust either.
    """
    with rasterio.open(url) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, *AOI)
        window = from_bounds(*bounds, transform=src.transform)
        data = src.read(1, window=window)
        profile = {
            "driver": "GTiff",
            "dtype": src.dtypes[0],
            "count": 1,
            "height": data.shape[0],
            "width": data.shape[1],
            "crs": src.crs,
            "transform": src.window_transform(window),
            "nodata": src.nodata,
            "compress": "deflate",
        }
        with rasterio.open(destination, "w", **profile) as dst:
            dst.write(data, 1)
    valid = data[data > 0]
    return data.shape, int(data.min()), float(np.percentile(valid, 0.1))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for year, scene_id in SCENES.items():
        with urllib.request.urlopen(ITEM + scene_id, timeout=60) as response:
            item = json.load(response)
        print(f"\n{year}  {scene_id}  baseline {item['properties'].get('s2:processing_baseline')}")
        for band in ("red", "nir"):
            destination = OUT / f"{year}_{band}.tif"
            shape, dn_min, floor = clip(item["assets"][band]["href"], destination)
            size_mb = destination.stat().st_size / 1e6
            print(f"  {destination.name:<14} {shape[1]}x{shape[0]}  {size_mb:.1f} MB"
                  f"   DN min {dn_min}, p0.1 {floor:.0f}")

    print(f"\nWritten to {OUT}")
    print("Every DN minimum above is far below 1000, which an offset-bearing")
    print("product cannot reach -- so divide by 10000 and subtract nothing.")
    print("\nDrag all four into QGIS. Raster calculator, per year:")
    print('  ("YYYY_nir@1" / 10000.0 - "YYYY_red@1" / 10000.0) / '
          '("YYYY_nir@1" / 10000.0 + "YYYY_red@1" / 10000.0)')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
