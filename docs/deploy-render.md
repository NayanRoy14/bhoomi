# Deploying Bhoomi on Render's free tier

A public Bhoomi where jobs really run, for nothing, with no credit card.

This is the low-friction path. [`docs/deploy.md`](deploy.md) is the better
deployment — a real VM running the full Compose stack, tiles included — and is
what to move to when this one's limits start to bite. Read the limits first;
they are the price of the zero in "no cost".

---

## What you get, and what you give up

| | |
|---|---|
| Jobs really run | ✅ search, submit, progress, COG download, OGC API |
| Cost | ✅ nothing, and no payment method on file |
| Job speed | ✅ Oregon is next to the Sentinel-2 COGs in AWS `us-west-2` |
| **API sleeps** | ⚠️ after 15 min idle; first request then takes 30–60 s |
| **Downloads are temporary** | ⚠️ ephemeral disk — a COG is gone after a redeploy or spin-down |
| **Map preview** | ❌ no free tile server. The UI says so rather than offering a dead link |
| Worker isolation | ⚠️ the worker shares the API's container and its 512 MB / 0.1 CPU |

The sleeping is less damaging than it sounds, because the frontend is a static
site that never sleeps: a visitor always gets the UI, and only the first API
call waits. It is worse for `examples/ogc_client.py`, which will look hung for
its first request.

Two of these have the same fix — an R2 bucket restores downloads that outlive a
restart *and* makes a tile server safe to add. See the last section.

## Step 1 — Postgres, at Neon rather than Render

Render's own free Postgres **expires 30 days after creation** and takes the
deployment with it. Neon's free tier does not expire, needs no card, and
supports PostGIS.

1. [neon.tech](https://neon.tech) → new project. **Choose AWS `us-west-2`**, so
   the database sits beside both Render's Oregon region and the COGs.
2. In the Neon SQL editor:
   ```sql
   CREATE EXTENSION IF NOT EXISTS postgis;
   ```
   Migration `0001` also tries this, but doing it here surfaces a permissions
   problem now rather than inside a failing deploy.
3. Copy the **pooled** connection string. It looks like
   `postgresql://user:pass@ep-xxx-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require`.

Use the pooled one. Neon's direct endpoint caps connections low, and the API
plus a forking RQ worker open more than you would guess.

## Step 2 — Deploy the blueprint

Render dashboard → **New** → **Blueprint** → select this repository. It reads
[`render.yaml`](../render.yaml) and proposes three services: `bhoomi-api`
(Docker web service), `bhoomi` (static frontend) and `bhoomi-queue` (Key Value).

Render will prompt for the four values marked `sync: false`. Two of them are
URLs of services that do not exist yet, which is a chicken-and-egg you resolve
by guessing correctly — Render's URLs are predictable from the service names:

| Variable | Service | Value |
|---|---|---|
| `BHOOMI_DATABASE_URL` | `bhoomi-api` | the Neon pooled string from step 1 |
| `BHOOMI_PUBLIC_BASE_URL` | `bhoomi-api` | `https://bhoomi-api.onrender.com` |
| `BHOOMI_CORS_ORIGINS` | `bhoomi-api` | `https://bhoomi.onrender.com` |
| `NEXT_PUBLIC_API_URL` | `bhoomi` | `https://bhoomi-api.onrender.com` |

If Render appends a suffix because a name is taken, correct these afterwards in
each service's Environment tab. **Changing `NEXT_PUBLIC_API_URL` requires a
redeploy of the frontend, not a restart** — Next.js inlines it at build time.

The first Docker build takes several minutes; rasterio's wheels are large.

## Step 3 — Check it

```bash
API=https://bhoomi-api.onrender.com

# The first call wakes the service. Allow a minute.
curl -sS $API/health
curl -sS $API/ogc/conformance | head -c 200
```

Then the real test, which speaks the standard rather than Bhoomi and needs
nothing installed:

```bash
python examples/ogc_client.py --base $API
```

If that writes a `.tif`, everything works: API, queue, worker, migrations and
all. Expect it to pause on the first request while the service wakes.

Open `https://bhoomi.onrender.com`, draw an area over Kolkata, search, pick a
scene, run NDVI. There will be no map preview — that is the missing tile
server, not a failure — and the download link gives you the GeoTIFF.

## When it does not work

| Symptom | Cause |
|---|---|
| Deploy fails at `alembic upgrade head` | PostGIS not enabled on the Neon branch, or the connection string is the direct rather than pooled endpoint. |
| Browser console shows a CORS error | `BHOOMI_CORS_ORIGINS` does not exactly match the frontend origin. Scheme included, no trailing slash. |
| Jobs sit at `queued` forever | The worker died and the restart loop is failing too. `bhoomi-api` → Logs; look for `render-start: worker exited`. |
| Job fails with a memory error | 512 MB shared between API and worker. Reduce the AOI; the fit is roughly 6.8 MB per megapixel. |
| Download 404s that worked earlier | Expected. Ephemeral disk, and the service restarted. This is the one that needs R2. |
| First request of the day hangs ~40 s | Expected. Free-tier spin-up. |

## Making it better later, cheapest first

1. **Add Cloudflare R2** (free, 10 GB, zero egress — but it does want a card on
   file). Set `BHOOMI_S3_BUCKET`, `BHOOMI_S3_ENDPOINT`, the key pair and
   `BHOOMI_S3_PUBLIC_BASE_URL` on `bhoomi-api`. Downloads then survive
   restarts, and outputs stop competing for the container's disk. No code
   change — `backend/storage.py` switches backend on the bucket variable alone.
2. **Add tiles.** Only once R2 is in place: TiTiler opens whatever path it is
   given, so it must be reading `https` objects rather than a local disk before
   it is exposed. Deploy `ghcr.io/developmentseed/titiler` as a second Render
   web service and point `BHOOMI_TITILER_URL` at it.
3. **Stop the sleeping.** $7/mo for the API on a Starter instance also buys the
   worker its own container, which is where it belongs.
4. **Move to a VM.** [`docs/deploy.md`](deploy.md) — the whole Compose stack,
   tiles included, one host, no sleeping.
