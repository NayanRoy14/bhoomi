# Bhoomi API.
#
# rasterio's wheels bundle GDAL, so no system GDAL package is required -- which
# is what keeps this on slim rather than a multi-hundred-megabyte GDAL base.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # GDAL settings for reading COGs over HTTP. Without these GDAL issues far
    # more requests than necessary, turning a windowed read into a slow scan.
    GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR \
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif \
    GDAL_HTTP_MULTIPLEX=YES \
    VSI_CACHE=TRUE \
    VSI_CACHE_SIZE=67108864

WORKDIR /app

# rasterio's wheel bundles GDAL, but GDAL is still dynamically linked against
# libexpat for XML parsing, and python:3.12-slim does not ship it. Without this
# `import rasterio` dies with `libexpat.so.1: cannot open shared object file`.
# It is the one system library the bundled-GDAL claim above does not cover.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so the layer caches across source changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY processing/ ./processing/
COPY catalogue/ ./catalogue/
# cache.py alongside pipeline.py: pipeline imports it, so copying one without
# the other leaves an image whose `import pipeline` fails at runtime.
COPY pipeline.py cache.py alembic.ini ./
COPY backend/ ./backend/
RUN chmod +x backend/entrypoint.sh

# Do not run as root.
RUN useradd --create-home --uid 10001 bhoomi && chown -R bhoomi:bhoomi /app
USER bhoomi

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["backend/entrypoint.sh"]
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
