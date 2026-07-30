"""API tests. No network -- the catalogue dependency is overridden."""

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_catalogue
from backend.api.main import app
from catalogue import CatalogueError, Scene, SearchQuery
from tests.test_catalogue import KOLKATA_AOI, TILE_45QXE, TILE_45QXF, item


class StubCatalogue:
    name = "stub"

    def __init__(self, scenes=(), raises=None):
        self.scenes = list(scenes)
        self.raises = raises
        self.queries: list[SearchQuery] = []

    def search(self, query):
        self.queries.append(query)
        if self.raises:
            raise self.raises
        return self.scenes

    def get(self, scene_id, collection=None):
        if self.raises:
            raise self.raises
        return next(s for s in self.scenes if s.id == scene_id)


def make_client(catalogue):
    app.dependency_overrides[get_catalogue] = lambda: catalogue
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def scenes():
    return [
        Scene.from_stac_item(item("S2A_45QXF_20200310_1_L2A", geometry=TILE_45QXF,
                                  cloud=0.0, assets=("red", "nir", "swir16", "scl",
                                                     "thumbnail"))),
        Scene.from_stac_item(item("S2A_45QXE_20200310_1_L2A", geometry=TILE_45QXE,
                                  cloud=3.5, assets=("red", "nir", "scl"))),
    ]


@pytest.fixture
def client(scenes):
    yield from make_client(StubCatalogue(scenes))


def body(**overrides):
    payload = {"aoi": KOLKATA_AOI, "start_date": "2020-02-15",
               "end_date": "2020-03-31", "max_cloud": 10}
    payload.update(overrides)
    return payload


class TestHealth:
    def test_reports_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["catalogue"] == "stub"

    def test_queue_fields_are_null_until_january(self, client):
        assert client.get("/health").json()["queue_depth"] is None

    def test_does_not_call_the_catalogue(self, scenes):
        """A health check that depends on a third party reports their outage as ours."""
        stub = StubCatalogue(scenes, raises=CatalogueError("upstream down"))
        app.dependency_overrides[get_catalogue] = lambda: stub
        try:
            assert TestClient(app).get("/health").status_code == 200
        finally:
            app.dependency_overrides.clear()


class TestSceneSearch:
    def test_returns_scenes(self, client):
        response = client.post("/api/v1/scenes/search", json=body())
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        # True polygon area in UTM. Slightly under the ~257 km2 quoted elsewhere
        # for the same AOI, which is the *grid* area after snapping the bounds
        # outward to whole pixels.
        assert data["aoi_area_km2"] == pytest.approx(251.5, abs=1.0)

    def test_coverage_distinguishes_usable_scenes(self, client):
        found = {s["id"]: s for s in
                 client.post("/api/v1/scenes/search", json=body()).json()["scenes"]}
        assert found["S2A_45QXF_20200310_1_L2A"]["aoi_coverage"] == pytest.approx(1.0)
        assert found["S2A_45QXE_20200310_1_L2A"]["aoi_coverage"] < 0.5

    def test_partial_scenes_are_returned_not_hidden(self, client):
        """The UI must be able to explain why a scene is unusable."""
        ids = [s["id"] for s in
               client.post("/api/v1/scenes/search", json=body()).json()["scenes"]]
        assert "S2A_45QXE_20200310_1_L2A" in ids

    def test_available_processes_reflect_bands(self, client):
        found = {s["id"]: s for s in
                 client.post("/api/v1/scenes/search", json=body()).json()["scenes"]}
        # Has swir16, so NDBI is possible.
        assert "ndbi" in found["S2A_45QXF_20200310_1_L2A"]["available_processes"]
        # No swir16, so it is not.
        assert "ndbi" not in found["S2A_45QXE_20200310_1_L2A"]["available_processes"]
        assert "ndvi" in found["S2A_45QXE_20200310_1_L2A"]["available_processes"]

    def test_thumbnail_surfaced_when_present(self, client):
        found = {s["id"]: s for s in
                 client.post("/api/v1/scenes/search", json=body()).json()["scenes"]}
        assert found["S2A_45QXF_20200310_1_L2A"]["thumbnail"]
        assert found["S2A_45QXE_20200310_1_L2A"]["thumbnail"] is None

    def test_query_is_forwarded(self, scenes):
        stub = StubCatalogue(scenes)
        app.dependency_overrides[get_catalogue] = lambda: stub
        try:
            TestClient(app).post("/api/v1/scenes/search", json=body(max_cloud=5))
            assert stub.queries[0].max_cloud == 5
            assert stub.queries[0].start == "2020-02-15"
        finally:
            app.dependency_overrides.clear()


class TestDeduplication:
    """Same acquisition, two baselines -- the user must not choose between them."""

    @pytest.fixture
    def duplicated(self):
        return [
            Scene.from_stac_item(item("S2A_45QXF_20200330_0_L2A", geometry=TILE_45QXF,
                                      cloud=0.9, baseline="02.14",
                                      when="2020-03-30T04:42:43Z")),
            Scene.from_stac_item(item("S2A_45QXF_20200330_1_L2A", geometry=TILE_45QXF,
                                      cloud=1.0, baseline="05.00",
                                      when="2020-03-30T04:42:43Z")),
        ]

    def test_collapses_to_newest_baseline_by_default(self, duplicated):
        for client in make_client(StubCatalogue(duplicated)):
            data = client.post("/api/v1/scenes/search", json=body()).json()
            assert data["count"] == 1
            assert data["scenes"][0]["processing_baseline"] == "05.00"

    def test_lower_cloud_does_not_win_over_newer_baseline(self, duplicated):
        """The 02.14 version has less cloud; consistency still matters more."""
        for client in make_client(StubCatalogue(duplicated)):
            scene = client.post("/api/v1/scenes/search", json=body()).json()["scenes"][0]
            assert scene["id"].endswith("_1_L2A")

    def test_can_be_disabled(self, duplicated):
        for client in make_client(StubCatalogue(duplicated)):
            data = client.post("/api/v1/scenes/search",
                               json=body(deduplicate=False)).json()
            assert data["count"] == 2


class TestLimits:
    def test_oversized_aoi_states_size_and_limit(self, client):
        big = {"type": "Polygon", "coordinates": [[
            [88.0, 22.0], [89.0, 22.0], [89.0, 23.0], [88.0, 23.0], [88.0, 22.0]]]}
        response = client.post("/api/v1/scenes/search", json=body(aoi=big))
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "aoi_too_large"
        assert detail["limit_km2"] == 500
        assert detail["area_km2"] > 500
        assert "Draw a smaller area" in detail["message"]

    def test_reversed_dates_rejected(self, client):
        response = client.post("/api/v1/scenes/search",
                               json=body(start_date="2020-06-01", end_date="2020-01-01"))
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_date_range"

    def test_date_range_over_a_year_rejected(self, client):
        response = client.post("/api/v1/scenes/search",
                               json=body(start_date="2020-01-01", end_date="2022-01-01"))
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "date_range_too_long"

    def test_unclosed_polygon_rejected(self, client):
        bad = {"type": "Polygon", "coordinates": [[
            [88.35, 22.55], [88.52, 22.55], [88.52, 22.68], [88.35, 22.68]]]}
        assert client.post("/api/v1/scenes/search", json=body(aoi=bad)).status_code == 422

    def test_limit_above_maximum_rejected(self, client):
        assert client.post("/api/v1/scenes/search",
                           json=body(limit=500)).status_code == 422

    def test_cloud_out_of_range_rejected(self, client):
        assert client.post("/api/v1/scenes/search",
                           json=body(max_cloud=150)).status_code == 422


class TestUpstreamFailures:
    def test_catalogue_outage_is_502_not_500(self, scenes):
        stub = StubCatalogue(scenes, raises=CatalogueError("connection refused"))
        app.dependency_overrides[get_catalogue] = lambda: stub
        try:
            response = TestClient(app, raise_server_exceptions=False).post(
                "/api/v1/scenes/search", json=body())
            assert response.status_code == 502
            assert response.json()["code"] == "catalogue_unavailable"
        finally:
            app.dependency_overrides.clear()


class TestOpenAPI:
    def test_schema_is_generated(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/v1/scenes/search" in schema["paths"]
        assert schema["info"]["title"] == "Bhoomi API"
