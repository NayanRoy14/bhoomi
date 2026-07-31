"""Where finished COGs live (PLAN.md D5, O4).

D5 says outputs belong in object storage and that `outputs.cog_uri` is a URL,
never a filesystem path. O4 — R2 vs S3 vs B2 — is not due until 2026-12-31,
and waiting for it would mean no working NDVI job until then. So this is the
seam rather than the decision: a Protocol with a local-filesystem backend now,
and an S3-compatible one added when O4 resolves, changing one class.

`cog_uri` is a URL in both cases. The local backend has no public endpoint of
its own, so it returns None from `url_for` and the caller falls back to the
API's own `/download` route (7.5) — which is a URL, reachable by TiTiler, and
does not encode a path into the database.

**LocalStorage is single-host.** The worker writes the file and the API serves
it, so they must share a filesystem; compose gives them a named volume. That
constraint is precisely what object storage removes, and it is the argument
for settling O4 before the deployment has more than one box.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Where the API is reachable from outside. Used to turn a download route into
#: an absolute URL, because `cog_uri` has to mean something to a client that is
#: not the API itself -- TiTiler, or a `gdalinfo /vsicurl/...`.
PUBLIC_BASE_URL = os.getenv("BHOOMI_PUBLIC_BASE_URL", "http://localhost:8000")

DEFAULT_OUTPUT_DIR = Path(os.getenv("BHOOMI_OUTPUT_DIR", "outputs/jobs"))

#: PLAN.md 8. Refuse to publish beyond this rather than filling the disk; a
#: 500 km2 NDVI COG is ~20 MB, so this is roughly a 10x headroom backstop.
MAX_OUTPUT_BYTES = int(os.getenv("BHOOMI_MAX_OUTPUT_MB", "200")) * 1024 * 1024


class OutputTooLarge(RuntimeError):
    """A finished raster exceeded the 8 size cap and was not published."""

    def __init__(self, size_bytes: int, limit: int) -> None:
        self.size_bytes, self.limit = size_bytes, limit
        super().__init__(
            f"Output is {size_bytes / 1e6:.0f} MB; the maximum is {limit / 1e6:.0f} MB.")


@runtime_checkable
class Storage(Protocol):
    def put(self, source: Path, key: str) -> int:
        """Store `source` under `key`. Returns the size in bytes."""

    def url_for(self, key: str) -> str | None:
        """A **stable** directly-fetchable URL, or None if the API must serve it.

        Stable matters: this ends up in `outputs.cog_uri`, which lives for 30
        days (PLAN.md 6). A presigned URL would satisfy "fetchable" and then
        expire inside the row, so a private bucket returns None here and is
        served through the API instead.
        """

    def local_path(self, key: str) -> Path | None:
        """A readable path, or None for backends that are not filesystems."""

    def open_stream(self, key: str) -> Iterator[bytes] | None:
        """The object's bytes in chunks, or None if it is not there.

        How a backend with neither a public URL nor a filesystem gets served by
        `/download` (7.5).
        """

    def tile_source(self, key: str) -> str | None:
        """What to hand a tile server so it can read this object, or None.

        Not the same as `url_for`. A tile server reads the COG directly, over
        many small range requests per tile; `url_for` is what a *client* is
        given. For object storage the two coincide. For the local filesystem
        they do not: the tile server shares the volume and reads the path,
        which avoids routing every tile through the API -- where each one would
        cost a database lookup and a rate-limiter charge.
        """

    def scratch_dir(self) -> Path | None:
        """Where to build an object so that storing it is cheap, or None.

        A COG has to be written to a path before it can be stored. Writing it
        under the system temp directory and then storing it copies the whole
        file across devices -- `/tmp` and a mounted volume are not the same
        filesystem. Staging inside the destination makes the store a rename.
        Remote backends have no such advantage and return None.
        """

    def delete(self, key: str) -> None:
        ...


class LocalStorage(Storage):
    """A directory on disk. Development, and single-box deployments."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_OUTPUT_DIR
        self._lock = threading.Lock()

    def put(self, source: Path, key: str) -> int:
        size = Path(source).stat().st_size
        if size > MAX_OUTPUT_BYTES:
            raise OutputTooLarge(size, MAX_OUTPUT_BYTES)
        destination = self._path(key)
        with self._lock:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # move, not copy: the source is a scratch file the caller is about
            # to discard, and a 20 MB copy per job is pure waste.
            shutil.move(str(source), destination)
        logger.info("stored %s (%.1f MB)", key, size / 1e6)
        return size

    def url_for(self, key: str) -> str | None:
        return None  # served by the API; see the module docstring

    def local_path(self, key: str) -> Path | None:
        path = self._path(key)
        return path if path.exists() else None

    def tile_source(self, key: str) -> str | None:
        """The path as the tile server sees it.

        `BHOOMI_TILE_ROOT` exists because the two containers mount the same
        volume and need not mount it at the same place. It defaults to this
        backend's own root, which is what compose arranges.

        **This hands the tile server a filesystem root.** TiTiler will open
        whatever path it is given, so it must not be reachable from outside the
        deployment while this is how it reads outputs -- compose binds it to
        127.0.0.1 for that reason. Object storage (PLAN.md O4) removes the
        problem rather than mitigating it: `tile_source` becomes an https URL
        and the tile server stops touching a filesystem at all.
        """
        if self.local_path(key) is None:
            return None
        root = os.getenv("BHOOMI_TILE_ROOT") or str(self.root)
        return f"{root.rstrip('/')}/{key}"

    def scratch_dir(self) -> Path | None:
        """Inside the root, so `put` is a rename rather than a copy.

        A sibling directory rather than the root itself: a half-written COG
        must never be visible under a key that `local_path` would serve.
        """
        scratch = self.root / ".scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    def open_stream(self, key: str) -> Iterator[bytes] | None:
        """Not used: `/download` prefers `local_path`, which streams natively."""
        path = self.local_path(key)
        if path is None:
            return None

        def chunks() -> Iterator[bytes]:
            with path.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    yield block

        return chunks()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        # Keys are generated from job UUIDs, never from user input, but a
        # traversal here would read arbitrary files -- cheap to rule out.
        if "/" in key or "\\" in key or key.startswith("."):
            raise ValueError(f"Unsafe storage key {key!r}")
        return self.root / key


class S3Storage(Storage):
    """Any S3-compatible object store. Cloudflare R2 is the one chosen (D14).

    Not named `R2Storage`, because nothing here is Cloudflare-specific: R2, AWS
    S3, MinIO and Backblaze's S3 endpoint all differ by an endpoint URL and a
    key pair. That is exactly what made D14 safe to decide before benchmarking
    -- being wrong costs configuration, not code.

    ## How the tile server reads these

    `tile_source` prefers a public URL, because then TiTiler needs no
    credentials at all and reads over `/vsicurl/`. With a private bucket it
    falls back to GDAL's `/vsis3/bucket/key`, which requires the tile server to
    carry the same credentials and endpoint. Public is the better shape for
    tiles -- they are meant to be seen -- and R2 supports it through a bucket's
    public r2.dev address or a custom domain.

    ## Why `url_for` is None for a private bucket

    A presigned URL would work for a download and then expire inside
    `outputs.cog_uri`, which lives 30 days. So a private bucket has no stable
    public URL, `cog_uri` falls back to the API's own `/download` route, and
    that route streams the object through. See `Storage.url_for`.
    """

    def __init__(self, bucket: str | None = None, endpoint: str | None = None,
                 access_key: str | None = None, secret_key: str | None = None,
                 region: str | None = None, public_base_url: str | None = None) -> None:
        self.bucket = bucket or os.getenv("BHOOMI_S3_BUCKET", "")
        self.endpoint = endpoint or os.getenv("BHOOMI_S3_ENDPOINT") or None
        self.access_key = access_key or os.getenv("BHOOMI_S3_ACCESS_KEY_ID") or None
        self.secret_key = secret_key or os.getenv("BHOOMI_S3_SECRET_ACCESS_KEY") or None
        # R2 ignores the region but the SDK insists on one; "auto" is what
        # Cloudflare's own documentation uses.
        self.region = region or os.getenv("BHOOMI_S3_REGION") or "auto"
        self.public_base_url = (
            public_base_url
            if public_base_url is not None
            else os.getenv("BHOOMI_S3_PUBLIC_BASE_URL", "")
        )
        if not self.bucket:
            raise ValueError("S3Storage needs a bucket; set BHOOMI_S3_BUCKET.")
        self._client = None
        self._client_lock = threading.Lock()

    @property
    def client(self):
        with self._client_lock:
            if self._client is None:
                import boto3
                from botocore.config import Config

                self._client = boto3.client(
                    "s3",
                    endpoint_url=self.endpoint,
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key,
                    region_name=self.region,
                    # R2 does not implement checksum trailers the way recent
                    # botocore versions send them by default, and rejects the
                    # upload. Requesting them only when asked keeps this working
                    # against R2, MinIO and S3 alike.
                    config=Config(
                        signature_version="s3v4",
                        request_checksum_calculation="when_required",
                        response_checksum_validation="when_required",
                        retries={"max_attempts": 3, "mode": "standard"},
                    ),
                )
            return self._client

    def put(self, source: Path, key: str) -> int:
        size = Path(source).stat().st_size
        if size > MAX_OUTPUT_BYTES:
            raise OutputTooLarge(size, MAX_OUTPUT_BYTES)
        self.client.upload_file(
            str(source), self.bucket, key,
            ExtraArgs={"ContentType": "image/tiff"},
        )
        # Unlike the local backend this copies rather than moves, so the
        # caller's scratch file still exists; its TemporaryDirectory removes it.
        logger.info("stored s3://%s/%s (%.1f MB)", self.bucket, key, size / 1e6)
        return size

    def url_for(self, key: str) -> str | None:
        if not self.public_base_url:
            return None
        return f"{self.public_base_url.rstrip('/')}/{key}"

    def local_path(self, key: str) -> Path | None:
        return None

    def scratch_dir(self) -> Path | None:
        # No advantage to staging anywhere in particular: the upload is a copy
        # over the network either way, so the system temp directory is right.
        return None

    def tile_source(self, key: str) -> str | None:
        public = self.url_for(key)
        if public:
            return public
        # GDAL reads this with AWS_S3_ENDPOINT and the same credentials, which
        # the tile server must be given. See docker-compose.yml.
        return f"/vsis3/{self.bucket}/{key}"

    def open_stream(self, key: str) -> Iterator[bytes] | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # botocore raises ClientError for a missing key
            logger.info("object %s unavailable: %s", key, exc)
            return None

        def chunks() -> Iterator[bytes]:
            with response["Body"] as body:
                while block := body.read(1024 * 1024):
                    yield block

        return chunks()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_storage: Storage | None = None
_factory_lock = threading.Lock()


def get_storage() -> Storage:
    """Object storage when a bucket is configured, local disk otherwise.

    Selected by the presence of `BHOOMI_S3_BUCKET` rather than by a mode flag,
    the same way `BHOOMI_DATABASE_URL` selects the scene cache: one setting to
    get wrong instead of two that can disagree.
    """
    global _storage
    with _factory_lock:
        if _storage is None:
            _storage = S3Storage() if os.getenv("BHOOMI_S3_BUCKET") else LocalStorage()
            logger.info("output storage: %s", type(_storage).__name__)
        return _storage


def set_storage(storage: Storage | None) -> None:
    """Replace the backend. Tests, and the S3 bootstrap when O4 resolves."""
    global _storage
    with _factory_lock:
        _storage = storage


def key_for(job_id: str) -> str:
    """One output per job in V1, so the job id is the whole key."""
    return f"{job_id}.tif"


def download_url(job_id: str) -> str:
    """The 7.5 download route, absolute so it is usable off-host."""
    return f"{PUBLIC_BASE_URL.rstrip('/')}/api/v1/jobs/{job_id}/download"
