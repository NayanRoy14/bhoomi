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
