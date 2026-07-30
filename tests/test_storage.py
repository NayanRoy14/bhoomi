"""Tests for output storage (PLAN.md D5, O4).

The interesting behaviour is not "a file was copied" -- it is the seam. D5
requires `cog_uri` to be a URL, O4 has not chosen a provider, and the local
backend has to satisfy the first without pretending to be the second.
"""

from __future__ import annotations

import pytest

from backend import storage


@pytest.fixture
def local(tmp_path):
    return storage.LocalStorage(tmp_path / "jobs")


def a_file(tmp_path, name="src.tif", size=32):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


class TestLocalStorage:
    def test_put_returns_the_size(self, local, tmp_path):
        assert local.put(a_file(tmp_path, size=100), "job.tif") == 100

    def test_the_object_is_readable_afterwards(self, local, tmp_path):
        local.put(a_file(tmp_path, size=8), "job.tif")
        assert local.local_path("job.tif").read_bytes() == b"x" * 8

    def test_put_moves_rather_than_copies(self, local, tmp_path):
        """A 20 MB copy per job would be pure waste; the source is scratch."""
        source = a_file(tmp_path)
        local.put(source, "job.tif")
        assert not source.exists()

    def test_it_creates_its_directory(self, local, tmp_path):
        local.put(a_file(tmp_path), "job.tif")
        assert local.root.is_dir()

    def test_a_missing_object_reads_as_none(self, local):
        assert local.local_path("never-written.tif") is None

    def test_delete_is_idempotent(self, local, tmp_path):
        local.put(a_file(tmp_path), "job.tif")
        local.delete("job.tif")
        local.delete("job.tif")
        assert local.local_path("job.tif") is None

    def test_it_has_no_public_url_of_its_own(self, local):
        """Which is what makes the API serve it -- see the module docstring."""
        assert local.url_for("job.tif") is None

    def test_scratch_is_on_the_same_filesystem_as_the_root(self, local):
        """So put() is a rename. /tmp and a mounted volume are not the same
        device, and a cross-device move copies the whole COG."""
        scratch = local.scratch_dir()
        assert scratch.is_dir()
        assert local.root in scratch.parents or scratch.parent == local.root

    def test_scratch_is_not_servable_as_a_key(self, local):
        """A half-written COG must never appear under a key local_path serves."""
        local.scratch_dir()
        with pytest.raises(ValueError):
            local.local_path(".scratch")

    def test_an_oversized_output_is_refused(self, local, tmp_path, monkeypatch):
        """PLAN.md 8 caps outputs at 200 MB; refuse rather than fill the disk."""
        monkeypatch.setattr(storage, "MAX_OUTPUT_BYTES", 16)
        with pytest.raises(storage.OutputTooLarge) as exc:
            local.put(a_file(tmp_path, size=64), "job.tif")
        assert exc.value.size_bytes == 64

    def test_a_refused_output_is_not_stored(self, local, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "MAX_OUTPUT_BYTES", 16)
        with pytest.raises(storage.OutputTooLarge):
            local.put(a_file(tmp_path, size=64), "job.tif")
        assert local.local_path("job.tif") is None

    @pytest.mark.parametrize("key", ["../escape.tif", "a/b.tif", ".hidden"])
    def test_traversal_keys_are_refused(self, local, tmp_path, key):
        """Keys come from job UUIDs, never user input -- cheap to rule out."""
        with pytest.raises(ValueError):
            local.put(a_file(tmp_path), key)


class TestUrls:
    def test_the_key_is_the_job_id(self):
        assert storage.key_for("8f3e") == "8f3e.tif"

    def test_the_download_url_is_absolute(self, monkeypatch):
        """`cog_uri` has to mean something to a client that is not the API."""
        monkeypatch.setattr(storage, "PUBLIC_BASE_URL", "https://bhoomi.test")
        assert storage.download_url("8f3e") == \
            "https://bhoomi.test/api/v1/jobs/8f3e/download"

    def test_a_trailing_slash_does_not_double_up(self, monkeypatch):
        monkeypatch.setattr(storage, "PUBLIC_BASE_URL", "https://bhoomi.test/")
        assert "//api" not in storage.download_url("8f3e")


class TestFactory:
    def test_the_default_is_local(self):
        storage.set_storage(None)
        assert isinstance(storage.get_storage(), storage.LocalStorage)

    def test_it_can_be_replaced(self, tmp_path):
        replacement = storage.LocalStorage(tmp_path)
        storage.set_storage(replacement)
        try:
            assert storage.get_storage() is replacement
        finally:
            storage.set_storage(None)

    def test_local_storage_satisfies_the_protocol(self, local):
        assert isinstance(local, storage.Storage)
