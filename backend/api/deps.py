"""Dependency providers.

The catalogue is injected rather than constructed inline so tests can substitute
a stub, and so Bhoonidhi can later be selected by configuration without touching
the routes.
"""

from __future__ import annotations

import os
from functools import lru_cache

from catalogue import Catalogue, EarthSearchCatalogue

CATALOGUE_ENDPOINT = os.getenv("BHOOMI_STAC_ENDPOINT", "")


@lru_cache(maxsize=1)
def _default_catalogue() -> Catalogue:
    if CATALOGUE_ENDPOINT:
        return EarthSearchCatalogue(endpoint=CATALOGUE_ENDPOINT)
    return EarthSearchCatalogue()


def get_catalogue() -> Catalogue:
    """Override in tests with `app.dependency_overrides[get_catalogue] = ...`."""
    return _default_catalogue()
