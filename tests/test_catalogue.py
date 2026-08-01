"""Tests for the STAC catalogue client. No network -- the transport is stubbed."""

import json
from datetime import datetime, timezone

import pytest

from catalogue import (
    CatalogueError,
    EarthSearchCatalogue,
    Scene,
    SceneNotFoundError,
    SearchQuery,
)

KOLKATA_AOI = {
    "type": "Polygon",
    "coordinates": [[[88.35, 22.55], [88.52, 22.55], [88.52, 22.68],
                     [88.35, 22.68], [88.35, 22.55]]],
}

# Tile 45QXF, which fully contains the demo AOI.
TILE_45QXF = {
    "type": "Polygon",
    "coordinates": [[[87.97, 22.51], [89.05, 22.51], [89.05, 23.51],
                     [87.97, 23.51], [87.97, 22.51]]],
}
# Tile 45QXE, whose top edge cuts through the AOI -- the D3 rejection case.
TILE_45QXE = {
    "type": "Polygon",
    "coordinates": [[[87.97, 21.60], [89.04, 21.60], [89.04, 22.60],
                     [87.97, 22.60], [87.97, 21.60]]],
}


def bbox_of(geometry):
    """Derive the bbox from the ring, so scenes with different footprints get
    different bboxes. Deduplication keys on (time, bbox); a hardcoded bbox made
    two distinct tiles look like one acquisition."""
    ring = geometry["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return [min(lons), min(lats), max(lons), max(lats)]


def item(scene_id, geometry=TILE_45QXF, cloud=0.0, baseline="05.00",
         when="2020-03-10T04:42:43Z", assets=("red", "nir", "scl")):
    return {
        "id": scene_id,
        "collection": "sentinel-2-l2a",
        "bbox": bbox_of(geometry),
        "geometry": geometry,
        "properties": {
            "datetime": when,
            "eo:cloud_cover": cloud,
            "s2:processing_baseline": baseline,
            "platform": "sentinel-2a",
        },
        "assets": {a: {"href": f"https://example.test/{scene_id}/{a}.tif"} for a in assets},
    }


class StubCatalogue(EarthSearchCatalogue):
    """EarthSearchCatalogue with the transport replaced."""

    def __init__(self, features):
        super().__init__(retries=1)
        self.features = features
        self.calls = []

    def _post(self, path, payload):
        self.calls.append((path, payload))
        ids = payload.get("ids")
        feats = ([f for f in self.features if f["id"] in ids] if ids else self.features)
        return {"features": feats}


class TestSearchQuery:
    def test_bbox_from_polygon(self):
        q = SearchQuery(aoi=KOLKATA_AOI)
        assert q.bbox() == pytest.approx((88.35, 22.55, 88.52, 22.68))

    def test_datetime_range(self):
        q = SearchQuery(aoi=KOLKATA_AOI, start="2020-01-01", end="2020-12-31")
        assert q.datetime_range() == "2020-01-01T00:00:00Z/2020-12-31T23:59:59Z"

    def test_open_ended_range(self):
        assert SearchQuery(aoi=KOLKATA_AOI, start="2020-01-01").datetime_range() \
            == "2020-01-01T00:00:00Z/.."

    def test_no_dates_means_no_filter(self):
        assert SearchQuery(aoi=KOLKATA_AOI).datetime_range() is None


class TestScene:
    def test_from_stac_item(self):
        s = Scene.from_stac_item(item("S2A_TEST"), "earth-search")
        assert s.id == "S2A_TEST"
        assert s.cloud_cover == 0.0
        assert s.processing_baseline == "05.00"
        assert s.acquired_at.year == 2020
        assert s.href("red").endswith("/red.tif")

    def test_missing_datetime_raises(self):
        bad = item("X")
        bad["properties"].pop("datetime")
        with pytest.raises(CatalogueError, match="no datetime"):
            Scene.from_stac_item(bad)

    def test_missing_band_raises_with_available_listed(self):
        s = Scene.from_stac_item(item("X", assets=("red",)))
        with pytest.raises(CatalogueError, match=r"Available: \['red'\]"):
            s.href("swir16")

    def test_has_bands(self):
        s = Scene.from_stac_item(item("X", assets=("red", "nir")))
        assert s.has_bands(("nir", "red"))
        assert not s.has_bands(("swir16", "nir"))

    def test_coverage_full_when_scene_contains_aoi(self):
        s = Scene.from_stac_item(item("X", geometry=TILE_45QXF))
        assert s.aoi_coverage(KOLKATA_AOI) == pytest.approx(1.0, abs=1e-6)

    def test_coverage_partial_across_tile_boundary(self):
        """45QXE's top edge is 22.60; the AOI runs to 22.68 (PLAN.md D11)."""
        s = Scene.from_stac_item(item("X", geometry=TILE_45QXE))
        coverage = s.aoi_coverage(KOLKATA_AOI)
        assert 0.3 < coverage < 0.5

    def test_utc_is_assumed_when_absent(self):
        s = Scene.from_stac_item(item("X", when="2020-03-10T04:42:43"))
        assert s.acquired_at.tzinfo is not None


class TestEarthSearch:
    def test_search_builds_expected_payload(self):
        cat = StubCatalogue([item("A")])
        cat.search(SearchQuery(aoi=KOLKATA_AOI, start="2020-01-01",
                               end="2020-12-31", max_cloud=20))
        _, payload = cat.calls[0]
        assert payload["collections"] == ["sentinel-2-l2a"]
        assert payload["bbox"] == pytest.approx([88.35, 22.55, 88.52, 22.68])
        assert payload["query"] == {"eo:cloud_cover": {"lt": 20}}

    def test_cloud_filter_omitted_when_unset(self):
        cat = StubCatalogue([item("A")])
        cat.search(SearchQuery(aoi=KOLKATA_AOI))
        assert "query" not in cat.calls[0][1]

    def test_results_sorted_newest_first(self):
        cat = StubCatalogue([
            item("old", when="2020-01-01T00:00:00Z"),
            item("new", when="2026-01-01T00:00:00Z"),
        ])
        assert [s.id for s in cat.search(SearchQuery(aoi=KOLKATA_AOI))] == ["new", "old"]

    def test_get_by_id(self):
        cat = StubCatalogue([item("A"), item("B")])
        assert cat.get("B").id == "B"

    def test_get_missing_raises(self):
        with pytest.raises(SceneNotFoundError, match="no scene"):
            StubCatalogue([item("A")]).get("nope")

    def test_search_best_picks_lowest_cloud(self):
        # Distinct acquisitions, or deduplication would collapse them first.
        cat = StubCatalogue([
            item("cloudy", cloud=40.0, when="2020-03-10T04:42:43Z"),
            item("clear", cloud=0.5, when="2020-03-20T04:42:43Z"),
        ])
        assert cat.search_best(SearchQuery(aoi=KOLKATA_AOI)).id == "clear"

    def test_search_best_rejects_partial_coverage(self):
        """A cloud-free scene that only half-covers the AOI is still unusable."""
        cat = StubCatalogue([item("partial", geometry=TILE_45QXE, cloud=0.0)])
        assert cat.search_best(SearchQuery(aoi=KOLKATA_AOI)) is None

    def test_search_best_requires_bands(self):
        cat = StubCatalogue([item("no_swir", assets=("red", "nir", "scl"))])
        q = SearchQuery(aoi=KOLKATA_AOI)
        assert cat.search_best(q, require_bands=("swir16", "nir")) is None
        assert cat.search_best(q, require_bands=("nir", "red")) is not None


class TestDeduplication:
    """The archive serves the same acquisition at multiple baselines."""

    def test_keeps_newest_baseline_for_one_acquisition(self):
        from catalogue.earthsearch import deduplicate_by_acquisition

        scenes = [Scene.from_stac_item(i) for i in (
            item("S2A_45QXF_20200310_0_L2A", baseline="02.14", cloud=0.014),
            item("S2A_45QXF_20200310_1_L2A", baseline="05.00", cloud=0.000),
        )]
        kept = deduplicate_by_acquisition(scenes)
        assert [s.id for s in kept] == ["S2A_45QXF_20200310_1_L2A"]

    def test_prefers_baseline_over_lower_cloud(self):
        """Deterministic beats marginally clearer -- consistency matters more."""
        from catalogue.earthsearch import deduplicate_by_acquisition

        scenes = [Scene.from_stac_item(i) for i in (
            item("old_but_clearer", baseline="02.14", cloud=0.001),
            item("new_baseline", baseline="05.00", cloud=0.900),
        )]
        assert deduplicate_by_acquisition(scenes)[0].id == "new_baseline"

    def test_baselines_compare_numerically_not_as_strings(self):
        """'05.10' outranks '05.09'; string comparison gets this backwards."""
        from catalogue.earthsearch import deduplicate_by_acquisition

        scenes = [Scene.from_stac_item(i) for i in (
            item("b0510", baseline="05.10"),
            item("b0509", baseline="05.09"),
        )]
        assert deduplicate_by_acquisition(scenes)[0].id == "b0510"

    def test_millisecond_drift_still_collapses(self):
        """Reprocessed versions can differ by 1 ms -- measured on 45QXE."""
        from catalogue.earthsearch import deduplicate_by_acquisition

        a = item("S2A_45QXE_20200330_0_L2A", geometry=TILE_45QXE, baseline="02.14",
                 when="2020-03-30T04:52:25.488000Z")
        b = item("S2A_45QXE_20200330_1_L2A", geometry=TILE_45QXE, baseline="05.00",
                 when="2020-03-30T04:52:25.489000Z")
        a["properties"]["grid:code"] = b["properties"]["grid:code"] = "MGRS-45QXE"
        kept = deduplicate_by_acquisition([Scene.from_stac_item(a), Scene.from_stac_item(b)])
        assert [s.id for s in kept] == ["S2A_45QXE_20200330_1_L2A"]

    def test_millisecond_drift_collapses_across_a_second_boundary(self):
        """The same 1 ms drift, landing either side of a whole second.

        Grouping used to key on the timestamp truncated to the second, which
        absorbs the drift everywhere except here: `...25.9995` and `...26.0005`
        are one millisecond apart and truncate to 25 and 26, so both versions
        survived and reached the caller. They differ by up to +-3900 DN, so a
        user could pick the older baseline off the list, or a change job could
        pair one date's new baseline against another's old one -- the exact
        Sen2Cor drift de-duplication exists to keep out.

        Truncation does not remove the seam, it moves it. A tolerance has none.
        """
        from catalogue.earthsearch import deduplicate_by_acquisition

        a = item("S2A_45QXF_20200310_0_L2A", geometry=TILE_45QXF, baseline="02.14",
                 when="2020-03-10T04:52:25.999500Z")
        b = item("S2A_45QXF_20200310_1_L2A", geometry=TILE_45QXF, baseline="05.00",
                 when="2020-03-10T04:52:26.000500Z")
        a["properties"]["grid:code"] = b["properties"]["grid:code"] = "MGRS-45QXF"
        kept = deduplicate_by_acquisition([Scene.from_stac_item(a), Scene.from_stac_item(b)])
        assert [s.id for s in kept] == ["S2A_45QXF_20200310_1_L2A"]

    def test_a_tolerance_does_not_chain_into_one_cluster(self):
        """Each scene is compared to its cluster's first member, not its last.

        Comparing against the previous one would let a run of near-adjacent
        timestamps merge across an unbounded span, collapsing genuinely
        distinct acquisitions. Five scenes 0.6 s apart span 2.4 s and must not
        become one.
        """
        from datetime import timedelta

        from catalogue.earthsearch import ACQUISITION_TOLERANCE, deduplicate_by_acquisition

        step = ACQUISITION_TOLERANCE * 0.6
        base = datetime(2020, 3, 10, 4, 52, 25, tzinfo=timezone.utc)
        scenes = []
        for i in range(5):
            when = (base + step * i).isoformat().replace("+00:00", "Z")
            entry = item(f"c{i}", geometry=TILE_45QXF, baseline="05.00", when=when)
            entry["properties"]["grid:code"] = "MGRS-45QXF"
            scenes.append(Scene.from_stac_item(entry))

        kept = deduplicate_by_acquisition(scenes)
        assert len(kept) == 3, [s.id for s in kept]
        assert timedelta(0) < step < ACQUISITION_TOLERANCE

    def test_grid_code_separates_tiles_from_the_same_overpass(self):
        """Adjacent tiles are seconds apart; grid:code keeps them distinct."""
        from catalogue.earthsearch import deduplicate_by_acquisition

        a = item("xe", geometry=TILE_45QXE, when="2020-03-30T04:52:25.488000Z")
        b = item("xf", geometry=TILE_45QXF, when="2020-03-30T04:52:25.488000Z")
        a["properties"]["grid:code"] = "MGRS-45QXE"
        b["properties"]["grid:code"] = "MGRS-45QXF"
        kept = deduplicate_by_acquisition([Scene.from_stac_item(a), Scene.from_stac_item(b)])
        assert len(kept) == 2

    def test_distinct_acquisitions_are_both_kept(self):
        from catalogue.earthsearch import deduplicate_by_acquisition

        scenes = [Scene.from_stac_item(i) for i in (
            item("march", when="2020-03-10T04:42:43Z"),
            item("april", when="2020-04-10T04:42:43Z"),
        )]
        assert len(deduplicate_by_acquisition(scenes)) == 2

    def test_same_time_different_tile_both_kept(self):
        """Adjacent tiles are imaged in the same pass -- not duplicates."""
        from catalogue.earthsearch import deduplicate_by_acquisition

        a = item("tile_xf", geometry=TILE_45QXF)
        b = item("tile_xe", geometry=TILE_45QXE)
        b["bbox"] = [87.97, 21.60, 89.04, 22.60]
        scenes = [Scene.from_stac_item(a), Scene.from_stac_item(b)]
        assert len(deduplicate_by_acquisition(scenes)) == 2

    def test_search_best_deduplicates_by_default(self):
        cat = StubCatalogue([
            item("S2A_45QXF_20200310_0_L2A", baseline="02.14", cloud=0.0),
            item("S2A_45QXF_20200310_1_L2A", baseline="05.00", cloud=0.5),
        ])
        assert cat.search_best(SearchQuery(aoi=KOLKATA_AOI)).id \
            == "S2A_45QXF_20200310_1_L2A"
        # Opt out when the point is to compare baselines.
        assert cat.search_best(SearchQuery(aoi=KOLKATA_AOI), deduplicate=False).id \
            == "S2A_45QXF_20200310_0_L2A"


class TestTransport:
    def test_non_retryable_http_error_surfaces(self):
        import urllib.error

        class Failing(EarthSearchCatalogue):
            def _post(self, path, payload):
                raise urllib.error.HTTPError(path, 400, "Bad Request", {}, None)

        with pytest.raises(urllib.error.HTTPError):
            Failing().search(SearchQuery(aoi=KOLKATA_AOI))

    def test_payload_is_json_serialisable(self):
        q = SearchQuery(aoi=KOLKATA_AOI, start="2020-01-01", max_cloud=10)
        payload = {"collections": list(q.collections), "bbox": list(q.bbox()),
                   "datetime": q.datetime_range()}
        assert json.loads(json.dumps(payload))["bbox"][0] == pytest.approx(88.35)
