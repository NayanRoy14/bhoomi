"""The retention sweep (PLAN.md 6).

Split deliberately: the prefix deletion and the sweep's ordering are testable
on a bare clone with a fake store, and only the SQL that decides *what* has
expired needs Postgres. The pinned-output rule is the part most worth having
covered by real SQL -- it is a `HAVING count(*) FILTER (...) = 0`, and getting
`all` versus `any` backwards deletes the demo rasters the README links to.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend import storage
from backend.queue import maintenance
from tests.conftest import needs_db

# --------------------------------------------------------------- local store


def test_delete_prefix_removes_every_variant(tmp_path):
    """A change job's three objects go together; another job's do not."""
    store = storage.LocalStorage(tmp_path)
    job, other = uuid.uuid4().hex, uuid.uuid4().hex
    for key in (storage.key_for(job), storage.key_for(job, "earlier"),
                storage.key_for(job, "later"), storage.key_for(other)):
        source = tmp_path / f"src-{key}"
        source.write_bytes(b"tif")
        store.put(source, key)

    assert store.delete_prefix(f"{job}.") == 3
    assert store.local_path(storage.key_for(job)) is None
    assert store.local_path(storage.key_for(job, "earlier")) is None
    # The neighbour is untouched: a uuid prefix must not match another key.
    assert store.local_path(storage.key_for(other)) is not None


def test_delete_prefix_refuses_to_empty_the_store(tmp_path):
    """An empty prefix globs to everything. It has to raise, not run.

    This is the guard that makes a lost job id a crash instead of a data loss,
    and it is the reason `_reject_traversal` rejects '' at all.
    """
    store = storage.LocalStorage(tmp_path)
    source = tmp_path / "src"
    source.write_bytes(b"tif")
    store.put(source, storage.key_for(uuid.uuid4().hex))

    with pytest.raises(ValueError):
        store.delete_prefix("")
    assert list(store.root.glob("*.tif"))


def test_delete_prefix_skips_the_scratch_directory(tmp_path):
    """`.scratch` lives in the root and is a directory, not an object."""
    store = storage.LocalStorage(tmp_path)
    scratch = store.scratch_dir()
    assert scratch is not None and scratch.exists()

    job = uuid.uuid4().hex
    source = tmp_path / "src"
    source.write_bytes(b"tif")
    store.put(source, storage.key_for(job))

    store.delete_prefix(f"{job}.")
    assert scratch.exists()


def test_delete_prefix_is_not_recursive(tmp_path):
    """Keys cannot contain a separator, so a prefix cannot reach a subtree."""
    store = storage.LocalStorage(tmp_path)
    nested = store.root / "nested"
    nested.mkdir(parents=True)
    (nested / "secret.tif").write_bytes(b"tif")

    assert store.delete_prefix("nested") == 0
    assert (nested / "secret.tif").exists()


# ------------------------------------------------------------- sweep ordering


class _FakeStore:
    """Enough JobStore for the sweep. Records the order it was called in."""

    def __init__(self, expired: list[str]) -> None:
        self._expired = expired
        self.calls: list[str] = []
        self.expired_arg: list[str] | None = None

    def reap_stalled(self, older_than_seconds: int) -> int:
        self.calls.append("reap")
        return 1

    def purge_client_ips(self, older_than_days: int) -> int:
        self.calls.append("purge")
        return 2

    def expired_jobs(self, limit: int) -> list[str]:
        self.calls.append("expired_jobs")
        return list(self._expired)

    def expire_outputs(self, job_ids: list[str]) -> int:
        self.calls.append("expire_outputs")
        self.expired_arg = list(job_ids)
        return len(job_ids)


class _FakeBackend:
    def __init__(self, fail_on: str | None = None) -> None:
        self.deleted: list[str] = []
        self.fail_on = fail_on

    def delete_prefix(self, prefix: str) -> int:
        if self.fail_on and prefix.startswith(self.fail_on):
            raise RuntimeError("bucket unreachable")
        self.deleted.append(prefix)
        return 2


@pytest.fixture
def fake_backend(monkeypatch):
    backend = _FakeBackend()
    monkeypatch.setattr(storage, "get_storage", lambda: backend)
    return backend


def test_sweep_deletes_objects_before_rows(fake_backend):
    """The recoverable failure is a row pointing at a gone object, so the
    object goes first. Reversing this strands objects nothing can find."""
    job = str(uuid.uuid4())
    store = _FakeStore([job])

    result = maintenance.sweep(store)

    assert store.calls.index("expire_outputs") > store.calls.index("expired_jobs")
    assert fake_backend.deleted == [f"{job}."]
    assert result.expired_jobs == 1
    assert result.objects_deleted == 2
    assert result.rows_deleted == 1


def test_sweep_keeps_rows_whose_objects_would_not_delete(monkeypatch):
    """A store that refuses must not have its rows deleted anyway.

    This is the case that would otherwise leak: row gone, object still billed,
    and no record left of the key to go and find it.
    """
    stuck, fine = str(uuid.uuid4()), str(uuid.uuid4())
    backend = _FakeBackend(fail_on=stuck)
    monkeypatch.setattr(storage, "get_storage", lambda: backend)
    store = _FakeStore([stuck, fine])

    result = maintenance.sweep(store)

    assert store.expired_arg == [fine]
    assert result.expired_jobs == 1


def test_sweep_reaps_and_purges_even_with_nothing_expired(fake_backend):
    """The three obligations are independent; an empty bucket sweep still
    has to fail stalled jobs and forget old IPs."""
    store = _FakeStore([])

    result = maintenance.sweep(store)

    assert result.jobs_reaped == 1
    assert result.ips_purged == 2
    assert result.expired_jobs == 0
    assert fake_backend.deleted == []


def test_sweep_survives_a_failing_reaper(fake_backend):
    """One broken obligation must not cancel the other two."""
    store = _FakeStore([])
    store.reap_stalled = lambda older_than_seconds: (_ for _ in ()).throw(
        RuntimeError("no connection"))

    result = maintenance.sweep(store)

    assert result.jobs_reaped == 0
    assert result.ips_purged == 2


def test_start_without_a_database_returns_none(monkeypatch):
    monkeypatch.setattr(maintenance, "get_engine", lambda: None)
    assert maintenance.start() is None


# ------------------------------------------------------------------ real SQL


def _record_output(store, job_id, *, expires_at, output_type="index_raster"):
    store.add_output(
        job_id, output_type=output_type, cog_uri=f"http://x/{job_id}.tif",
        bounds={"type": "Polygon",
                "coordinates": [[[88.3, 22.5], [88.4, 22.5],
                                 [88.4, 22.6], [88.3, 22.5]]]},
        crs="EPSG:32645", resolution_m=10.0, expires_at=expires_at)


@pytest.fixture
def job_store(clean_db):
    from backend.db.jobs import JobStore

    return JobStore(clean_db)


def _submit(store, **kwargs):
    return store.create(
        process="ndvi", aoi={"type": "Polygon",
                             "coordinates": [[[88.3, 22.5], [88.4, 22.5],
                                              [88.4, 22.6], [88.3, 22.5]]]},
        aoi_area_km2=100.0, scene_ids=["S2A_45QXF_20260304_0_L2A"],
        parameters={}, **kwargs)


@needs_db
def test_expired_jobs_finds_only_fully_expired_jobs(job_store):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)

    expired = _submit(job_store)
    _record_output(job_store, expired.id, expires_at=past)

    live = _submit(job_store)
    _record_output(job_store, live.id, expires_at=future)

    no_outputs = _submit(job_store)

    found = job_store.expired_jobs()
    assert str(expired.id) in found
    assert str(live.id) not in found
    assert str(no_outputs.id) not in found


@needs_db
def test_a_pinned_output_protects_its_whole_job(job_store):
    """`expires_at IS NULL` is the demo pin (PLAN.md 6). Because expiry deletes
    a whole prefix, one pinned output has to save its expired siblings too."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    job = _submit(job_store)
    _record_output(job_store, job.id, expires_at=past, output_type="change_raster")
    _record_output(job_store, job.id, expires_at=None, output_type="earlier_ndvi")

    assert str(job.id) not in job_store.expired_jobs()


@needs_db
def test_expire_outputs_keeps_the_job_row(job_store):
    """A job whose outputs were reclaimed still answers -- that is what makes
    /download's 410 `output_missing` truthful rather than a 404."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    job = _submit(job_store)
    _record_output(job_store, job.id, expires_at=past)

    assert job_store.expire_outputs([str(job.id)]) == 1
    assert job_store.outputs_for(job.id) == []
    assert job_store.get(job.id) is not None


@needs_db
def test_expire_outputs_with_no_ids_touches_nothing(job_store):
    """The empty list must not become `DELETE FROM outputs`."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    job = _submit(job_store)
    _record_output(job_store, job.id, expires_at=past)

    assert job_store.expire_outputs([]) == 0
    assert len(job_store.outputs_for(job.id)) == 1


@needs_db
def test_purge_client_ips_forgets_only_old_ones(job_store, clean_db):
    from sqlalchemy import text

    old = _submit(job_store, client_ip="203.0.113.7")
    recent = _submit(job_store, client_ip="203.0.113.8")
    with clean_db.begin() as conn:
        conn.execute(text("UPDATE jobs SET created_at = now() - interval '31 days' "
                          "WHERE id = :id"), {"id": str(old.id)})

    assert job_store.purge_client_ips(30) == 1

    with clean_db.connect() as conn:
        remaining = conn.execute(
            text("SELECT id FROM jobs WHERE client_ip IS NOT NULL")).scalars().all()
    assert [str(r) for r in remaining] == [str(recent.id)]
