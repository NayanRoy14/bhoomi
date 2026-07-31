"""Does reading a scene's bands concurrently actually make a job faster?

`compute_index` reads its bands one after another. Each read is a windowed
fetch from a COG on S3, so the process spends nearly all of its time waiting on
the network rather than on CPU -- which is the shape of problem concurrency
helps. That is the hypothesis; this measures it.

Measuring it honestly is harder than it looks, for two reasons this probe is
built around:

  - **GDAL caches.** VSI_CACHE keeps fetched blocks in the process, so reading
    the same window twice measures the cache the second time. `benchmark_pipeline`
    hit exactly this and recorded a 0.1 s run. Every AOI here is therefore
    distinct, on a distinct tile, and read exactly once.

  - **Sites differ enormously, and stably.** A 3-band read of the same 0.29 Mpx
    takes ~8 s over Kolkata and ~160 s over Bhopal, and those numbers repeat to
    within 10 % across runs. The spread between sites is ~20x; the effect being
    looked for is a few percent. So *which site* a method is measured on
    dominates the result completely.

    The first version of this probe gave one site to each method and alternated
    by index. That reads like a control and is not one: the site-to-method
    mapping was the same every run, so the two arms were really
    {Kolkata, Nagpur, Bhopal, Surat} against {Jaipur, Hyderabad, Lucknow,
    Coimbatore}, and running it again just re-measured the same confound. It
    reported 1.11x and then 0.97x, and neither number meant anything.

    The design here is **paired**: every site is read twice, once per method,
    on two non-overlapping AOIs inside the same tile -- non-overlapping because
    the same window read twice would hit the GDAL cache the second time. Each
    site then yields a seq/par *ratio*, and the site cancels out of it. Which
    of the two AOIs gets which method alternates, so a systematic difference
    between the two window positions cancels too.

Run it before changing how bands are read, and again after. Compare the median
ratio and the per-site ratios; a real effect shows up in most sites, not in the
median of four numbers that disagree in sign.
"""

from __future__ import annotations

import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from catalogue import EarthSearchCatalogue, SearchQuery  # noqa: E402
from processing import grid_for_aoi, raster_utils  # noqa: E402

#: Distinct cities on distinct MGRS tiles, so no two cases can share a cached
#: block. Spread across India rather than clustered, because tiles differ in how
#: well they compress and a single region would confound that with the method.
SITES = [
    ("Kolkata",   88.44, 22.58),
    ("Jaipur",    75.75, 26.85),
    ("Nagpur",    79.05, 21.12),
    ("Hyderabad", 78.45, 17.40),
    ("Bhopal",    77.40, 23.24),
    ("Lucknow",   80.92, 26.84),
    ("Surat",     72.83, 21.17),
    ("Coimbatore", 76.95, 11.01),
]

#: The bands an NDVI job reads: two 10 m bands and the 20 m scene classification.
BANDS = ("red", "nir", "scl")

HALF = 0.025  # ~5.5 km square, the size of the README's worked example

#: Longitude gap between a site's two AOIs. Four half-widths, so the windows are
#: two full AOI widths apart and cannot share a COG block (1024 px = ~10 km at
#: 10 m, so this clears it) -- otherwise the second read would be served from
#: the cache the first one filled and would "win" by construction.
PAIR_OFFSET = HALF * 4


def square(lon: float, lat: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon - HALF, lat - HALF], [lon + HALF, lat - HALF],
            [lon + HALF, lat + HALF], [lon - HALF, lat + HALF],
            [lon - HALF, lat - HALF],
        ]],
    }


def read_sequential(scene, grid) -> float:
    t0 = time.perf_counter()
    for band in BANDS:
        raster_utils.read_to_grid(scene.href(band), grid)
    return time.perf_counter() - t0


def read_parallel(scene, grid) -> float:
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(BANDS)) as pool:
        futures = [pool.submit(raster_utils.read_to_grid, scene.href(b), grid)
                   for b in BANDS]
        for future in futures:
            future.result()
    return time.perf_counter() - t0


def main() -> int:
    raster_utils.configure_gdal()
    catalogue = EarthSearchCatalogue()

    cases = []
    for name, lon, lat in SITES:
        # Two windows in the same tile: one scene serves both, so the pair
        # differs in nothing but which method reads it.
        left, right = square(lon, lat), square(lon + PAIR_OFFSET, lat)
        scenes = catalogue.search(SearchQuery(
            aoi=left, start="2026-01-01", end="2026-03-15", max_cloud=20))
        usable = [s for s in scenes
                  if s.aoi_coverage(left) > 0.999 and s.aoi_coverage(right) > 0.999]
        if not usable:
            print(f"  {name}: no scene fully covers both windows, skipped")
            continue
        scene = usable[0]
        cases.append((name, scene,
                      grid_for_aoi(SearchQuery(aoi=left).bbox(), 10.0),
                      grid_for_aoi(SearchQuery(aoi=right).bbox(), 10.0)))

    if len(cases) < 4:
        print("Not enough usable sites to compare; need at least 4.")
        return 1

    print(f"\n{len(cases)} sites, paired, {len(BANDS)} bands each, "
          f"{cases[0][2].pixel_count / 1e6:.2f} Mpx per band\n")
    print(f"{'site':<12}{'scene':<28}{'seq s':>8}{'par s':>8}{'seq/par':>9}")

    ratios: list[float] = []
    seq_all: list[float] = []
    par_all: list[float] = []
    for index, (name, scene, left_grid, right_grid) in enumerate(cases):
        # Alternate which window each method gets, so any systematic difference
        # between the two positions cancels across sites instead of loading
        # onto one arm.
        seq_grid, par_grid = ((left_grid, right_grid) if index % 2
                              else (right_grid, left_grid))
        # Sequential first on even sites, parallel first on odd ones, so
        # whatever the connection is doing early in a pair is not always the
        # same method's problem.
        if index % 2:
            seq = read_sequential(scene, seq_grid)
            par = read_parallel(scene, par_grid)
        else:
            par = read_parallel(scene, par_grid)
            seq = read_sequential(scene, seq_grid)

        ratios.append(seq / par)
        seq_all.append(seq)
        par_all.append(par)
        print(f"{name:<12}{scene.id:<28}{seq:8.1f}{par:8.1f}{seq / par:9.2f}")

    faster = sum(1 for r in ratios if r > 1.0)
    print(f"\n  sequential  median {statistics.median(seq_all):6.1f}s")
    print(f"  parallel    median {statistics.median(par_all):6.1f}s")
    print(f"\n  paired speedup: median {statistics.median(ratios):.2f}x, "
          f"range {min(ratios):.2f}-{max(ratios):.2f}")
    print(f"  parallel won on {faster}/{len(ratios)} sites")
    print("\n  The per-site ratio is the result; the medians above it are context.\n"
          "  A real effect shows up as most sites landing on the same side of\n"
          "  1.00. Ratios straddling 1.00 mean no effect this probe can see,\n"
          "  however far apart the medians happen to look.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
