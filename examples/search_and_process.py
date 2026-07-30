"""End to end from a search: AOI + dates in, Cloud-Optimized GeoTIFF out.

This is the December milestone's data path, exercised from a script instead of
a web request. Nothing here supplies a scene id or an asset URL by hand -- the
catalogue resolves everything, including the reflectance convention, which is
detected from the scene's own pixels.

    AOI + date range
        -> STAC search              catalogue/
        -> pick lowest-cloud scene that fully contains the AOI   (D3)
        -> read only the AOI window from the remote COGs
        -> mask, harmonise, compute NDVI                          processing/
        -> write and validate a COG
"""

from __future__ import annotations

import logging
import sys

sys.path.insert(0, r"D:\Bhoomi")

import pipeline  # noqa: E402
from catalogue import EarthSearchCatalogue, SearchQuery  # noqa: E402
from processing import cog  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

OUT = r"D:\Bhoomi\outputs"
AOI = {  # D13: New Town / Rajarhat, Kolkata
    "type": "Polygon",
    "coordinates": [[[88.35, 22.55], [88.52, 22.55], [88.52, 22.68],
                     [88.35, 22.68], [88.35, 22.55]]],
}


def main() -> None:
    catalogue = EarthSearchCatalogue()

    query = SearchQuery(aoi=AOI, start="2020-02-15", end="2020-03-31",
                        max_cloud=10, limit=50)
    scenes = catalogue.search(query)
    print(f"\n{len(scenes)} scenes matched\n")
    print(f"  {'id':<32}{'date':<12}{'cloud':>7}{'coverage':>10}{'baseline':>10}")
    for s in scenes[:8]:
        print(f"  {s.id:<32}{s.acquired_at:%Y-%m-%d}  {s.cloud_cover:6.3f}%"
              f"{s.aoi_coverage(AOI):9.1%}{s.processing_baseline:>10}")

    scene = catalogue.search_best(query, require_bands=("nir", "red"))
    if scene is None:
        print("\nNo scene fully contains the AOI. Reduce it or widen the dates.")
        return

    print(f"\nselected: {scene!r}")
    print(f"  coverage {scene.aoi_coverage(AOI):.1%}  satellite {scene.satellite}\n")

    result = pipeline.compute_index(scene, AOI, "ndvi")

    print(f"\ngrid   : {result.grid.width}x{result.grid.height} @ "
          f"{result.grid.resolution} m  {result.grid.crs}")
    print(f"offset : present={result.offset_present} (detected from pixels)")
    print(f"valid  : {result.valid_fraction:.2%} after masking")
    stats = result.stats()
    print(f"ndvi   : median {stats['median']:.3f}  mean {stats['mean']:.3f}  "
          f"range {stats['min']:.3f}..{stats['max']:.3f}")
    for w in result.warnings:
        print(f"WARNING: {w}")

    path = result.write(rf"{OUT}\ndvi_from_search.tif")
    ok, messages = cog.validate_cog(path)
    print(f"\nwrote {path.name}  valid COG: {ok} {messages if messages else ''}")
    print("\nexpected median ~0.327 (matches examples/kolkata_change.py)")


if __name__ == "__main__":
    main()
