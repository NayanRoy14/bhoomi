"""Turning a finished COG into an XYZ tile URL (PLAN.md 7.5, 11).

TiTiler does the rendering. This module only knows the URL shape and what each
index should look like -- which belongs neither in `storage` (bytes) nor in
`queue/processes` (computation), and is read by the API when it serves 7.5.

**Optional.** With `BHOOMI_TITILER_URL` unset, `tiles_url` returns None, the
`tiles` field stays null, and the frontend says so rather than offering a link
that 404s. That is the configuration a bare `uvicorn` run has, and it is why
the field was nullable from the start.

## Why the ranges are fixed rather than per-image

TiTiler will happily stretch each tile to its own min/max. That produces a
picture that looks better and means less: two dates of the same AOI would get
different scales, so a visual comparison would be measuring the stretch rather
than the ground. Normalised indices are already bounded to [-1, 1] by
construction, so a fixed range is both honest and comparable -- and it is what
makes the swipe comparison in February meaningful.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

logger = logging.getLogger(__name__)

#: Where the tile server is reachable *from a browser*. Like the API's public
#: base URL, this cannot be a compose service name.
TITILER_URL = os.getenv("BHOOMI_TITILER_URL", "")

#: WebMercatorQuad is the tile matrix set every web map expects; MapLibre reads
#: {z}/{x}/{y} directly and TiTiler names the scheme explicitly.
TILE_PATH = "/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png"

#: (colormap, rescale) per process. All three indices are bounded to [-1, 1].
#:
#: The colormaps are chosen so the sign reads correctly at a glance rather than
#: for prettiness: green is more vegetation, blue is more water, red is more
#: built-up. A perceptually-uniform ramp would be better for reading magnitude,
#: but these are diverging quantities where the zero crossing is the thing the
#: eye should find first.
RENDER: dict[str, tuple[str, tuple[float, float]]] = {
    "ndvi": ("rdylgn", (-1.0, 1.0)),
    "ndwi": ("rdbu", (-1.0, 1.0)),
    "ndbi": ("rdylbu_r", (-1.0, 1.0)),
    # A difference of two indices, so mathematically it spans [-2, 2] -- but
    # rendering that range puts every real change into a narrow washed-out band
    # around the middle. §5.4.4 treats 0.2 as the threshold that matters, and
    # [-1, 1] makes that plainly visible. Values beyond clip to the ends, which
    # is rare and only affects extremes that are already unambiguous.
    #
    # BrBG is diverging about zero: brown for a fall, green for a rise. For
    # NDVI that reads as vegetation lost and gained without a legend; for the
    # others the legend supplies the noun.
    "change": ("brbg", (-1.0, 1.0)),
}

DEFAULT_RENDER = ("viridis", (-1.0, 1.0))


def configured() -> bool:
    return bool(TITILER_URL)


def render_for(process: str) -> tuple[str, tuple[float, float]]:
    return RENDER.get(process, DEFAULT_RENDER)


def render_key(process: str, output_type: str | None = None) -> str:
    """Which ramp an output wants, which is not always the job's process.

    A change job publishes three rasters: the difference, and the two per-date
    index rasters it was taken between. Rendering an NDVI raster with the
    change ramp would put a healthy field in the brown "vegetation lost" half
    of the scale -- the picture would be wrong in the one way a colour ramp
    can be, by inverting the reading.

    Per-date outputs are named `earlier_<index>` / `later_<index>`, so the
    index they carry is in the name and needs no extra column.
    """
    if output_type:
        for prefix in ("earlier_", "later_"):
            if output_type.startswith(prefix):
                return output_type[len(prefix):]
        if output_type == "change_raster":
            return "change"
    return process


#: TiTiler's COG part endpoint (verified against 2.2.1's factory.py). Crops to a
#: bbox given in `coord-crs`, which defaults to EPSG:4326 -- the CRS a STAC bbox
#: is already in, so no reprojection is needed on our side.
BBOX_PATH = "/cog/bbox/{minx},{miny},{maxx},{maxy}.png"

#: Longest edge of a scene preview, in pixels.
#:
#: Sized against the box it lands in rather than against the sensor. `.thumb` in
#: globals.css is 62x62 with `object-fit: cover`, so 256 is already 4x the CSS
#: pixels and 2x a retina panel -- and the search list requests one of these per
#: scene, each one a fresh set of range reads across the Pacific on an instance
#: with 0.1 CPU. Measured on a live TiTiler: 256 is about 35 KB against 115 KB
#: at 512, for a picture nobody sees at either size.
#:
#: Over the Rajarhat demo AOI this is still roughly 72 m per pixel against the
#: STAC thumbnail's 320 m, and unlike the thumbnail it gets *sharper* as the AOI
#: gets smaller, because the same budget covers less ground. That relationship
#: is the fix; the exact number is a tradeoff and safe to raise if the UI ever
#: shows these larger than a list row.
PREVIEW_MAX_SIZE = 256


def preview_url(visual_href: str | None, bbox: tuple[float, float, float, float] | None
                ) -> str | None:
    """A preview of just the AOI, cut from the scene's 10 m true-colour COG.

    **Why this exists.** Sentinel-2's STAC `thumbnail` asset is a 343x343 JPEG
    of the *entire* tile, which is about 110 km across -- roughly 320 m per
    pixel. It does not know about the AOI, so the smaller the area someone
    draws, the smaller the fraction of that image their area occupies: the
    Rajarhat demo AOI lands on about 54x45 of those pixels, and a 2 km box on
    a handful. Measured, not estimated -- the numbers are from the asset itself.

    The `visual` asset is the same scene as a 10 m three-band uint8 TCI COG.
    Cropping it to the AOI gives a picture whose resolution is set by how much
    ground was asked for rather than by how big the tile happens to be, which
    inverts the relationship: a smaller AOI now looks *better*, not worse.

    No `rescale` and no `colormap_name`: TCI is already 8-bit display-ready RGB,
    unlike the index rasters `tiles_url` serves. Passing a rescale here would
    stretch a correct image into a wrong one.

    Returns None when there is no tile server or no `visual` asset, and the
    caller falls back to the JPEG. A preview is cosmetic -- it must never be the
    reason a search fails.
    """
    if not TITILER_URL or not visual_href or bbox is None:
        return None

    minx, miny, maxx, maxy = bbox
    # A degenerate bbox would make TiTiler read a zero-width window and 500.
    if not (maxx > minx and maxy > miny):
        return None

    path = BBOX_PATH.format(minx=f"{minx:.6f}", miny=f"{miny:.6f}",
                            maxx=f"{maxx:.6f}", maxy=f"{maxy:.6f}")
    return (
        f"{TITILER_URL.rstrip('/')}{path}"
        f"?url={quote(visual_href, safe='')}"
        f"&max_size={PREVIEW_MAX_SIZE}"
    )


def tiles_url(process: str, source: str | None) -> str | None:
    """An XYZ template for this output, or None if tiles are unavailable.

    `source` is whatever the storage backend says the tile server can open --
    a path today, an https URL once O4 lands. Either way it is percent-encoded
    into the query string, because a Windows path or a presigned URL both
    contain characters that would otherwise end the parameter early.
    """
    if not TITILER_URL or not source:
        return None

    colormap, (minimum, maximum) = render_for(process)
    return (
        f"{TITILER_URL.rstrip('/')}{TILE_PATH}"
        f"?url={quote(source, safe='')}"
        f"&rescale={minimum},{maximum}"
        f"&colormap_name={colormap}"
    )
