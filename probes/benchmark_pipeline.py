"""Measure end-to-end NDVI runtime and peak memory against AOI size.

Sets two numbers that PLAN.md currently guesses at:
  - the job timeout (section 8)
  - estimated_seconds in the job-creation response (section 7.3)

Also closes risk R6, which says to test at the AOI cap before December rather
than discovering the ceiling in January.
"""

from __future__ import annotations

import gc
import sys
import time

import psutil

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))

import pipeline  # noqa: E402
from catalogue import EarthSearchCatalogue, SearchQuery  # noqa: E402
from processing import grid_for_aoi  # noqa: E402

SCENE_ID = "S2A_45QXF_20200310_1_L2A"

#: (half-width in degrees, centre). Centres are DISTINCT and the squares do not
#: overlap -- an earlier version nested them around one point, so each larger
#: AOI reused GDAL-cached blocks from the smaller ones and one run finished in
#: 0.1 s. Non-overlapping AOIs model what separate user requests actually cost.
#: All centres sit inside tile 45QXF (lon 87.97-89.05, lat 22.51-23.51).
CASES = [
    (0.015, (88.15, 22.65)),
    (0.030, (88.40, 22.70)),
    (0.060, (88.70, 22.85)),
    (0.100, (88.30, 23.20)),
]

process = psutil.Process()


def square(half: float, centre: tuple[float, float]) -> dict:
    lon, lat = centre
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon - half, lat - half], [lon + half, lat - half],
            [lon + half, lat + half], [lon - half, lat + half],
            [lon - half, lat - half],
        ]],
    }


def area_km2(grid) -> float:
    return grid.pixel_count * grid.resolution ** 2 / 1e6


catalogue = EarthSearchCatalogue()
t0 = time.perf_counter()
scene = catalogue.get(SCENE_ID)
search_seconds = time.perf_counter() - t0
print(f"catalogue.get(): {search_seconds:.2f}s\n")

# Warm the offset cache once; it is per-scene, not per-request.
t0 = time.perf_counter()
pipeline.offset_present(scene)
detect_seconds = time.perf_counter() - t0
print(f"offset detection (full-tile overview): {detect_seconds:.2f}s\n")

print(f"{'AOI km2':>9}{'Mpixels':>9}{'seconds':>9}{'Mpx/s':>8}"
      f"{'peak RSS MB':>13}{'MB/Mpx':>9}")
rows = []
for half, centre in CASES:
    aoi = square(half, centre)
    grid = grid_for_aoi(SearchQuery(aoi=aoi).bbox(), 10.0)

    gc.collect()
    before = process.memory_info().rss
    t0 = time.perf_counter()
    result = pipeline.compute_index(scene, aoi, "ndvi")
    elapsed = time.perf_counter() - t0
    peak = process.memory_info().rss

    mpx = grid.pixel_count / 1e6
    used_mb = (peak - before) / 1e6
    rows.append((area_km2(grid), mpx, elapsed))
    print(f"{area_km2(grid):9.1f}{mpx:9.2f}{elapsed:9.1f}{mpx / elapsed:8.3f}"
          f"{peak / 1e6:13.0f}{max(used_mb, 0) / mpx:9.1f}")

    del result
    gc.collect()

print("\n--- extrapolation ---")
# Fit seconds = a + b * Mpixels over the measured points.
n = len(rows)
xs = [r[1] for r in rows]
ys = [r[2] for r in rows]
mean_x, mean_y = sum(xs) / n, sum(ys) / n
b = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / \
    sum((x - mean_x) ** 2 for x in xs)
a = mean_y - b * mean_x
print(f"  seconds ~= {a:.1f} + {b:.1f} * Mpixels   (fit over {n} points)")

for label, km2 in [("PLAN 8 cap, 500 km2", 500), ("demo AOI, 250 km2", 250),
                   ("100 km2", 100), ("50 km2", 50)]:
    mpx = km2 * 1e6 / 100 / 1e6  # 10 m pixels
    print(f"  {label:<22} {mpx:6.1f} Mpx -> {a + b * mpx:7.0f}s "
          f"({(a + b * mpx) / 60:5.1f} min)")

print(f"\n  600s timeout allows ~{(600 - a) / b:.1f} Mpx = "
      f"~{(600 - a) / b * 1e6 * 100 / 1e6:.0f} km2 at 10 m")
