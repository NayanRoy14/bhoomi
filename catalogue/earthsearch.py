"""Element84 Earth Search -- the primary catalogue (PLAN.md D9).

Chosen over Planetary Computer because assets are readable anonymously with no
SAS-token signing. Measured 2026-07-30 from Kolkata: HTTP 200 in ~1.1 s.

Uses stdlib urllib rather than requests to keep the dependency surface small.
If connection pooling or richer retry behaviour is needed later, replace
``_post`` -- nothing else in this module touches the transport.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from .base import Catalogue, CatalogueError, Scene, SceneNotFoundError, SearchQuery

logger = logging.getLogger(__name__)

EARTH_SEARCH_V1 = "https://earth-search.aws.element84.com/v1"

#: Retried on transient failures. 5xx and timeouts are worth retrying; 4xx is not.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class EarthSearchCatalogue(Catalogue):
    name = "earth-search"

    def __init__(
        self,
        endpoint: str = EARTH_SEARCH_V1,
        timeout: float = 60.0,
        retries: int = 3,
        backoff: float = 1.5,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    # -- transport ---------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.endpoint}{path}"
        body = json.dumps(payload).encode()
        last: Exception | None = None

        for attempt in range(self.retries):
            request = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Accept": "application/geo+json,application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in RETRY_STATUS:
                    detail = exc.read()[:400].decode(errors="replace")
                    raise CatalogueError(
                        f"{self.name} returned HTTP {exc.code} for {path}: {detail}"
                    ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc

            if attempt < self.retries - 1:
                delay = self.backoff ** attempt
                logger.warning("%s %s failed (%s); retrying in %.1fs",
                               self.name, path, last, delay)
                time.sleep(delay)

        raise CatalogueError(
            f"{self.name} unreachable after {self.retries} attempts: {last}"
        ) from last

    # -- Catalogue protocol ------------------------------------------------

    def search(self, query: SearchQuery) -> list[Scene]:
        payload: dict = {
            "collections": list(query.collections),
            "bbox": list(query.bbox()),
            "limit": query.limit,
        }
        when = query.datetime_range()
        if when:
            payload["datetime"] = when
        if query.max_cloud is not None:
            payload["query"] = {"eo:cloud_cover": {"lt": query.max_cloud}}

        features = self._post("/search", payload).get("features", [])
        scenes = [Scene.from_stac_item(f, self.name) for f in features]
        scenes.sort(key=lambda s: s.acquired_at, reverse=True)
        logger.info("%s: %d scenes for %s", self.name, len(scenes), when or "any date")
        return scenes

    def get(self, scene_id: str, collection: str | None = None) -> Scene:
        payload = {
            "collections": [collection] if collection else ["sentinel-2-l2a"],
            "ids": [scene_id],
            "limit": 1,
        }
        features = self._post("/search", payload).get("features", [])
        if not features:
            raise SceneNotFoundError(
                f"{self.name} has no scene {scene_id!r} in "
                f"{collection or 'sentinel-2-l2a'}"
            )
        return Scene.from_stac_item(features[0], self.name)

    # -- convenience -------------------------------------------------------

    def search_best(
        self,
        query: SearchQuery,
        require_bands: tuple[str, ...] = (),
        min_coverage: float = 1.0,
        deduplicate: bool = True,
    ) -> Scene | None:
        """Lowest-cloud scene that fully contains the AOI and has the bands.

        ``min_coverage`` enforces PLAN.md D3: an AOI spanning two scenes is
        rejected here rather than silently producing a partial raster.

        ``deduplicate`` collapses repeat versions of the same acquisition --
        see :func:`deduplicate_by_acquisition`. Leave it on unless you
        specifically want to compare processing baselines.
        """
        scenes = self.search(query)
        if deduplicate:
            scenes = deduplicate_by_acquisition(scenes)
        candidates = [
            s for s in scenes
            if (not require_bands or s.has_bands(require_bands))
            and s.aoi_coverage(query.aoi) >= min_coverage
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda s: s.cloud_cover if s.cloud_cover is not None else 1e9)


def deduplicate_by_acquisition(scenes: list[Scene]) -> list[Scene]:
    """Keep one scene per acquisition, preferring the newest processing baseline.

    The archive serves the same acquisition more than once: 2020-03-10 over tile
    45QXF exists as both ``_0_`` (Sen2Cor 02.14) and ``_1_`` (05.00). They are
    genuinely different products, not two encodings -- median NDVI differs by
    ~0.014 and per-pixel values by up to +-3900 DN (PLAN.md 5.3).

    Selecting on cloud cover alone would pick between them essentially at
    random, and two such picks across dates would put a Sen2Cor version change
    inside a change-detection result. Preferring the newest baseline makes the
    choice deterministic and keeps a series internally consistent.
    """
    best: dict[tuple, Scene] = {}
    for scene in scenes:
        # Same instant and same footprint == same acquisition.
        key = (scene.acquired_at, tuple(round(v, 4) for v in scene.bbox))
        current = best.get(key)
        if current is None or _baseline_sort_key(scene) > _baseline_sort_key(current):
            best[key] = scene
    return sorted(best.values(), key=lambda s: s.acquired_at, reverse=True)


def _baseline_sort_key(scene: Scene) -> tuple[int, int]:
    """Order baselines numerically. '05.10' must rank above '05.09'."""
    raw = scene.processing_baseline
    if not raw:
        return (-1, -1)
    major, _, minor = raw.partition(".")
    try:
        return (int(major), int(minor or 0))
    except ValueError:
        return (-1, -1)
