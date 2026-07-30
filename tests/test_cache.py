"""Tests for the persistent offset cache.

The value cached here costs ~6 s to derive and is wrong in only one direction:
a stale or corrupt entry produces silently mis-harmonized reflectance. So the
tests care as much about failure behaviour as about hits and misses.
"""

import json
import threading

import pytest

import cache
import pipeline


@pytest.fixture
def json_cache(tmp_path):
    return cache.JsonFileOffsetCache(tmp_path / "offsets.json")


class TestMemoryCache:
    def test_roundtrip(self):
        c = cache.MemoryOffsetCache()
        assert c.get("S2A") is None
        c.set("S2A", True)
        assert c.get("S2A") is True

    def test_false_is_not_confused_with_missing(self):
        """False and None mean different things: 'no offset' vs 'not measured'."""
        c = cache.MemoryOffsetCache()
        c.set("S2A", False)
        assert c.get("S2A") is False
        assert c.get("S2B") is None

    def test_clear(self):
        c = cache.MemoryOffsetCache()
        c.set("S2A", True)
        c.clear()
        assert c.get("S2A") is None


class TestJsonFileCache:
    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "offsets.json"
        cache.JsonFileOffsetCache(path).set("S2A_45QXF_20220213_0_L2A", True)
        # A fresh instance is what a restarted worker sees.
        assert cache.JsonFileOffsetCache(path).get("S2A_45QXF_20220213_0_L2A") is True

    def test_false_survives_a_restart(self, tmp_path):
        path = tmp_path / "offsets.json"
        cache.JsonFileOffsetCache(path).set("S2C_45QXF_20250304_0_L2A", False)
        assert cache.JsonFileOffsetCache(path).get("S2C_45QXF_20250304_0_L2A") is False

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert cache.JsonFileOffsetCache(tmp_path / "nope.json").get("x") is None

    def test_corrupt_file_is_discarded_not_fatal(self, tmp_path, caplog):
        """A cache miss costs 6 s. Raising on every read costs the whole job."""
        path = tmp_path / "offsets.json"
        path.write_text("{ this is not json", encoding="utf-8")
        c = cache.JsonFileOffsetCache(path)
        assert c.get("anything") is None
        assert "Discarding unreadable offset cache" in caplog.text

    def test_non_boolean_values_are_ignored(self, tmp_path):
        """Only True/False are meaningful; anything else is corruption."""
        path = tmp_path / "offsets.json"
        path.write_text(json.dumps({"good": True, "bad": "yes", "worse": 1}),
                        encoding="utf-8")
        c = cache.JsonFileOffsetCache(path)
        assert c.get("good") is True
        assert c.get("bad") is None
        assert c.get("worse") is None

    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "offsets.json"
        cache.JsonFileOffsetCache(path).set("S2A", True)
        assert path.exists()

    def test_no_temp_files_left_behind(self, tmp_path):
        path = tmp_path / "offsets.json"
        c = cache.JsonFileOffsetCache(path)
        for i in range(5):
            c.set(f"S2A_{i}", i % 2 == 0)
        assert list(tmp_path.glob("*.tmp")) == []

    def test_rewriting_the_same_value_does_not_touch_disk(self, tmp_path):
        path = tmp_path / "offsets.json"
        c = cache.JsonFileOffsetCache(path)
        c.set("S2A", True)
        before = path.stat().st_mtime_ns
        c.set("S2A", True)
        assert path.stat().st_mtime_ns == before

    def test_concurrent_writes_do_not_corrupt(self, tmp_path):
        path = tmp_path / "offsets.json"
        c = cache.JsonFileOffsetCache(path)

        def write(start):
            for i in range(start, start + 20):
                c.set(f"scene_{i}", i % 2 == 0)

        threads = [threading.Thread(target=write, args=(n * 20,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(cache.JsonFileOffsetCache(path)) == 80
        json.loads(path.read_text(encoding="utf-8"))  # still valid JSON


class TestPipelineIntegration:
    def test_detection_runs_once_then_is_cached(self, monkeypatch):
        calls = []

        def fake_detect(url, decimation=32):
            calls.append(url)
            return True

        monkeypatch.setattr(pipeline.harmonize, "detect_offset_in_scene", fake_detect)
        pipeline.set_offset_cache(cache.MemoryOffsetCache())

        from catalogue import Scene
        from tests.test_catalogue import item

        scene = Scene.from_stac_item(item("S2A_TEST"))
        assert pipeline.offset_present(scene) is True
        assert pipeline.offset_present(scene) is True
        assert len(calls) == 1, "detection should be paid once per scene"

    def test_a_persisted_false_is_honoured(self, monkeypatch, tmp_path):
        """The 2025 case: pixels say no offset. That must survive a restart."""
        def explode(url, decimation=32):
            raise AssertionError("should not re-detect a cached scene")

        path = tmp_path / "offsets.json"
        cache.JsonFileOffsetCache(path).set("S2C_45QXF_20250304_0_L2A", False)

        monkeypatch.setattr(pipeline.harmonize, "detect_offset_in_scene", explode)
        pipeline.set_offset_cache(cache.JsonFileOffsetCache(path))

        from catalogue import Scene
        from tests.test_catalogue import item

        scene = Scene.from_stac_item(item("S2C_45QXF_20250304_0_L2A"))
        assert pipeline.offset_present(scene) is False


class TestDefaultSelection:
    def test_empty_cache_dir_selects_memory(self, monkeypatch):
        monkeypatch.setenv("BHOOMI_CACHE_DIR", "")
        assert isinstance(cache.default_cache(), cache.MemoryOffsetCache)

    def test_otherwise_selects_the_file_backend(self, monkeypatch):
        monkeypatch.delenv("BHOOMI_CACHE_DIR", raising=False)
        assert isinstance(cache.default_cache(), cache.JsonFileOffsetCache)
