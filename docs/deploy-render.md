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
| **Downloads are temporary** | ⚠️ ephemeral disk — a COG is gone after a redeploy or spin-down. [Step 4](#step-4--r2-and-the-map-preview) fixes it |
| **Map preview** | ⚠️ off until [step 4](#step-4--r2-and-the-map-preview); until then the UI says so rather than offering a dead link |
| Worker isolation | ⚠️ the worker shares the API's container and its 512 MB / 0.1 CPU |

The sleeping is less damaging than it sounds, because the frontend is a static
site that never sleeps: a visitor always gets the UI, and only the first API
call waits. It is worse for `examples/ogc_client.py`, which will look hung for
its first request.

Two of these have the same fix, and it is free: an R2 bucket restores downloads
that outlive a restart *and* makes a tile server safe to add. That is
[step 4](#step-4--r2-and-the-map-preview), and it is optional — steps 1 to 3
give you a working deployment on their own.

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
[`render.yaml`](../render.yaml) and proposes four services: `bhoomi-api`
(Docker web service), `bhoomi-site` (static frontend), `bhoomi-tiles` (TiTiler)
and `bhoomi-queue` (Key Value).

`bhoomi-tiles` does nothing until [step 4](#step-4--r2-and-the-map-preview) and
costs nothing to leave running — the API only sends the browser to it once
`BHOOMI_TITILER_URL` is set. Skip it now if you would rather deploy less.

Render prompts for the values marked `sync: false`. There are now three, and
only one of them is a URL of a service that does not exist yet.

`BHOOMI_PUBLIC_BASE_URL` used to be prompted and no longer is: Render sets
`RENDER_EXTERNAL_URL` automatically in every web service, and the API falls back
to it. Set it by hand only to override — a custom domain in front of Render, say.

> ⚠️ **Do not guess these, and do not assume the service names survive.**
> `onrender.com` is one global namespace, so a taken name gets four random
> characters appended: asking for `bhoomi` can yield `bhoomi-8t7g.onrender.com`.
> There is no way to know in advance, and the failure is quiet — the API is
> healthy and answers `curl` perfectly while the browser reports it as
> unreachable, because the CORS preflight from an origin that is not on the
> list is rejected before any of your code runs.
>
> Deploy the blueprint with placeholders, then **read each service's real URL
> off its own service page** (it sits under the service name at the top) and
> correct the variables in each Environment tab. Check *every* service. Getting
> one right and assuming the other followed the same pattern is exactly how
> this was got wrong the first time.

| Variable | Service | Value |
|---|---|---|
| `BHOOMI_DATABASE_URL` | `bhoomi-api` | the Neon pooled string from step 1 |
| `BHOOMI_CORS_ORIGINS` | `bhoomi-api` | the **static site's** URL, exactly — scheme included, no trailing slash |
| `NEXT_PUBLIC_API_URL` | `bhoomi` | the API service's URL |

`BHOOMI_CORS_ORIGINS` is the one that cannot be automated, and it is worth
knowing why rather than assuming it was an oversight. `fromService` reads
another service's *private network* hostname, which is not the public origin a
browser sends, and it does not accept a static site as a source at all — so the
API has no way to ask Render what the frontend's public URL is.

Changing `BHOOMI_CORS_ORIGINS` restarts the API, which takes about a minute.
**Changing `NEXT_PUBLIC_API_URL` requires a redeploy of the frontend, not a
restart** — Next.js inlines it at build time.

To confirm CORS rather than trust it, ask the API what it will allow, using the
frontend's origin:

```bash
curl -s -o /dev/null -D - -X OPTIONS https://<api>/api/v1/scenes/search \
  -H 'Origin: https://<frontend>' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type' | grep -i access-control-allow-origin
```

An `access-control-allow-origin` line echoing your frontend means the browser
will work. No such line means it will not, however healthy `/health` looks.

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

Open the static site (its URL is on its own service page), draw an area over Kolkata, search, pick a
scene, run NDVI. There will be no map preview yet — that is
[step 4](#step-4--r2-and-the-map-preview), not a failure — and the download
link gives you the GeoTIFF.

## Step 4 — R2 and the map preview

Optional, free, and no code change: `backend/storage.py` selects its backend on
the presence of `BHOOMI_S3_BUCKET` alone. Skip this and everything above still
works, with a temporary download link and no map.

Doing it fixes two of the three ⚠️ rows at once, which is not a coincidence —
they are the same missing piece. Outputs on an ephemeral container disk cannot
outlive a restart, and a tile server cannot be safely exposed while the thing
it reads is a filesystem.

**Cloudflare does ask for a card to enable R2**, even though this stays inside
the free 10 GB. That is the one prerequisite that is not free of friction.

1. **Create the bucket.** Cloudflare dashboard → R2 → *Create bucket*. Any
   name; location hint `APAC` if offered.
2. **Make it public.** Bucket → Settings → *Public access* → enable the
   `r2.dev` subdomain, or attach a custom domain. Copy that address — it is
   `BHOOMI_S3_PUBLIC_BASE_URL`, and it is **not** the same host as the S3
   endpoint in the next step.

   A custom domain is better than `r2.dev` if you have a domain on Cloudflare:
   the `r2.dev` address is rate limited by Cloudflare and documented as being
   for development. Tiles issue many small requests, which is exactly the
   traffic shape that finds a rate limit.

   Public means anyone with the URL can read the object. `/download` is already
   anonymous by design (PLAN.md §1.4), so this publishes nothing that was not
   already being served — but it is a deliberate choice, not a default.
3. **Create an API token.** R2 → *Manage API tokens* → *Object Read & Write*,
   scoped to this bucket. You get an access key id, a secret, and an endpoint
   of the form `https://<account-id>.r2.cloudflarestorage.com`.
4. **Set five variables on `bhoomi-api`**, then let it redeploy:

   | Variable | Value |
   |---|---|
   | `BHOOMI_S3_BUCKET` | the bucket name |
   | `BHOOMI_S3_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` |
   | `BHOOMI_S3_ACCESS_KEY_ID` | from step 3 |
   | `BHOOMI_S3_SECRET_ACCESS_KEY` | from step 3 |
   | `BHOOMI_S3_PUBLIC_BASE_URL` | the `r2.dev` or custom domain from step 2 |

   Run a job. `cog_uri` in the response should now be an R2 URL rather than a
   `/download` link on the API — that is the check that the bucket is really
   being written to, and it is worth doing before adding the tile server.
5. **Enable `bhoomi-tiles`.** It is already in `render.yaml`, so a blueprint
   sync creates it. Set `TITILER_API_CORS_ORIGINS` on it to the static site's
   origin — the same value as the API's `BHOOMI_CORS_ORIGINS`, because the
   browser fetches tiles from this service directly.
6. **Point the API at it.** Set `BHOOMI_TITILER_URL` on `bhoomi-api` to the
   tile service's own public URL, scheme included, no trailing slash. Read it
   off that service's page rather than guessing it from the name — `onrender.com`
   is one global namespace and a taken name gets four random characters
   appended.

   **Do this last.** Setting it while outputs are still on local disk gives the
   UI tile URLs that a public TiTiler cannot read, and would mean exposing a
   tile server that reads a filesystem — see the warning in `README.md`.

Check it end to end:

```bash
# A completed job's tiles field should now be an XYZ template, not null.
curl -sS $API/api/v1/jobs/<job-id> | grep -o '"tiles":[^,]*'

# And the tile server should answer for the COG itself.
curl -sI "https://<tiles-service>/cog/info?url=<the cog_uri>"
```

`200` from the second means TiTiler can read R2. A `403` or `404` almost always
means the bucket is not actually public — step 2 enables it per bucket, not per
account.

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

1. **Add R2 and tiles.** [Step 4](#step-4--r2-and-the-map-preview) below. Both
   the ⚠️ on downloads and the ❌ on the map preview come off the table, at no
   cost and with no code change.
2. **Stop the sleeping.** $7/mo for the API on a Starter instance also buys the
   worker its own container, which is where it belongs.
4. **Move to a VM.** [`docs/deploy.md`](deploy.md) — the whole Compose stack,
   tiles included, one host, no sleeping.
