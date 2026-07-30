"""Persistent cache for per-scene facts that are expensive to derive.

Right now that means one thing: whether the BOA offset is present in a scene's
pixels. Detecting it costs ~6.0 s (a decimated overview read of the full tile) --
over a third of a maximum-size NDVI job, and more than the entire compute for a
small one (PLAN.md 8).

It is a property of the *scene*, not of the request, so it should be paid once
per scene ever rather than once per worker process. `pipeline` kept it in a
module-level dict, which meant every restart re-paid it.

The JSON backend is deliberately modest. When the `scenes` table lands (PLAN.md
6) a PostgresOffsetCache implements the same three methods and the default
changes; nothing else moves.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(os.getenv("BHOOMI_CACHE_DIR", ".cache"))


@runtime_checkable
class OffsetCache(Protocol):
    """Storage for the per-scene BOA-offset decision."""

    def get(self, scene_id: str) -> bool | None:
        """The cached decision, or None if this scene has not been measured."""

    def set(self, scene_id: str, offset_present: bool) -> None:
        """Record the decision for a scene."""

    def clear(self) -> None:
        """Forget everything. Used by tests and by cache invalidation."""


class MemoryOffsetCache(OffsetCache):
    """In-process only. The previous behaviour, kept for tests."""

    def __init__(self) -> None:
        self._data: dict[str, bool] = {}
        self._lock = threading.Lock()

    def get(self, scene_id: str) -> bool | None:
        with self._lock:
            return self._data.get(scene_id)

    def set(self, scene_id: str, offset_present: bool) -> None:
        with self._lock:
            self._data[scene_id] = offset_present

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class JsonFileOffsetCache(OffsetCache):
    """A JSON file on disk, written atomically.

    Atomic because a worker killed mid-write would otherwise leave truncated
    JSON, and a corrupt cache that raises on every read is worse than no cache.
    A damaged file is discarded with a warning rather than being fatal --
    the cost of a miss is 6 s, not a failure.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_CACHE_DIR / "scene_offsets.json"
        self._lock = threading.Lock()
        self._data: dict[str, bool] | None = None

    def _load(self) -> dict[str, bool]:
        if self._data is not None:
            return self._data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = {k: bool(v) for k, v in raw.items() if isinstance(v, bool)}
        except FileNotFoundError:
            self._data = {}
        except (json.JSONDecodeError, OSError, AttributeError) as exc:
            logger.warning("Discarding unreadable offset cache at %s: %s", self.path, exc)
            self._data = {}
        return self._data

    def _flush(self) -> None:
        assert self._data is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file, then replace -- os.replace is atomic on
        # both POSIX and Windows when source and destination share a directory.
        handle, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get(self, scene_id: str) -> bool | None:
        with self._lock:
            return self._load().get(scene_id)

    def set(self, scene_id: str, offset_present: bool) -> None:
        with self._lock:
            data = self._load()
            if data.get(scene_id) is offset_present:
                return
            data[scene_id] = offset_present
            self._flush()

    def clear(self) -> None:
        with self._lock:
            self._data = {}
            self.path.unlink(missing_ok=True)

    def __len__(self) -> int:
        with self._lock:
            return len(self._load())


def default_cache() -> OffsetCache:
    """The cache the pipeline uses unless one is injected.

    `BHOOMI_CACHE_DIR=` (empty) selects the in-memory cache, which is what the
    test suite wants -- no stray files, no cross-test contamination.
    """
    if os.getenv("BHOOMI_CACHE_DIR") == "":
        return MemoryOffsetCache()
    return JsonFileOffsetCache()
