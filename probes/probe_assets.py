"""Probe 2: baseline consistency in c1-l2a, and whether anonymous HTTP range
reads actually work from India (the core architectural premise of Bhoomi)."""
import json
import urllib.request

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
KOLKATA_BBOX = [88.20, 22.40, 88.50, 22.70]


def post(url, payload, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def find(coll, date):
    r = post(f"{EARTH_SEARCH}/search", {
        "collections": [coll], "bbox": KOLKATA_BBOX,
        "datetime": f"{date}T00:00:00Z/{date}T23:59:59Z", "limit": 5})
    return r["features"]


print("=" * 74)
print("BASELINE CONSISTENCY — is the 2020 archive reprocessed?")
print("=" * 74)
for coll, date in [("sentinel-2-l2a", "2020-03-10"), ("sentinel-2-l2a", "2026-03-04"),
                   ("sentinel-2-c1-l2a", "2020-03-10"), ("sentinel-2-c1-l2a", "2026-03-04")]:
    feats = find(coll, date)
    if not feats:
        print(f"\n{coll:<20} {date}  -> no scene")
        continue
    p = feats[0]["properties"]
    print(f"\n{coll:<20} {date}")
    print(f"   id                          {feats[0]['id']}")
    print(f"   s2:processing_baseline      {p.get('s2:processing_baseline')}")
    print(f"   earthsearch:boa_offset_...  {p.get('earthsearch:boa_offset_applied', 'ABSENT')}")
    print(f"   eo:cloud_cover              {p.get('eo:cloud_cover')}")
    print(f"   proj:code / epsg            {p.get('proj:code', p.get('proj:epsg'))}")
    bands = sorted(a for a in feats[0]["assets"]
                   if a in {"red", "green", "blue", "nir", "swir16", "swir22", "scl"})
    print(f"   band assets                 {bands}")

print()
print("=" * 74)
print("HTTP RANGE READ TEST — the core architectural premise")
print("=" * 74)
feats = find("sentinel-2-l2a", "2026-03-04")
red = feats[0]["assets"]["red"]
url = red["href"]
print(f"\nasset: {url}")
print(f"type : {red.get('type')}")

# HEAD for size + range support
req = urllib.request.Request(url, method="HEAD")
with urllib.request.urlopen(req, timeout=60) as r:
    size = int(r.headers.get("Content-Length", 0))
    print(f"size : {size / 1024 / 1024:.1f} MB")
    print(f"accept-ranges: {r.headers.get('Accept-Ranges')}")

# Fetch only the first 32 KB - enough for the COG header + IFDs
import time
t0 = time.time()
req = urllib.request.Request(url, headers={"Range": "bytes=0-32767"})
with urllib.request.urlopen(req, timeout=60) as r:
    chunk = r.read()
    dt = time.time() - t0
print(f"\nrange request status: {r.status} (206 == partial content works)")
print(f"fetched {len(chunk)} bytes of {size} in {dt:.2f}s "
      f"({len(chunk)/size*100:.3f}% of the file)")
print(f"TIFF magic: {chunk[:4]!r}  (b'II*\\x00' or b'MM\\x00*' == valid TIFF)")
