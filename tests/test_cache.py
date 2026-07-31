"""Tests for the persistent offset cache.

The value cached here costs ~11 s to derive and is wrong in only one direction:
a stale or corrupt entry produces silently mis-harmonized reflectance. So the
tests care as much about failure behaviour as about hits and misses.

What is stored is the measured DN floor, not the offset verdict -- so that a
recalibration re-derives decisions instead of discarding them, as migration
0003 and CACHE_FILENAME v2 had to.
"""

import json
import threading

import pytest

import cache
import pipeline


@pytest.fixture
def json_cache(tmp_path):
    return cache.JsonFileOffsetCache(tmp_path / "offsets.json")


#: A floor that proves the offset absent, and one that decides nothing.
CONCLUSIVE_FLOOR = 250.0
INCONCLUSIVE_FLOOR = 1450.0


class TestMemoryCache:
    def test_roundtrip(self):
        c = cache.MemoryOffsetCache()
        assert c.get("S2A") is None
        c.set("S2A", CONCLUSIVE_FLOOR)
        assert c.get("S2A") == CONCLUSIVE_FLOOR

    def test_a_low_floor_is_not_confused_with_missing(self):
        """A floor of 0.0 and None mean different things, and 0.0 is falsy."""
        c = cache.MemoryOffsetCache()
        c.set("S2A", 0.0)
        assert c.get("S2A") == 0.0
        assert c.get("S2B") is None

    def test_clear(self):
        c = cache.MemoryOffsetCache()
        c.set("S2A", CONCLUSIVE_FLOOR)
        c.clear()
        assert c.get("S2A") is None


class TestJsonFileCache:
    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "offsets.json"
        cache.JsonFileOffsetCache(path).set("S2A_45QXF_20220213_0_L2A", 1003.0)
        # A fresh instance is what a restarted worker sees.
        assert cache.JsonFileOffsetCache(path).get("S2A_45QXF_20220213_0_L2A") == 1003.0

    def test_a_conclusive_floor_survives_a_restart(self, tmp_path):
        path = tmp_path / "offsets.json"
        cache.JsonFileOffsetCache(path).set("S2C_45QXF_20250304_0_L2A", CONCLUSIVE_FLOOR)
        assert (cache.JsonFileOffsetCache(path).get("S2C_45QXF_20250304_0_L2A")
                == CONCLUSIVE_FLOOR)

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert cache.JsonFileOffsetCache(tmp_path / "nope.json").get("x") is None

    def test_corrupt_file_is_discarded_not_fatal(self, tmp_path, caplog):
        """A cache miss costs one overview read. Raising costs the whole job."""
        path = tmp_path / "offsets.json"
        path.write_text("{ this is not json", encoding="utf-8")
        c = cache.JsonFileOffsetCache(path)
        assert c.get("anything") is None
        assert "Discarding unreadable offset cache" in caplog.text

    def test_non_numeric_values_are_ignored(self, tmp_path):
        path = tmp_path / "offsets.json"
        path.write_text(json.dumps({"good": 698.0, "int": 250, "bad": "yes",
                                    "worse": None}), encoding="utf-8")
        c = cache.JsonFileOffsetCache(path)
        assert c.get("good") == 698.0
        assert c.get("int") == 250.0
        assert c.get("bad") is None
        assert c.get("worse") is None

    def test_booleans_left_by_an_older_cache_are_rejected(self, tmp_path):
        """v1 and v2 stored verdicts. `bool` is a subclass of `int`.

        Read numerically, `False` becomes a floor of 0.0 DN -- conclusive, and
        conclusive for every scene, which would silently assert 'offset absent'
        across the board. The filename is versioned so this should never be
        reached; the guard is here because the failure is invisible.
        """
        path = tmp_path / "offsets.json"
        path.write_text(json.dumps({"old_true": True, "old_false": False}),
                        encoding="utf-8")
        c = cache.JsonFileOffsetCache(path)
        assert c.get("old_true") is None
        assert c.get("old_false") is None

    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "offsets.json"
        cache.JsonFileOffsetCache(path).set("S2A", CONCLUSIVE_FLOOR)
        assert path.exists()

    def test_no_temp_files_left_behind(self, tmp_path):
        path = tmp_path / "offsets.json"
        c = cache.JsonFileOffsetCache(path)
        for i in range(5):
            c.set(f"S2A_{i}", 100.0 * i)
        assert list(tmp_path.glob("*.tmp")) == []

    def test_rewriting_the_same_value_does_not_touch_disk(self, tmp_path):
        path = tmp_path / "offsets.json"
        c = cache.JsonFileOffsetCache(path)
        c.set("S2A", CONCLUSIVE_FLOOR)
        before = path.stat().st_mtime_ns
        c.set("S2A", CONCLUSIVE_FLOOR)
        assert path.stat().st_mtime_ns == before

    def test_concurrent_writes_do_not_corrupt(self, tmp_path):
        path = tmp_path / "offsets.json"
        c = cache.JsonFileOffsetCache(path)

        def write(start):
            for i in range(start, start + 20):
                c.set(f"scene_{i}", float(i))

        threads = [threading.Thread(target=write, args=(n * 20,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(cache.JsonFileOffsetCache(path)) == 80
        json.loads(path.read_text(encoding="utf-8"))  # still valid JSON


class TestPipelineIntegration:
    def _scene(self, scene_id):
        from catalogue import Scene
        from tests.test_catalogue import item

        return Scene.from_stac_item(item(scene_id))

    def test_measurement_runs_once_then_is_cached(self, monkeypatch):
        calls = []

        def fake_measure(url, decimation=4):
            calls.append(url)
            return pipeline.harmonize.OffsetEvidence(floor_dn=CONCLUSIVE_FLOOR,
                                                     sample_pixels=10**6)

        monkeypatch.setattr(pipeline.harmonize, "measure_offset_floor_in_scene",
                            fake_measure)
        pipeline.set_offset_cache(cache.MemoryOffsetCache())

        scene = self._scene("S2A_TEST")
        assert pipeline.offset_present(scene) is False
        assert pipeline.offset_present(scene) is False
        assert len(calls) == 1, "measurement should be paid once per scene"

    def test_a_persisted_floor_is_honoured(self, monkeypatch, tmp_path):
        """The 2025 case: pixels say no offset. That must survive a restart."""
        def explode(url, decimation=4):
            raise AssertionError("should not re-measure a cached scene")

        path = tmp_path / "offsets.json"
        cache.JsonFileOffsetCache(path).set("S2C_45QXF_20250304_0_L2A",
                                            CONCLUSIVE_FLOOR)

        monkeypatch.setattr(pipeline.harmonize, "measure_offset_floor_in_scene", explode)
        pipeline.set_offset_cache(cache.JsonFileOffsetCache(path))

        assert pipeline.offset_present(self._scene("S2C_45QXF_20250304_0_L2A")) is False

    def test_a_recalibration_re_derives_without_re_measuring(self, monkeypatch, tmp_path):
        """The reason the measurement is cached rather than the verdict.

        Move the threshold below a cached floor and the decision changes on the
        next call, with no network read. Under the old design this needed
        migration 0003 and a new cache filename.
        """
        def explode(url, decimation=4):
            raise AssertionError("a threshold change must not re-measure")

        path = tmp_path / "offsets.json"
        cache.JsonFileOffsetCache(path).set("S2A_TEST", 700.0)
        monkeypatch.setattr(pipeline.harmonize, "measure_offset_floor_in_scene", explode)
        pipeline.set_offset_cache(cache.JsonFileOffsetCache(path))

        scene = self._scene("S2A_TEST")
        assert pipeline.offset_decision(scene).basis == "pixels"

        # 700 DN is no longer conclusive; the scene falls through to metadata,
        # which this fixture does not carry, so the pipeline refuses to guess.
        monkeypatch.setattr(pipeline.harmonize, "FLOOR_DN", 600.0)
        with pytest.raises(pipeline.harmonize.HarmonizationError):
            pipeline.offset_decision(scene)


class TestDefaultSelection:
    def test_empty_cache_dir_selects_memory(self, monkeypatch):
        monkeypatch.setenv("BHOOMI_CACHE_DIR", "")
        assert isinstance(cache.default_cache(), cache.MemoryOffsetCache)

    def test_otherwise_selects_the_file_backend(self, monkeypatch):
        monkeypatch.delenv("BHOOMI_CACHE_DIR", raising=False)
        assert isinstance(cache.default_cache(), cache.JsonFileOffsetCache)
