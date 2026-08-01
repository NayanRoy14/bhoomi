"""AOI-cropped scene previews.

The defect these cover, measured against the real asset on 2026-08-01: the
Sentinel-2 STAC `thumbnail` is a 343x343 JPEG of the whole ~110 km tile, about
320 m per pixel, and it is the same image whatever AOI was drawn. A 0.17 x 0.13
degree AOI therefore lands on roughly 54x45 of those pixels, and a small one on
a handful -- so the *smaller* the area asked for, the worse the picture. The
crop inverts that.
"""

from __future__ import annotations

import pytest

from backend import tiles
from backend.api.routes import scenes as scenes_route

TCI = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/45/Q/XE/TCI.tif"

#: The Rajarhat demo AOI (D13) and a tile that contains it.
AOI = (88.35, 22.55, 88.52, 22.68)
TILE = (88.0, 22.5, 89.1, 23.5)


@pytest.fixture
def titiler(monkeypatch):
    monkeypatch.setattr(tiles, "TITILER_URL", "https://tiles.example.com")


class _Scene:
    """Only what `_preview` reads."""

    def __init__(self, assets: dict, bbox=TILE) -> None:
        self.assets = assets
        self.bbox = bbox


# ------------------------------------------------------------------ url shape


def test_preview_url_crops_to_the_bbox(titiler):
    url = tiles.preview_url(TCI, AOI)
    assert url is not None
    # The verified TiTiler 2.2.1 part endpoint, not the older /cog/crop.
    assert url.startswith("https://tiles.example.com/cog/bbox/")
    assert "88.350000,22.550000,88.520000,22.680000.png" in url
    assert f"max_size={tiles.PREVIEW_MAX_SIZE}" in url


def test_preview_url_percent_encodes_the_source(titiler):
    url = tiles.preview_url(TCI, AOI)
    # Unencoded, the :// and any & in a signed href would end the parameter.
    assert "url=https%3A%2F%2F" in url
    assert TCI not in url


def test_preview_url_sends_no_rescale_or_colormap(titiler):
    """TCI is 8-bit display RGB. `tiles_url` rescales because an index is a
    float in [-1, 1]; doing the same here would corrupt a correct image."""
    url = tiles.preview_url(TCI, AOI)
    assert "rescale" not in url
    assert "colormap_name" not in url


def test_preview_url_needs_a_tile_server():
    """Unset TITILER_URL is the default everywhere, including a bare uvicorn."""
    assert tiles.preview_url(TCI, AOI) is None


def test_preview_url_without_a_visual_asset(titiler):
    assert tiles.preview_url(None, AOI) is None


@pytest.mark.parametrize("bbox", [
    (88.4, 22.5, 88.4, 22.6),   # zero width
    (88.4, 22.6, 88.5, 22.6),   # zero height
    (88.5, 22.5, 88.4, 22.6),   # inverted
])
def test_preview_url_refuses_a_degenerate_bbox(titiler, bbox):
    """A zero-width window makes TiTiler read nothing and answer 500."""
    assert tiles.preview_url(TCI, bbox) is None


# -------------------------------------------------------------------- overlap


def test_overlap_clips_a_partial_scene():
    """A scene covering the left half must be cropped to the half it has, not
    to the whole AOI -- otherwise the preview is mostly nodata and reads as a
    broken scene rather than a partial one."""
    half = (88.0, 22.5, 88.42, 23.5)
    assert scenes_route._overlap(AOI, half) == (88.35, 22.55, 88.42, 22.68)


def test_overlap_of_disjoint_boxes_is_none():
    assert scenes_route._overlap(AOI, (90.0, 25.0, 91.0, 26.0)) is None


def test_overlap_touching_edges_is_none():
    """Sharing an edge is zero area, which is degenerate, not an overlap."""
    assert scenes_route._overlap(AOI, (88.52, 22.55, 89.0, 22.68)) is None


def test_aoi_bounds_of_a_polygon():
    poly = {"type": "Polygon", "coordinates": [[[88.35, 22.55], [88.52, 22.55],
                                                [88.52, 22.68], [88.35, 22.68],
                                                [88.35, 22.55]]]}
    assert scenes_route._aoi_bounds(poly) == AOI


def test_aoi_bounds_of_an_empty_geometry():
    assert scenes_route._aoi_bounds({"type": "Polygon", "coordinates": []}) is None


# --------------------------------------------------------------- selection


def test_preview_prefers_the_crop(titiler):
    scene = _Scene({"visual": TCI, "thumbnail": "https://example.com/thumb.jpg"})
    assert scenes_route._preview(scene, AOI).startswith("https://tiles.example.com/")


def test_preview_falls_back_to_the_jpeg_without_a_tile_server():
    """The deployment's state today: no tile server, so the old behaviour."""
    scene = _Scene({"visual": TCI, "thumbnail": "https://example.com/thumb.jpg"})
    assert scenes_route._preview(scene, AOI) == "https://example.com/thumb.jpg"


def test_preview_falls_back_when_the_scene_has_no_visual(titiler):
    scene = _Scene({"thumbnail": "https://example.com/thumb.jpg"})
    assert scenes_route._preview(scene, AOI) == "https://example.com/thumb.jpg"


def test_preview_falls_back_when_the_scene_misses_the_aoi(titiler):
    """Should not happen -- search returns intersecting scenes -- but a preview
    must never be the reason a search fails."""
    scene = _Scene({"visual": TCI}, bbox=(90.0, 25.0, 91.0, 26.0))
    assert scenes_route._preview(scene, AOI) == TCI  # _thumbnail's last resort


def test_preview_without_an_aoi_bbox(titiler):
    scene = _Scene({"visual": TCI, "thumbnail": "https://example.com/thumb.jpg"})
    assert scenes_route._preview(scene, None) == "https://example.com/thumb.jpg"
