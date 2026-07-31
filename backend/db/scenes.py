"""The ``scenes`` table (PLAN.md 6): a write-through cache of catalogue metadata.

## What this cache does, and the thing it deliberately does not

It stores scenes **by identity**, so a later request can resolve a scene id to
its footprint and asset hrefs without going back to the catalogue. That is what
January needs: `POST /api/v1/jobs` is given `scene_ids` (PLAN.md 7.3), and the
worker has to turn those into band URLs. Re-searching STAC to answer "what is
scene X" would be a second network round trip for a fact already fetched.

It does **not** answer scene *searches* from cache, and the search route does not
ask it to. Knowing that the table holds some scenes intersecting an AOI is not
the same as knowing it holds *all* of them -- that needs a ledger of which
(bbox, date-range, cloud) windows have actually been fetched, and a policy for
when each expires. Without one, a cache hit silently returns a subset, and a
user comparing two dates would be choosing from scenes that happen to have been
cached rather than scenes that exist. A ~1.1 s STAC query (PLAN.md D9) is not
worth that failure mode. If search-from-cache is ever wanted, it needs that
ledger first, not a `SELECT ... WHERE ST_Intersects`.

## Why writes here are allowed to fail quietly

A failed cache write costs latency on some later request. It cannot change an
answer, because every value served still comes from the catalogue on the same
request that wrote it. So `put_many` logs and returns rather than raising: a
Postgres outage should degrade Bhoomi to its no-database configuration, which
is a supported one, not take scene search down with it.

This is the opposite call from the one in `processing/indices.py`, where an
out-of-range excursion now raises instead of logging -- and the difference is
the point. There, the mutable warning was the only signal that a *served
number* was wrong. Here, nothing served is affected.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable, Protocol, runtime_checkable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.db.engine import get_engine
from catalogue import Scene

logger = logging.getLogger(__name__)

DEFAULT_CATALOGUE = "earth-search"

# `boa_floor_dn` appears in neither the insert nor the update list. It is
# measured from pixels at ~11 s a time (PLAN.md 5.3) and is *not* catalogue
# metadata, so a re-search must never overwrite it with NULL. A reprocessed
# scene is safe here: Earth Search gives it a new id (..._0_L2A -> ..._1_L2A),
# so it lands as a new row rather than inheriting a stale measurement.
_UPSERT = text("""
    INSERT INTO scenes (
        external_id, catalogue, collection, satellite, sensor, acquired_at,
        cloud_cover, processing_baseline, geometry, assets, properties, cached_at
    ) VALUES (
        :external_id, :catalogue, :collection, :satellite, :sensor, :acquired_at,
        :cloud_cover, :processing_baseline,
        ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326),
        CAST(:assets AS JSONB), CAST(:properties AS JSONB), now()
    )
    ON CONFLICT (catalogue, external_id) DO UPDATE SET
        collection          = EXCLUDED.collection,
        satellite           = EXCLUDED.satellite,
        sensor              = EXCLUDED.sensor,
        acquired_at         = EXCLUDED.acquired_at,
        cloud_cover         = EXCLUDED.cloud_cover,
        processing_baseline = EXCLUDED.processing_baseline,
        geometry            = EXCLUDED.geometry,
        assets              = EXCLUDED.assets,
        properties          = EXCLUDED.properties,
        cached_at           = now()
""")

_SELECT_ONE = text("""
    SELECT external_id, catalogue, collection, acquired_at,
           ST_AsGeoJSON(geometry) AS geometry,
           ST_XMin(geometry) AS xmin, ST_YMin(geometry) AS ymin,
           ST_XMax(geometry) AS xmax, ST_YMax(geometry) AS ymax,
           assets, properties
    FROM scenes
    WHERE catalogue = :catalogue AND external_id = :external_id
""")


def _sensor(scene: Scene) -> str | None:
    """STAC reports instruments as a list; the column holds one name."""
    instruments = scene.properties.get("instruments")
    if isinstance(instruments, (list, tuple)) and instruments:
        return str(instruments[0])
    return None


def _row_params(scene: Scene, catalogue: str) -> dict | None:
    """Bind parameters for one scene, or None if it cannot be stored.

    The geometry column is `GEOMETRY(Polygon, 4326)` per PLAN.md 6, so a scene
    with a MultiPolygon footprint -- rare for Sentinel-2, but possible where a
    tile is split by nodata -- would abort the whole insert. Skipping it costs
    one cache entry; letting it through would cost the batch.
    """
    geometry = scene.geometry or {}
    if geometry.get("type") != "Polygon":
        logger.warning("not caching scene %s: footprint is %s, not Polygon",
                       scene.id, geometry.get("type") or "missing")
        return None
    return {
        "external_id": scene.id,
        "catalogue": catalogue,
        "collection": scene.collection,
        "satellite": scene.satellite,
        "sensor": _sensor(scene),
        "acquired_at": scene.acquired_at,
        "cloud_cover": scene.cloud_cover,
        "processing_baseline": scene.processing_baseline,
        "geometry": json.dumps(geometry),
        "assets": json.dumps(scene.assets),
        "properties": json.dumps(scene.properties, default=str),
    }


def _scene_from_row(row) -> Scene:
    mapping = row._mapping
    return Scene(
        id=mapping["external_id"],
        collection=mapping["collection"],
        acquired_at=mapping["acquired_at"],
        bbox=(mapping["xmin"], mapping["ymin"], mapping["xmax"], mapping["ymax"]),
        geometry=json.loads(mapping["geometry"]),
        assets=dict(mapping["assets"] or {}),
        properties=dict(mapping["properties"] or {}),
        catalogue=mapping["catalogue"],
    )


@runtime_checkable
class SceneStore(Protocol):
    """Persistence for catalogue metadata. See the module docstring for scope."""

    def put_many(self, scenes: Iterable[Scene], catalogue: str = DEFAULT_CATALOGUE) -> int:
        """Cache these scenes. Returns how many rows were written."""

    def get(self, scene_id: str, catalogue: str = DEFAULT_CATALOGUE) -> Scene | None:
        """One scene by identifier, or None if it is not cached."""

    def clear(self) -> None:
        """Forget every cached scene."""


class NullSceneStore(SceneStore):
    """The no-database configuration: every write is dropped, every read misses.

    Not a degraded mode to be warned about -- it is what `BHOOMI_DATABASE_URL`
    unset means, and what the test suite runs against.
    """

    def put_many(self, scenes: Iterable[Scene], catalogue: str = DEFAULT_CATALOGUE) -> int:
        return 0

    def get(self, scene_id: str, catalogue: str = DEFAULT_CATALOGUE) -> Scene | None:
        return None

    def clear(self) -> None:
        return None


class PostgresSceneStore(SceneStore):
    """The `scenes` table, reached through the shared engine."""

    def __init__(self, engine=None) -> None:
        self._engine = engine

    @property
    def engine(self):
        return self._engine if self._engine is not None else get_engine()

    def put_many(self, scenes: Iterable[Scene], catalogue: str = DEFAULT_CATALOGUE) -> int:
        params = [p for p in (_row_params(s, catalogue) for s in scenes) if p is not None]
        if not params:
            return 0
        engine = self.engine
        if engine is None:
            return 0
        try:
            with engine.begin() as conn:
                conn.execute(_UPSERT, params)
        except SQLAlchemyError as exc:
            # Deliberately not re-raised; see the module docstring.
            logger.warning("scene cache write failed (%d scenes): %s", len(params), exc)
            return 0
        return len(params)

    def get(self, scene_id: str, catalogue: str = DEFAULT_CATALOGUE) -> Scene | None:
        engine = self.engine
        if engine is None:
            return None
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    _SELECT_ONE,
                    {"external_id": scene_id, "catalogue": catalogue},
                ).first()
        except SQLAlchemyError as exc:
            logger.warning("scene cache read failed for %s: %s", scene_id, exc)
            return None
        return _scene_from_row(row) if row is not None else None

    def clear(self) -> None:
        engine = self.engine
        if engine is None:
            return
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE TABLE scenes"))


class PostgresOffsetCache:
    """`cache.OffsetCache` backed by `scenes.boa_floor_dn`.

    Anticipated in `cache.py`: the same three methods, a different default, and
    nothing else moves. The one real constraint is that the table is keyed on
    (catalogue, external_id) while the OffsetCache protocol passes only a scene
    id -- so the catalogue is fixed per instance rather than per call.

    Stores the measured DN floor, not the offset verdict derived from it. The
    verdict column it replaced had to be nulled wholesale when the detector was
    recalibrated (migration 0003); the measurement never needs that.

    `set` updates an existing row and does nothing if there is none. It cannot
    insert: the row needs a footprint, assets and an acquisition time that a
    bare (scene_id, float) pair does not carry. In practice the row is already
    there, because a scene reaches processing by way of a search that cached it.
    When it is not, the cost is re-measuring later -- never a wrong value.
    """

    def __init__(self, catalogue: str = DEFAULT_CATALOGUE, engine=None) -> None:
        self.catalogue = catalogue
        self._engine = engine

    @property
    def engine(self):
        return self._engine if self._engine is not None else get_engine()

    def get(self, scene_id: str) -> float | None:
        engine = self.engine
        if engine is None:
            return None
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("""SELECT boa_floor_dn FROM scenes
                            WHERE catalogue = :catalogue AND external_id = :external_id"""),
                    {"catalogue": self.catalogue, "external_id": scene_id},
                ).first()
        except SQLAlchemyError as exc:
            logger.warning("offset cache read failed for %s: %s", scene_id, exc)
            return None
        return None if row is None or row[0] is None else float(row[0])

    def set(self, scene_id: str, floor_dn: float) -> None:
        engine = self.engine
        if engine is None:
            return
        try:
            with engine.begin() as conn:
                result = conn.execute(
                    text("""UPDATE scenes SET boa_floor_dn = :value
                            WHERE catalogue = :catalogue AND external_id = :external_id"""),
                    {"value": float(floor_dn), "catalogue": self.catalogue,
                     "external_id": scene_id},
                )
        except SQLAlchemyError as exc:
            logger.warning("offset cache write failed for %s: %s", scene_id, exc)
            return
        if result.rowcount == 0:
            logger.debug("scene %s not cached; offset measurement not persisted", scene_id)

    def clear(self) -> None:
        engine = self.engine
        if engine is None:
            return
        with engine.begin() as conn:
            conn.execute(text("UPDATE scenes SET boa_floor_dn = NULL"))


def default_scene_store() -> SceneStore:
    """The store the API uses: Postgres when configured, otherwise the null one."""
    return PostgresSceneStore() if get_engine() is not None else NullSceneStore()
