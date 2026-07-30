"""Turning a scene id back into a Scene.

Wanted in two places that must not import each other: the API validates a
submission against the scene's footprint, and the worker — a separate process
with no request context — needs the band hrefs to read. Keeping the lookup
here means both get the same cache-first behaviour and the same catalogue
configuration.

Cache first is the whole point of the `scenes` table (PLAN.md 6): the ids in a
submission came from a search that already wrote them, so the common path is
one local query rather than a STAC round trip. A miss is not an error — a
client may submit an id it obtained hours ago — so it falls through.
"""

from __future__ import annotations

import logging
import os

from backend.db.scenes import SceneStore, default_scene_store
from catalogue import Catalogue, EarthSearchCatalogue, Scene

logger = logging.getLogger(__name__)


def default_catalogue() -> Catalogue:
    """The configured catalogue. `BHOOMI_STAC_ENDPOINT` overrides the default."""
    endpoint = os.getenv("BHOOMI_STAC_ENDPOINT", "")
    return EarthSearchCatalogue(endpoint=endpoint) if endpoint else EarthSearchCatalogue()


def resolve_scene(scene_id: str, store: SceneStore | None = None,
                  catalogue: Catalogue | None = None) -> Scene:
    """The scene, from cache if it is there and from the catalogue if not.

    Raises SceneNotFoundError when neither has it, which the API turns into a
    404 and the worker records as a job failure.
    """
    store = store if store is not None else default_scene_store()
    cached = store.get(scene_id)
    if cached is not None:
        return cached

    logger.info("scene %s not cached; asking the catalogue", scene_id)
    catalogue = catalogue if catalogue is not None else default_catalogue()
    return catalogue.get(scene_id)
