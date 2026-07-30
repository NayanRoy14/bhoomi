"""Dependency providers.

The catalogue is injected rather than constructed inline so tests can substitute
a stub, and so Bhoonidhi can later be selected by configuration without touching
the routes.
"""

from __future__ import annotations

import os
from functools import lru_cache

from backend.api import errors
from backend.db import SceneStore, default_scene_store
from backend.db.jobs import JobStore, jobs_available
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


def get_scene_store() -> SceneStore:
    """The scene metadata cache -- a null one when no database is configured.

    Resolved per request rather than cached, unlike the catalogue: it holds no
    connection of its own (the engine underneath is what is pooled), so the
    only cost is an env lookup, and it means BHOOMI_DATABASE_URL taking effect
    without a restart in development.
    """
    return default_scene_store()


def get_job_store() -> JobStore:
    """The jobs table. Refuses up front when there is no database.

    No null variant exists on purpose (see `backend/db/jobs.py`): accepting a
    submission with nowhere to record it would hand back a job id that can
    never resolve. Raising here means the 503 carries the reason.
    """
    if not jobs_available():
        raise errors.jobs_unavailable("no database is configured")
    return JobStore()
