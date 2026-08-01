"""Reproduce the numbers the August QGIS checklist quotes.

The checklist tells you to compute NDVI on the two demo scenes by hand and
compare the result against the README. Whether it agrees turns entirely on one
choice — `DN / 10000` or `(DN - 1000) / 10000` — and an earlier revision of the
checklist named the wrong one, on the reasoning that baseline >= 04.00 implies
the offset is folded into the pixels. It is not, for these files: Earth Search
applies the correction when it builds the COGs.

This prints the evidence for that, per scene:

  - the DN floor `processing/harmonize.py` measures, and what it resolves to,
  - NDVI over the D13 AOI computed both ways.

The wrong convention does not produce a subtly shifted number. It puts most of
the scene outside [-1, 1], which no NDVI can be, so the check is self-announcing
once you know to look. Run it if the hand-computed figure disagrees with the
README and you need to find out which side is wrong.

    python -m probes.verify_demo_offset
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from processing import harmonize  # noqa: E402

#: Each band read here can take minutes on a slow link (PLAN.md 8 records a 27x
#: spread on the same read), and block-buffered output makes that indistinguishable
#: from a hang when stdout is redirected to a file. Flush as we go.
print = functools.partial(print, flush=True)  # noqa: A001

ITEM = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/"

#: D11's demo pair, on tile 45QXF. `_1_` for 2020 — the 05.00 reprocessing,
#: which is what `deduplicate_by_acquisition` keeps.
SCENES = ("S2A_45QXF_20200310_1_L2A", "S2B_45QXF_20260304_0_L2A")

#: D13.
AOI = (88.35, 22.55, 88.52, 22.68)


def _read_aoi(url: str) -> np.ndarray:
    with rasterio.open(url) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, *AOI)
        window = from_bounds(*bounds, transform=src.transform)
        return src.read(1, window=window).astype("float64")


def _ndvi(red: np.ndarray, nir: np.ndarray, offset: float) -> np.ndarray:
    red = (red + offset) / 10000.0
    nir = (nir + offset) / 10000.0
    total = nir + red
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = np.where(total != 0, (nir - red) / total, np.nan)
    return ndvi[np.isfinite(ndvi)]


def main() -> int:
    import urllib.request
    import json

    for scene_id in SCENES:
        with urllib.request.urlopen(ITEM + scene_id, timeout=60) as response:
            item = json.load(response)
        properties = item["properties"]

        evidence = harmonize.measure_offset_floor_in_scene(item["assets"]["red"]["href"])
        decision = harmonize.resolve_offset(evidence, properties)

        print(f"\n=== {scene_id}")
        print(f"  baseline {properties.get(harmonize.BASELINE_KEY)}   "
              f"{harmonize.OFFSET_FLAG} = {properties.get(harmonize.OFFSET_FLAG)!r}")
        print(f"  floor {evidence.floor_dn:.0f} DN (threshold {harmonize.FLOOR_DN:.0f}) "
              f"-> offset {'present' if decision.present else 'absent'}, "
              f"basis {decision.basis}")

        red = _read_aoi(item["assets"]["red"]["href"])
        nir = _read_aoi(item["assets"]["nir"]["href"])

        for label, offset in (("DN / 10000       ", 0.0), ("(DN - 1000)/10000", -1000.0)):
            ndvi = _ndvi(red, nir, offset)
            negative = ((red + offset) < 0).sum() + ((nir + offset) < 0).sum()
            print(f"  {label}  mean {ndvi.mean():+.4f}  median {np.median(ndvi):+.4f}  "
                  f"|NDVI|>1 {100 * np.mean(np.abs(ndvi) > 1):5.1f} %  "
                  f"negative reflectance {100 * negative / (red.size * 2):5.1f} %")

    print("\nThe convention with values outside [-1, 1] is the wrong one. "
          "For these two scenes that is always the subtracting one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
