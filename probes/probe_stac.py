"""Bhoomi August de-risking probe: resolve open decisions O1 and O2.

Answers:
  - Does a low-cloud Sentinel-2 scene exist over Kolkata in BOTH 2020 and 2026?
  - Which collection (sentinel-2-l2a vs sentinel-2-c1-l2a) covers both?
  - What property key carries the processing baseline (needed for PLAN.md 5.3)?
  - What are the band asset keys?
Uses stdlib only.
"""
import json
import urllib.request
import urllib.error
from collections import defaultdict

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
KOLKATA_BBOX = [88.20, 22.40, 88.50, 22.70]  # core city, ~1000 km2


def post(url, payload, timeout=60):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def search(collection, start, end, max_cloud=100, limit=200):
    return post(
        f"{EARTH_SEARCH}/search",
        {
            "collections": [collection],
            "bbox": KOLKATA_BBOX,
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": max_cloud}},
            "limit": limit,
        },
    )


print("=" * 74)
print("AVAILABLE COLLECTIONS (Sentinel-2 only)")
print("=" * 74)
cols = get(f"{EARTH_SEARCH}/collections")
s2_cols = []
for c in cols.get("collections", []):
    if "sentinel-2" in c["id"]:
        s2_cols.append(c["id"])
        interval = c.get("extent", {}).get("temporal", {}).get("interval", [[None, None]])
        print(f"  {c['id']:<24} temporal extent: {interval[0]}")

for coll in ["sentinel-2-l2a", "sentinel-2-c1-l2a"]:
    if coll not in s2_cols:
        print(f"\n!! {coll} NOT PRESENT — skipping")
        continue

    print()
    print("=" * 74)
    print(f"COLLECTION: {coll}")
    print("=" * 74)

    for year in (2020, 2026):
        try:
            res = search(coll, f"{year}-01-01", f"{year}-12-31")
        except urllib.error.HTTPError as e:
            print(f"\n  {year}: HTTP {e.code} — {e.read()[:200]}")
            continue

        feats = res.get("features", [])
        print(f"\n  {year}: {len(feats)} scenes total over Kolkata bbox")
        if not feats:
            continue

        # cloud cover distribution by month
        by_month = defaultdict(list)
        for f in feats:
            p = f["properties"]
            month = p["datetime"][:7]
            by_month[month].append(p.get("eo:cloud_cover", 999))

        print(f"    {'month':<10}{'n':>4}{'min cloud %':>14}")
        for m in sorted(by_month):
            cc = by_month[m]
            print(f"    {m:<10}{len(cc):>4}{min(cc):>14.1f}")

        # best low-cloud candidates
        good = sorted(
            [f for f in feats if f["properties"].get("eo:cloud_cover", 999) < 10],
            key=lambda f: f["properties"]["eo:cloud_cover"],
        )
        print(f"    --> {len(good)} scenes under 10% cloud")
        for f in good[:5]:
            p = f["properties"]
            print(f"        {p['datetime'][:10]}  cloud={p['eo:cloud_cover']:5.1f}%  {f['id']}")

        # inspect one item for baseline property + asset keys
        if year == 2026 or coll == "sentinel-2-l2a":
            sample = good[0] if good else feats[0]
            p = sample["properties"]
            baseline_keys = {
                k: v for k, v in p.items()
                if "baseline" in k.lower() or "offset" in k.lower() or "processing" in k.lower()
            }
            print(f"    baseline/offset properties on {sample['id']}:")
            print(f"        {baseline_keys if baseline_keys else 'NONE FOUND'}")
            if year == 2020:
                wanted = {"red", "green", "nir", "swir16", "scl", "B03", "B04", "B08", "B11", "SCL"}
                present = [a for a in sample["assets"] if a in wanted]
                print(f"    band asset keys present: {sorted(present)}")
