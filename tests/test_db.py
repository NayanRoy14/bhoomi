"""Tests for the scene metadata cache (PLAN.md 6).

Two halves. The first needs no database and always runs: URL handling, the
null store, and the Scene -> row mapping, which is where a silent data loss
would live. The second talks to a real PostGIS and is skipped unless
`BHOOMI_TEST_DATABASE_URL` points at a throwaway one --

    docker run -d --rm -p 55432:5432 -e POSTGRES_PASSWORD=x \\
        -e POSTGRES_DB=bhoomi_test postgis/postgis:16-3.4
    BHOOMI_TEST_DATABASE_URL=postgresql://postgres:x@localhost:55432/bhoomi_test \\
        python -m pytest tests/test_db.py

There is no mock-Postgres half. The parts worth testing here are the parts a
mock would have to invent: whether ST_GeomFromGeoJSON accepts what we send it,
whether ON CONFLICT preserves the offset column, whether a JSONB round trip
returns the dict that went in. A fake that answered those would be asserting
its own behaviour.
"""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

from backend.db import engine as db_engine
from backend.db import scenes as db_scenes
from catalogue import Scene
from tests.test_catalogue import TILE_45QXF, item

TEST_DB_URL = os.getenv("BHOOMI_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB_URL,
                              reason="set BHOOMI_TEST_DATABASE_URL to run")

MULTIPOLYGON = {"type": "MultiPolygon", "coordinates": [TILE_45QXF["coordinates"]]}


def scene(scene_id="S2A_45QXF_20200310_0_L2A", **kw) -> Scene:
    return Scene.from_stac_item(item(scene_id, **kw), catalogue="earth-search")


class TestUrlHandling:
    def test_psycopg_driver_is_forced(self):
        """The bare form makes SQLAlchemy reach for psycopg2, which is absent."""
        assert db_engine.normalize_url("postgresql://u:p@h/db") == \
            "postgresql+psycopg://u:p@h/db"

    def test_postgres_scheme_alias_is_handled(self):
        """Hosting dashboards still emit `postgres://`."""
        assert db_engine.normalize_url("postgres://u:p@h/db") == \
            "postgresql+psycopg://u:p@h/db"

    def test_explicit_driver_is_left_alone(self):
        url = "postgresql+psycopg://u:p@h/db"
        assert db_engine.normalize_url(url) == url

    def test_sqlite_is_left_alone(self):
        assert db_engine.normalize_url("sqlite:///x.db") == "sqlite:///x.db"


class TestNoDatabaseIsSupported:
    """Unset means "run without a cache", not "misconfigured"."""

    def test_unset_url_reads_as_none(self, monkeypatch):
        monkeypatch.delenv(db_engine.ENV_VAR, raising=False)
        assert db_engine.database_url() is None

    def test_empty_url_reads_as_none(self, monkeypatch):
        monkeypatch.setenv(db_engine.ENV_VAR, "")
        assert db_engine.database_url() is None

    def test_no_engine_without_a_url(self, monkeypatch):
        monkeypatch.delenv(db_engine.ENV_VAR, raising=False)
        assert db_engine.get_engine() is None

    def test_default_store_is_the_null_one(self, monkeypatch):
        monkeypatch.delenv(db_engine.ENV_VAR, raising=False)
        assert isinstance(db_scenes.default_scene_store(), db_scenes.NullSceneStore)

    def test_null_store_writes_nothing_and_misses(self):
        store = db_scenes.NullSceneStore()
        assert store.put_many([scene()]) == 0
        assert store.get("S2A_45QXF_20200310_0_L2A") is None


class TestRowMapping:
    """Pure Scene -> bind-parameter conversion, no connection involved."""

    def test_every_column_is_populated(self):
        params = db_scenes._row_params(scene(), "earth-search")
        assert params["external_id"] == "S2A_45QXF_20200310_0_L2A"
        assert params["catalogue"] == "earth-search"
        assert params["collection"] == "sentinel-2-l2a"
        assert params["satellite"] == "sentinel-2a"
        assert params["cloud_cover"] == 0.0
        assert params["processing_baseline"] == "05.00"

    def test_geometry_is_serialised_as_geojson(self):
        params = db_scenes._row_params(scene(), "earth-search")
        assert json.loads(params["geometry"])["type"] == "Polygon"

    def test_assets_survive_as_json(self):
        params = db_scenes._row_params(scene(), "earth-search")
        assert json.loads(params["assets"])["nir"].endswith("nir.tif")

    def test_sensor_comes_from_the_instruments_list(self):
        s = dataclasses.replace(scene(),
                                properties={**scene().properties, "instruments": ["msi"]})
        assert db_scenes._row_params(s, "earth-search")["sensor"] == "msi"

    def test_sensor_is_null_when_absent(self):
        assert db_scenes._row_params(scene(), "earth-search")["sensor"] is None

    def test_non_polygon_footprint_is_skipped_not_fatal(self, caplog):
        """The column is GEOMETRY(Polygon); one bad scene must not fail a batch."""
        s = dataclasses.replace(scene(), geometry=MULTIPOLYGON)
        assert db_scenes._row_params(s, "earth-search") is None
        assert "not Polygon" in caplog.text

    def test_unserialisable_properties_do_not_raise(self):
        """STAC is JSON, but a datetime can reach properties via other paths."""
        from datetime import datetime
        s = dataclasses.replace(
            scene(), properties={**scene().properties, "odd": datetime(2020, 3, 10)})
        params = db_scenes._row_params(s, "earth-search")
        assert "2020-03-10" in json.loads(params["properties"])["odd"]


# --------------------------------------------------------------- integration

@pytest.fixture(scope="module")
def db():
    """A migrated database, torn down and rebuilt once for this module."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    url = db_engine.normalize_url(TEST_DB_URL)
    eng = create_engine(url, future=True)

    # Start from nothing so a half-migrated leftover cannot mask a broken
    # migration -- the migration is one of the things under test.
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS scenes"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    yield eng
    eng.dispose()


@pytest.fixture
def store(db):
    s = db_scenes.PostgresSceneStore(engine=db)
    s.clear()
    return s


@needs_db
class TestMigration:
    def test_table_and_indexes_exist(self, db):
        from sqlalchemy import text
        with db.connect() as conn:
            indexes = {r[0] for r in conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'scenes'"))}
        assert "scenes_geom_idx" in indexes
        assert "scenes_acquired_idx" in indexes

    def test_geometry_column_is_a_polygon_in_4326(self, db):
        from sqlalchemy import text
        with db.connect() as conn:
            row = conn.execute(text(
                """SELECT type, srid FROM geometry_columns
                   WHERE f_table_name = 'scenes'""")).first()
        assert row.type == "POLYGON"
        assert row.srid == 4326

    def test_downgrade_then_upgrade_is_clean(self, db):
        """A migration that cannot be undone is one that cannot be corrected."""
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import text

        cfg = Config("alembic.ini")
        # str(URL) renders the password as ***, which alembic would then try to
        # authenticate with.
        cfg.set_main_option("sqlalchemy.url", db.url.render_as_string(hide_password=False))
        command.downgrade(cfg, "base")
        with db.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('scenes')")).scalar() is None
        command.upgrade(cfg, "head")
        with db.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('scenes')")).scalar() is not None


@needs_db
class TestRoundTrip:
    def test_a_stored_scene_comes_back_equal(self, store):
        original = scene()
        assert store.put_many([original]) == 1
        loaded = store.get(original.id)
        assert loaded is not None
        assert loaded.id == original.id
        assert loaded.collection == original.collection
        assert loaded.assets == original.assets
        assert loaded.cloud_cover == original.cloud_cover
        assert loaded.processing_baseline == original.processing_baseline
        assert loaded.catalogue == "earth-search"

    def test_acquisition_time_keeps_its_timezone(self, store):
        original = scene()
        store.put_many([original])
        loaded = store.get(original.id)
        assert loaded.acquired_at == original.acquired_at
        assert loaded.acquired_at.tzinfo is not None

    def test_geometry_survives_postgis(self, store):
        original = scene()
        store.put_many([original])
        loaded = store.get(original.id)
        assert loaded.geometry["type"] == "Polygon"
        assert loaded.bbox == pytest.approx(original.bbox, abs=1e-6)

    def test_a_miss_is_none_not_an_error(self, store):
        assert store.get("S2A_NOT_CACHED_L2A") is None

    def test_scenes_are_namespaced_by_catalogue(self, store):
        """The same id in two catalogues is two scenes, per the UNIQUE key."""
        store.put_many([scene()], catalogue="earth-search")
        assert store.get(scene().id, catalogue="bhoonidhi") is None

    def test_batch_insert(self, store):
        batch = [scene(f"S2A_45QXF_2020031{i}_0_L2A") for i in range(5)]
        assert store.put_many(batch) == 5
        assert all(store.get(s.id) is not None for s in batch)

    def test_one_bad_footprint_does_not_lose_the_batch(self, store):
        good = [scene("S2A_GOOD_L2A")]
        bad = dataclasses.replace(scene("S2A_BAD_L2A"), geometry=MULTIPOLYGON)
        assert store.put_many(good + [bad]) == 1
        assert store.get("S2A_GOOD_L2A") is not None
        assert store.get("S2A_BAD_L2A") is None


@needs_db
class TestUpsert:
    def test_researching_a_scene_updates_rather_than_duplicating(self, store, db):
        from sqlalchemy import text
        store.put_many([scene()])
        store.put_many([scene(cloud=1.5)])
        with db.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM scenes")).scalar()
        assert count == 1
        assert store.get(scene().id).cloud_cover == pytest.approx(1.5)

    def test_a_repeat_search_does_not_erase_the_offset_measurement(self, store, db):
        """The value this whole table exists to protect: 6 s to derive."""
        offsets = db_scenes.PostgresOffsetCache(engine=db)
        store.put_many([scene()])
        offsets.set(scene().id, True)

        store.put_many([scene(cloud=9.9)])          # the user searches again

        assert offsets.get(scene().id) is True


@needs_db
class TestOffsetCache:
    def test_roundtrip(self, store, db):
        offsets = db_scenes.PostgresOffsetCache(engine=db)
        store.put_many([scene()])
        assert offsets.get(scene().id) is None      # stored, never measured
        offsets.set(scene().id, True)
        assert offsets.get(scene().id) is True

    def test_false_is_not_confused_with_missing(self, store, db):
        """The 2025 case: pixels say no offset. Distinct from 'not measured'."""
        offsets = db_scenes.PostgresOffsetCache(engine=db)
        store.put_many([scene()])
        offsets.set(scene().id, False)
        assert offsets.get(scene().id) is False

    def test_setting_an_uncached_scene_is_a_no_op_not_an_error(self, db, store):
        """It cannot insert -- a (id, bool) pair has no footprint. Costs 6 s later."""
        offsets = db_scenes.PostgresOffsetCache(engine=db)
        offsets.set("S2A_NEVER_SEARCHED_L2A", True)
        assert offsets.get("S2A_NEVER_SEARCHED_L2A") is None

    def test_it_satisfies_the_offset_cache_protocol(self, db):
        """`pipeline.set_offset_cache` accepts it because the shape matches."""
        import cache
        assert isinstance(db_scenes.PostgresOffsetCache(engine=db), cache.OffsetCache)


class TestConnectTimeout:
    """Degradation is only graceful if the failure arrives quickly.

    Without an explicit timeout an unreachable host took **130 s** to fail --
    long enough that "search still works, just uncached" would have been false
    in practice.
    """

    def test_default_is_applied(self, monkeypatch):
        monkeypatch.delenv(db_engine.TIMEOUT_VAR, raising=False)
        assert db_engine.connect_timeout() == db_engine.DEFAULT_CONNECT_TIMEOUT

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv(db_engine.TIMEOUT_VAR, "12")
        assert db_engine.connect_timeout() == 12

    def test_garbage_falls_back_rather_than_crashing_at_startup(self, monkeypatch, caplog):
        monkeypatch.setenv(db_engine.TIMEOUT_VAR, "soon")
        assert db_engine.connect_timeout() == db_engine.DEFAULT_CONNECT_TIMEOUT
        assert "not an integer" in caplog.text

    def test_the_engine_actually_carries_it(self, monkeypatch):
        """The value is only worth anything if it reaches psycopg.

        Asserted by behaviour rather than by introspection: SQLAlchemy merges
        `connect_args` at connect time, so they are absent from
        `dialect.create_connect_args` and there is no supported attribute to
        read them back from. Timing the failure tests the plumbing end to end,
        which is what the setting is for.
        """
        import time

        from sqlalchemy.exc import SQLAlchemyError

        monkeypatch.setenv(db_engine.ENV_VAR, "postgresql://u:p@127.0.0.1:1/db")
        monkeypatch.setenv(db_engine.TIMEOUT_VAR, "1")
        db_engine.reset_engines()
        try:
            engine = db_engine.get_engine()
            started = time.monotonic()
            with pytest.raises(SQLAlchemyError):
                engine.connect()
            # Generous against a 1 s setting, but far under the 130 s an
            # unbounded connect took.
            assert time.monotonic() - started < 20
        finally:
            db_engine.reset_engines()


class TestDegradation:
    """No database needed: the point is that an unreachable one is survivable."""

    DEAD = "postgresql+psycopg://nobody@127.0.0.1:1/nothing?connect_timeout=1"

    def _dead_store(self):
        from sqlalchemy import create_engine
        return db_scenes.PostgresSceneStore(engine=create_engine(self.DEAD, future=True))

    def test_a_write_to_a_dead_database_is_logged_not_raised(self, caplog):
        """A Postgres outage degrades Bhoomi to no-cache; it does not 500 search."""
        assert self._dead_store().put_many([scene()]) == 0
        assert "scene cache write failed" in caplog.text

    def test_a_read_from_a_dead_database_is_a_miss(self, caplog):
        assert self._dead_store().get("anything") is None
        assert "scene cache read failed" in caplog.text

    def test_it_fails_fast(self):
        """Guards the regression directly: 130 s was the unbounded behaviour."""
        import time
        started = time.monotonic()
        self._dead_store().get("anything")
        assert time.monotonic() - started < 30
