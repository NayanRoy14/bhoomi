"""Bhoomi catalogue clients.

Layering:

    catalogue/   metadata -- what scenes exist, and where their bands live
    processing/  mathematics -- masking, harmonisation, indices, COG output
    pipeline.py  composition -- ties the two together

Neither library imports the other. ``pipeline`` imports both. The FastAPI
backend will import ``pipeline`` and add nothing but HTTP.
"""

from .base import (
    Catalogue,
    CatalogueError,
    Scene,
    SceneNotFoundError,
    SearchQuery,
)
from .earthsearch import EARTH_SEARCH_V1, EarthSearchCatalogue

__all__ = [
    "Catalogue", "CatalogueError", "Scene", "SceneNotFoundError", "SearchQuery",
    "EARTH_SEARCH_V1", "EarthSearchCatalogue",
]
