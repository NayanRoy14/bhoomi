"""Tests for output storage (PLAN.md D5, O4).

The interesting behaviour is not "a file was copied" -- it is the seam. D5
requires `cog_uri` to be a URL, O4 has not chosen a provider, and the local
backend has to satisfy the first without pretending to be the second.
"""

from __future__ import annotations

import pytest

from backend import storage
from tests.conftest import needs_s3


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


class TestPublicBaseUrlResolution:
    """Where the public base URL comes from when nobody sets it explicitly.

    This matters because a wrong value here fails quietly: the API serves every
    request correctly and only the `cog_uri` it publishes points nowhere, which
    surfaces as a broken download long after the deploy looked fine.
    """

    def test_an_explicit_value_wins(self, monkeypatch):
        """An operator who sets it means it -- a custom domain, say."""
        monkeypatch.setenv("BHOOMI_PUBLIC_BASE_URL", "https://bhoomi.example")
        monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://ignored.onrender.com")
        assert storage._public_base_url() == "https://bhoomi.example"

    def test_it_falls_back_to_the_platform(self, monkeypatch):
        """Render sets this automatically, so the URL need not be guessed."""
        monkeypatch.delenv("BHOOMI_PUBLIC_BASE_URL", raising=False)
        monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://bhoomi-api.onrender.com")
        assert storage._public_base_url() == "https://bhoomi-api.onrender.com"

    def test_an_empty_explicit_value_does_not_shadow_the_platform(self, monkeypatch):
        """Blueprints and compose files supply "" for unset optional vars.

        Treating that as a real value would leave the platform's own correct URL
        unused in favour of an empty string, and localhost would then be baked
        into every published cog_uri.
        """
        monkeypatch.setenv("BHOOMI_PUBLIC_BASE_URL", "")
        monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://bhoomi-api.onrender.com")
        assert storage._public_base_url() == "https://bhoomi-api.onrender.com"

    def test_it_falls_back_to_localhost_off_platform(self, monkeypatch):
        monkeypatch.delenv("BHOOMI_PUBLIC_BASE_URL", raising=False)
        monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
        assert storage._public_base_url() == "http://localhost:8000"


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


# ------------------------------------------------ object storage (needs S3)

class TestS3Configuration:
    """No server needed: how the backend is selected and addressed."""

    def test_a_bucket_switches_the_default_backend(self, monkeypatch):
        monkeypatch.setenv("BHOOMI_S3_BUCKET", "bhoomi-outputs")
        storage.set_storage(None)
        try:
            assert isinstance(storage.get_storage(), storage.S3Storage)
        finally:
            storage.set_storage(None)

    def test_no_bucket_keeps_local_disk(self, monkeypatch):
        monkeypatch.delenv("BHOOMI_S3_BUCKET", raising=False)
        storage.set_storage(None)
        try:
            assert isinstance(storage.get_storage(), storage.LocalStorage)
        finally:
            storage.set_storage(None)

    def test_a_bucket_is_required(self, monkeypatch):
        monkeypatch.delenv("BHOOMI_S3_BUCKET", raising=False)
        with pytest.raises(ValueError, match="BHOOMI_S3_BUCKET"):
            storage.S3Storage()

    def test_a_private_bucket_has_no_stable_url(self):
        """A presigned one would expire inside a 30-day-lived cog_uri row."""
        store = storage.S3Storage(bucket="b", public_base_url="")
        assert store.url_for("a.tif") is None

    def test_a_public_bucket_exposes_the_object_directly(self):
        store = storage.S3Storage(bucket="b", public_base_url="https://cdn.test")
        assert store.url_for("a.tif") == "https://cdn.test/a.tif"

    def test_tiles_prefer_the_public_url_so_titiler_needs_no_keys(self):
        store = storage.S3Storage(bucket="b", public_base_url="https://cdn.test/")
        assert store.tile_source("a.tif") == "https://cdn.test/a.tif"

    def test_a_private_bucket_falls_back_to_vsis3(self):
        store = storage.S3Storage(bucket="bhoomi", public_base_url="")
        assert store.tile_source("a.tif") == "/vsis3/bhoomi/a.tif"

    def test_it_is_never_a_filesystem(self):
        store = storage.S3Storage(bucket="b")
        assert store.local_path("a.tif") is None
        # No point staging near a destination reached over the network.
        assert store.scratch_dir() is None

    def test_it_satisfies_the_protocol(self):
        assert isinstance(storage.S3Storage(bucket="b"), storage.Storage)


@needs_s3
class TestS3RoundTrip:
    """Against a real S3-compatible server. See the s3_bucket fixture."""

    def test_put_then_read_back(self, s3_bucket, tmp_path):
        source = a_file(tmp_path, size=4096)
        assert s3_bucket.put(source, "job.tif") == 4096
        assert b"".join(s3_bucket.open_stream("job.tif")) == b"x" * 4096

    def test_a_missing_object_streams_as_none(self, s3_bucket):
        assert s3_bucket.open_stream("never-written.tif") is None

    def test_a_real_cog_survives_the_round_trip(self, s3_bucket, tmp_path):
        """Bytes, not just length: a corrupted upload would still have a size."""
        import hashlib

        source = tmp_path / "cog.tif"
        source.write_bytes(bytes(range(256)) * 4096)   # 1 MB, non-uniform
        digest = hashlib.sha256(source.read_bytes()).hexdigest()

        s3_bucket.put(source, "cog.tif")
        got = b"".join(s3_bucket.open_stream("cog.tif"))
        assert hashlib.sha256(got).hexdigest() == digest

    def test_put_leaves_the_source_for_its_caller_to_clean_up(self, s3_bucket, tmp_path):
        """Unlike the local backend, which moves. _publish's TemporaryDirectory
        removes it either way."""
        source = a_file(tmp_path)
        s3_bucket.put(source, "job.tif")
        assert source.exists()

    def test_delete(self, s3_bucket, tmp_path):
        s3_bucket.put(a_file(tmp_path), "job.tif")
        s3_bucket.delete("job.tif")
        assert s3_bucket.open_stream("job.tif") is None

    def test_an_oversized_output_is_refused_before_upload(self, s3_bucket, tmp_path,
                                                          monkeypatch):
        monkeypatch.setattr(storage, "MAX_OUTPUT_BYTES", 16)
        with pytest.raises(storage.OutputTooLarge):
            s3_bucket.put(a_file(tmp_path, size=64), "job.tif")
        assert s3_bucket.open_stream("job.tif") is None
