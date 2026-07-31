# Deploying Bhoomi for free

A public, always-on Bhoomi where jobs really run, at no cost, on Oracle Cloud's
Always Free tier plus Cloudflare R2's free tier.

This is a runbook, not an overview. It assumes [`docs/architecture.md`](architecture.md).

---

## Why this shape

Three constraints rule out the obvious answers, and it is worth knowing which
before substituting a platform you prefer.

**The worker cannot be serverless.** A job reads bands over HTTP and takes from
about ten seconds to several minutes. Vercel, Netlify Functions and Cloudflare
Workers all cap a request far below that. Bhoomi needs one process that stays
up and pulls from a queue, which is a container or a VM, not a function.

**Postgres must be PostGIS.** `scenes.geometry` is `GEOMETRY(Polygon, 4326)`
and migration `0001` enables the extension. A generic free Postgres without
PostGIS fails on first migration.

**Redis must support blocking reads.** RQ's worker blocks on `BLPOP`. Some
serverless Redis products either do not implement blocking commands or bill
them as connection time, so the cheapest "free Redis" is often the wrong one.

Memory is *not* a constraint, which is worth saying because it is the thing
people size for: peak worker RSS measured at **115 MB**, about 6.8 MB per
megapixel (PLAN.md §8). A 1 GB box is comfortable.

**Region is the thing that actually decides whether the demo feels good.**
Sentinel-2 COGs live in AWS `us-west-2`. Measured from a home connection in
India, one 3-band read of a 0.29 Mpx window ranged from 5 s to 175 s and a
full NDVI took 352 s. The same job on a host near `us-west-2` finishes in
about 11 s. Put the worker on the US west coast even though the audience is in
India: the user waits on the *job*, and the job waits on S3. Page loads are a
few hundred milliseconds either way; jobs are a factor of thirty.

---

## Step 1 — Cloudflare R2, first and non-negotiable

**Do this before anything is publicly reachable.** TiTiler opens whatever path
its `url` parameter names. While outputs live on a filesystem, a reachable tile
server is an arbitrary-file-read of the container. The dev compose binds it to
`127.0.0.1` for exactly this reason. Object storage *removes* the exposure
rather than hiding it: `tile_source` becomes an `https` URL and the tile server
gets no filesystem at all.

`docker-compose.prod.yml` enforces the second half — it mounts no volume on
TiTiler — but it cannot enforce that you set the bucket. Without R2 you get a
tile server that can read nothing, which is the correct failure. Do not "fix"
it by adding the volume back.

1. Cloudflare dashboard → **R2** → *Create bucket*, name it `bhoomi-outputs`.
   The free tier is 10 GB of storage and, importantly, **zero egress cost** —
   which is what makes serving tiles from it free rather than merely cheap.
2. **Settings → Public access → Allow public access** via the managed
   `r2.dev` subdomain. Copy that URL; it becomes `BHOOMI_S3_PUBLIC_BASE_URL`.
   A custom domain works too and is faster, but needs a domain on Cloudflare.
3. **Manage R2 API Tokens** → *Create token*, permission **Object Read & Write**,
   scoped to this bucket. Copy the access key id and secret **now** — the secret
   is shown once.
4. Note your account id from the R2 overview page. The S3 endpoint is
   `https://<account-id>.r2.cloudflarestorage.com`.

Public read on the bucket is deliberate: a COG is a published result, the
`cog_uri` is meant to be loadable by QGIS, and a presigned URL would expire
inside the 30-day lifetime of the row that stores it.

## Step 2 — The instance

Oracle Cloud → *Create instance*.

- **Shape:** `VM.Standard.A1.Flex`, **Ampere ARM**. Always Free gives 4 OCPUs
  and 24 GB across your A1 instances, permanently — not a trial credit. Ask for
  2 OCPU / 12 GB and leave headroom.
- **Region:** `us-sanjose-1` or `us-phoenix-1`. See the region note above.
- **Image:** Ubuntu 22.04 or 24.04 (arm64).
- **SSH key:** upload yours; save the public IP it assigns.

> **ARM is not free of consequences.** `postgis/postgis` publishes **amd64
> only** and will not start on A1 at all. `docker-compose.prod.yml` therefore
> pins `imresamu/postgis:16-3.4`, the multi-arch build of the same pairing.
> `redis`, `titiler`, `python` and `node` all publish arm64 already; this was
> the only substitution needed.
>
> If A1 capacity is exhausted in your region — common, and it presents as
> "Out of host capacity" — either retry over a few days or take the two
> Always Free **AMD** micro instances (1/8 OCPU, 1 GB each) instead. 1 GB is
> enough given the 115 MB working set, but you would run Postgres on the second
> instance, and on AMD you can drop the `BHOOMI_POSTGIS_IMAGE` override.

## Step 3 — A domain, because Let's Encrypt will not certify an IP

Caddy gets a certificate automatically, but only for a name. Free options:
[DuckDNS](https://www.duckdns.org) gives `something.duckdns.org` pointed at your
IP in about a minute, and it works with Let's Encrypt's HTTP challenge. Any
domain you already own is better.

Point the A record at the instance's public IP and confirm it resolves before
starting Caddy — a failed challenge counts against Let's Encrypt's limit of
five per domain per week.

## Step 4 — Open the ports, in both places

This is where a first Oracle deploy usually appears to hang, because the
symptom is a silent timeout rather than a refusal.

**Cloud side:** VCN → Security Lists → default → *Add ingress rules*, source
`0.0.0.0/0`, TCP, destination ports **80** and **443**.

**Instance side:** Oracle's Ubuntu images ship a restrictive `iptables`
independent of the cloud firewall. Both are required.

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## Step 5 — Docker

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
```

`docker-compose.prod.yml` uses the `!override` YAML tag to *remove* the dev
volumes and host ports rather than merge with them, which needs **Compose
v2.24 or newer**. Check with `docker compose version`; if it is older, install
the plugin from Docker's own repository rather than `apt`.

## Step 6 — Configure

```bash
git clone https://github.com/NayanRoy14/bhoomi.git && cd bhoomi
cp .env.example .env
```

Edit `.env`:

```bash
BHOOMI_DOMAIN=bhoomi.duckdns.org           # no scheme, no trailing slash
POSTGRES_PASSWORD=<a long random string>

BHOOMI_S3_BUCKET=bhoomi-outputs
BHOOMI_S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
BHOOMI_S3_ACCESS_KEY_ID=<from step 1>
BHOOMI_S3_SECRET_ACCESS_KEY=<from step 1>
BHOOMI_S3_REGION=auto
BHOOMI_S3_PUBLIC_BASE_URL=https://pub-xxxx.r2.dev

# GDAL reads /vsis3/ with the endpoint host only -- no scheme.
BHOOMI_S3_GDAL_ENDPOINT=<account-id>.r2.cloudflarestorage.com
BHOOMI_S3_GDAL_HTTPS=YES

# Tighter than the defaults, because this is free compute on the open internet.
BHOOMI_JOB_LIMIT=5
BHOOMI_SEARCH_LIMIT=60
```

`BHOOMI_DOMAIN` is the only value that appears in several places, and the
overlay derives all of them from it: the API's public base URL, the tile URL,
CORS, and the frontend's `NEXT_PUBLIC_API_URL`. Because Next.js inlines that
last one at build time, **changing the domain means rebuilding the frontend
image**, not restarting it.

## Step 7 — Up

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The first build takes a while on 2 ARM cores. Then:

```bash
curl -sS https://$BHOOMI_DOMAIN/health
curl -sS https://$BHOOMI_DOMAIN/ogc/conformance | head -c 200
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

The real acceptance test is the one that speaks the standard rather than
Bhoomi, and it needs no browser and no dependencies:

```bash
python examples/ogc_client.py --base https://$BHOOMI_DOMAIN
```

That discovers the processes from the landing page, reads the input schema,
executes NDVI, polls, and downloads the GeoTIFF. If it writes a `.tif`, the
deployment is genuinely working — API, queue, worker, R2 and all.

---

## What is exposed, and what is not

| | |
|---|---|
| `443` | Caddy only. Everything else talks over the compose network. |
| Postgres, Redis | No host port in the prod overlay. Not reachable from outside. |
| TiTiler | Reachable *only* via `/tiles/*` through Caddy, with no filesystem mounted. It can read R2 over https and nothing else. |
| `/docs` | FastAPI's Swagger UI, public. Remove the route if you would rather it were not. |

Rate limits are per-IP and in-process. `BHOOMI_TRUSTED_PROXY_HOPS=1` in the
overlay is what makes them see the real client rather than Caddy — without it
every request looks like one client and the whole internet shares one budget.
It is set to exactly `1` because exactly one proxy of ours sets
`X-Forwarded-For`; a larger number lets a client forge the header and mint
itself a fresh budget per request.

Note the limiter is in-memory per process, so with the API replicated the
effective limit is the configured number times the replica count. One API
container, as here, makes it exact.

## Costs, honestly

Free, with two edges worth watching. R2 is 10 GB and outputs are roughly 1 MB
each, so a few thousand jobs before it matters — set a bucket lifecycle rule to
expire objects after 30 days, which matches the row lifetime. Oracle reclaims
*idle* Always Free compute; an always-on worker polling Redis is not idle, so
this stack is not at risk, but a stopped instance can be.

## When it does not work

| Symptom | Cause |
|---|---|
| `https://` times out, `http://` too | Ports open in the VCN but not in `iptables`, or the reverse. Both are needed. |
| Caddy logs an ACME failure | DNS not resolving to this host yet, or port 80 closed. Port 80 must stay open even though everything redirects to 443 — the HTTP challenge uses it. |
| `exec format error` on postgres | The amd64-only `postgis/postgis` on ARM. Use the overlay's default rather than overriding it. |
| Jobs stay `queued` | The worker cannot reach Redis. `docker compose logs worker`. |
| Jobs `failed` with an S3 error | R2 keys, or `BHOOMI_S3_GDAL_ENDPOINT` still carrying `https://` — GDAL wants the bare host. |
| Map shows no tiles, download works | `BHOOMI_S3_PUBLIC_BASE_URL` unset or the bucket is not public, so TiTiler cannot read the object over https. |
| First job of the day is slow, later ones fast | Expected. The large cost is establishing the connection to S3; a warm worker is much faster. See the note in `probes/benchmark_band_concurrency.py`. |
