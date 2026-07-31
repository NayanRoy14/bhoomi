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

  - **The first read of a scene pays for the connection.** The paired design
    above then failed too, in a third way. Across 7 sites the method that ran
    *second* was faster at 5 of them, usually by 10-20x: 140 s then 11 s at
    Lucknow, 92 s then 7 s at Hyderabad, 167 s then 61 s at Jaipur. Two
    non-overlapping windows miss each other in the GDAL block cache, which is
    what they were for, but they do not miss each other's HTTPS connection to
    the same S3 host. Warm-up is an order of magnitude; concurrency would be a
    few percent.

    Alternating which method goes first does not rescue it at n=7 -- it
    scrambles the effect rather than cancelling it, which is why the paired
    ratios came out spanning 0.07x to 4.76x with parallel "winning" 3 of 7.

**So the honest state is that this probe has not answered its question**, and
three designs have each failed differently: confounded by site, then by
within-pair order. Do not read a speedup out of it without first checking that
the first/second split reported below is small. From a connection like the one
this was written on, it will not be.

The warm-up effect is itself the useful finding, and it is not noise: a job on
a long-lived worker that has already talked to S3 is much faster than these
cold numbers suggest, which is why the deployed stack finishes an NDVI in 11 s
where a cold laptop took 352 s.

Run it before changing how bands are read, and again after.
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
    print(f"{'site':<12}{'scene':<28}{'seq s':>8}{'par s':>8}{'seq/par':>9}{'1st':>7}")

    ratios: list[float] = []
    seq_all: list[float] = []
    par_all: list[float] = []
    firsts: list[float] = []
    seconds: list[float] = []
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
        # Which physically ran first, regardless of which method it was.
        first, second = ((seq, par) if index % 2 else (par, seq))
        firsts.append(first)
        seconds.append(second)
        print(f"{name:<12}{scene.id:<28}{seq:8.1f}{par:8.1f}{seq / par:9.2f}"
              f"{'seq' if index % 2 else 'par':>7}")

    faster = sum(1 for r in ratios if r > 1.0)
    print(f"\n  sequential  median {statistics.median(seq_all):6.1f}s")
    print(f"  parallel    median {statistics.median(par_all):6.1f}s")
    print(f"\n  paired speedup: median {statistics.median(ratios):.2f}x, "
          f"range {min(ratios):.2f}-{max(ratios):.2f}")
    print(f"  parallel won on {faster}/{len(ratios)} sites")

    # The validity check. Whichever read happens first in a pair pays to open
    # the connection, and that cost has measured 10-20x the thing this probe is
    # trying to see. If it is large, the numbers above are measuring warm-up.
    second_won = sum(1 for f, s in zip(firsts, seconds) if s < f)
    print(f"\n  --- validity ---")
    print(f"  ran first   median {statistics.median(firsts):6.1f}s")
    print(f"  ran second  median {statistics.median(seconds):6.1f}s  "
          f"(faster at {second_won}/{len(firsts)} sites)")
    warm = statistics.median(firsts) / statistics.median(seconds)
    print(f"  order effect: {warm:.2f}x")
    if warm > 1.5 or warm < 0.67:
        print("\n  ORDER DOMINATES. The connection warm-up between the two reads of\n"
              "  a pair is larger than any difference between the methods, so the\n"
              "  speedup above is not a measurement of concurrency. This is the\n"
              "  expected outcome on a high-latency link; run it from a host near\n"
              "  us-west-2 before drawing a conclusion.")
    else:
        print("\n  Order effect is small, so the per-site ratios are worth reading.\n"
              "  A real effect shows up as most sites landing on the same side of\n"
              "  1.00, not in the medians.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
