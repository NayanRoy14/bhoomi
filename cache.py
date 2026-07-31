"""Persistent cache for per-scene facts that are expensive to derive.

Right now that means one thing: the floor of a scene's valid DN distribution,
from which the BOA offset convention is decided. Measuring it costs ~11 s warm
and far more cold (a decimated overview read of the full tile) -- more than the
entire compute for a small job (PLAN.md 8).

It is a property of the *scene*, not of the request, so it should be paid once
per scene ever rather than once per worker process. `pipeline` kept it in a
module-level dict, which meant every restart re-paid it.

**What is cached is the measurement, not the verdict.** Storing the boolean was
the earlier design and it cost real work: when the detector was recalibrated,
every cached decision had to be discarded (migration 0003, `CACHE_FILENAME` v2)
because there was no way to tell which were wrong. The floor is a raw
observation of the pixels -- it does not change when the threshold does -- so a
future recalibration re-derives every decision for free instead of re-reading
tens of thousands of COGs.

The JSON backend is deliberately modest. A PostgresOffsetCache implements the
same three methods and the default changes; nothing else moves.
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

#: Bumped when what is *stored* changes, so entries of an older shape are not
#: reused. v1 and v2 stored a boolean verdict; v3 stores the measured floor in
#: DN. Old entries cannot be converted -- a verdict does not carry the number
#: that produced it -- so a new filename discards them.
#: The database equivalent is migration 0005.
CACHE_FILENAME = "scene_offsets.v3.json"


@runtime_checkable
class OffsetCache(Protocol):
    """Storage for the per-scene measured DN floor.

    Deliberately stores the observation rather than the decision derived from
    it; see the module docstring.
    """

    def get(self, scene_id: str) -> float | None:
        """The cached floor in DN, or None if this scene has not been measured."""

    def set(self, scene_id: str, floor_dn: float) -> None:
        """Record the measured floor for a scene."""

    def clear(self) -> None:
        """Forget everything. Used by tests and by cache invalidation."""


class MemoryOffsetCache(OffsetCache):
    """In-process only. The previous behaviour, kept for tests."""

    def __init__(self) -> None:
        self._data: dict[str, float] = {}
        self._lock = threading.Lock()

    def get(self, scene_id: str) -> float | None:
        with self._lock:
            return self._data.get(scene_id)

    def set(self, scene_id: str, floor_dn: float) -> None:
        with self._lock:
            self._data[scene_id] = float(floor_dn)

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
    the cost of a miss is one overview read, not a failure.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_CACHE_DIR / CACHE_FILENAME
        self._lock = threading.Lock()
        self._data: dict[str, float] | None = None

    def _load(self) -> dict[str, float]:
        if self._data is not None:
            return self._data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            # `bool` is a subclass of `int`, so entries left by a v1/v2 file
            # would survive a naive numeric check and be read as 0.0 or 1.0 DN
            # -- a floor low enough to "prove" every scene offset-absent.
            self._data = {
                k: float(v) for k, v in raw.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }
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

    def get(self, scene_id: str) -> float | None:
        with self._lock:
            return self._load().get(scene_id)

    def set(self, scene_id: str, floor_dn: float) -> None:
        with self._lock:
            data = self._load()
            if data.get(scene_id) == float(floor_dn):
                return
            data[scene_id] = float(floor_dn)
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
