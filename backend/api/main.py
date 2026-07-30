"""Bhoomi API application.

    uvicorn backend.api.main:app --reload

This layer adds HTTP and nothing else. Search logic lives in catalogue/,
mathematics in processing/, and their composition in pipeline.py.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.errors import catalogue_error_handler
from backend.api.ratelimit import rate_limit_middleware
from backend.api.routes import health, jobs, scenes
from catalogue import CatalogueError
from processing import __version__

logging.basicConfig(
    level=os.getenv("BHOOMI_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

DESCRIPTION = """
On-demand Earth Observation processing.

Bhoomi does not display pre-made map layers. It performs geospatial computation
on demand and returns Cloud-Optimized GeoTIFFs that any GIS tool can consume.

**Currently implemented:** catalogue search, and the asynchronous job queue.

The only registered process is `fake`, which sleeps for ten seconds and
produces no raster. It exists to exercise the queue on its own; the real
indices land next. Submitting `ndvi` today returns 400, on purpose — a job
that reported `completed` with nothing behind it would be worse.
"""

app = FastAPI(
    title="Bhoomi API",
    version=__version__,
    description=DESCRIPTION,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# The frontend is served from a different origin in development, and may stay
# separate in production (Vercel) while the API needs a fixed IP for Bhoonidhi.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv(
        "BHOOMI_CORS_ORIGINS", "http://localhost:3000").split(",") if o],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.middleware("http")(rate_limit_middleware)

app.add_exception_handler(CatalogueError, catalogue_error_handler)

app.include_router(health.router)
app.include_router(scenes.router)
app.include_router(jobs.router)
