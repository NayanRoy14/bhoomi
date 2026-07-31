"""Tests for tile URL construction (PLAN.md 7.5, 11).

No TiTiler involved: what is worth checking here is the contract around it --
that tiles degrade to null rather than to a broken link, that the ranges are
fixed rather than per-image, and that a source containing a path separator or a
query string survives being put in a query parameter.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from backend import storage, tiles


@pytest.fixture
def titiler(monkeypatch):
    monkeypatch.setattr(tiles, "TITILER_URL", "http://tiles.test")


class TestAvailability:
    def test_no_tile_server_means_no_tiles(self, monkeypatch):
        """Null, not a URL that 404s -- the UI says so rather than offering it."""
        monkeypatch.setattr(tiles, "TITILER_URL", "")
        assert tiles.tiles_url("ndvi", "/app/outputs/jobs/a.tif") is None

    def test_no_source_means_no_tiles(self, titiler):
        """A backend that cannot expose the object to a tile server."""
        assert tiles.tiles_url("ndvi", None) is None

    def test_configured_reflects_the_setting(self, monkeypatch):
        monkeypatch.setattr(tiles, "TITILER_URL", "")
        assert tiles.configured() is False
        monkeypatch.setattr(tiles, "TITILER_URL", "http://tiles.test")
        assert tiles.configured() is True


class TestUrlShape:
    def test_it_is_an_xyz_template(self, titiler):
        url = tiles.tiles_url("ndvi", "/app/outputs/jobs/a.tif")
        assert "{z}/{x}/{y}" in url
        assert url.startswith("http://tiles.test/cog/tiles/WebMercatorQuad/")

    def test_a_trailing_slash_does_not_double_up(self, monkeypatch):
        monkeypatch.setattr(tiles, "TITILER_URL", "http://tiles.test/")
        assert "//cog" not in tiles.tiles_url("ndvi", "/a.tif")

    def test_the_source_is_percent_encoded(self, titiler):
        """A path separator left raw would end the parameter early."""
        url = tiles.tiles_url("ndvi", "/app/outputs/jobs/a.tif")
        assert "%2Fapp%2Foutputs" in url

    def test_a_presigned_url_survives_encoding(self, titiler):
        """What O4 will produce: a URL with its own query string inside ours."""
        source = "https://r2.example/bucket/a.tif?X-Amz-Signature=abc&x=1"
        url = tiles.tiles_url("ndvi", source)
        # The inner query must not become part of the outer one.
        params = parse_qs(urlparse(url).query)
        assert params["url"] == [source]
        assert "X-Amz-Signature" not in params

    def test_the_template_braces_are_not_encoded(self, titiler):
        """MapLibre substitutes {z}/{x}/{y}; encoding them would break it."""
        url = tiles.tiles_url("ndvi", "/a.tif")
        assert "%7Bz%7D" not in url


class TestRendering:
    def test_ndvi_is_green(self, titiler):
        assert "colormap_name=rdylgn" in tiles.tiles_url("ndvi", "/a.tif")

    def test_water_and_built_up_differ_from_vegetation(self):
        """The sign should read correctly at a glance for each index."""
        assert tiles.render_for("ndwi")[0] != tiles.render_for("ndvi")[0]
        assert tiles.render_for("ndbi")[0] != tiles.render_for("ndvi")[0]

    def test_every_index_is_rescaled_to_the_full_valid_range(self):
        """Fixed, not per-image: a per-tile stretch would make two dates of the
        same AOI incomparable, which is the point of the February swipe."""
        for process in ("ndvi", "ndwi", "ndbi"):
            assert tiles.render_for(process)[1] == (-1.0, 1.0)

    def test_the_rescale_reaches_the_url(self, titiler):
        assert "rescale=-1.0,1.0" in tiles.tiles_url("ndvi", "/a.tif")

    def test_an_unknown_process_still_renders(self, titiler):
        """Better a default ramp than no tiles when a process is added."""
        url = tiles.tiles_url("something_new", "/a.tif")
        assert url is not None and "colormap_name=" in url


class TestTileSource:
    def test_a_missing_object_has_no_source(self, tmp_path):
        local = storage.LocalStorage(tmp_path)
        assert local.tile_source("never-written.tif") is None

    def test_a_stored_object_resolves_to_its_path(self, tmp_path):
        local = storage.LocalStorage(tmp_path / "jobs")
        source = tmp_path / "src.tif"
        source.write_bytes(b"x" * 8)
        local.put(source, "job.tif")
        assert local.tile_source("job.tif").endswith("jobs/job.tif")

    def test_the_tile_root_can_differ_from_ours(self, tmp_path, monkeypatch):
        """The two containers share a volume; they need not mount it alike."""
        local = storage.LocalStorage(tmp_path / "jobs")
        source = tmp_path / "src.tif"
        source.write_bytes(b"x" * 8)
        local.put(source, "job.tif")

        monkeypatch.setenv("BHOOMI_TILE_ROOT", "/mounted/elsewhere")
        assert local.tile_source("job.tif") == "/mounted/elsewhere/job.tif"
