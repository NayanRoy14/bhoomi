"""Execute a Bhoomi process through OGC API - Processes. No website involved.

This is PLAN.md 7.6's acceptance test, written out: *"a QGIS user, or a Python
script using owslib or plain requests, executes an NDVI process and loads the
result -- without opening the website."*

Deliberately written against the **standard**, not against Bhoomi. Nothing here
knows a Bhoomi URL beyond the base: the processes are discovered, the input
schema is read from the process description, the job is polled through the
`self` link and the raster is fetched from the `results` document. Point it at
any OGC API - Processes server offering an `ndvi` process and it should work --
which is the entire claim the standard is here to support.

Uses only the standard library, so it runs on a clean machine with no pip
install: `urllib` rather than `requests`, and no owslib.

    python examples/ogc_client.py
    python examples/ogc_client.py --base http://localhost:8000 --process ndvi
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

#: New Town / Rajarhat, Kolkata -- D13. Small enough to finish quickly.
AOI = {
    "type": "Polygon",
    "coordinates": [[[88.44, 22.59], [88.49, 22.59],
                     [88.49, 22.63], [88.44, 22.63], [88.44, 22.59]]],
}

TERMINAL = {"successful", "failed", "dismissed"}


def get(url: str, attempts: int = 5) -> dict:
    """GET JSON, honouring 429 Retry-After rather than giving up on it.

    PLAN.md 8 rate-limits polling. A client that treats 429 as fatal is a
    badly-behaved client: the header says exactly how long to wait, and the
    whole point of sending it is that the caller should.
    """
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            wait = int(exc.headers.get("Retry-After") or 5)
            print(f"   rate limited; waiting {wait}s as asked")
            time.sleep(wait)
    raise SystemExit("unreachable")


def post(url: str, body: dict) -> tuple[int, "urllib.request.email.message.Message", dict]:
    """POST JSON, returning (status, headers, parsed body).

    The headers are returned as the `HTTPMessage` itself rather than as a
    `dict`. HTTP header names are case-insensitive and uvicorn emits them
    lowercase, so `dict(response.headers)["Location"]` raises KeyError against
    a response that does carry a Location. `HTTPMessage.get` is
    case-insensitive; a dict is not.
    """
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 # Part 1 Core: this is how a client asks for async execution.
                 "Prefer": "respond-async"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.headers, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, json.loads(exc.read())


def link_map(document_url: str, document: dict) -> dict[str, str]:
    """Index a document's links by relation, with hrefs made absolute.

    Relative hrefs are legal, so they are resolved against the document that
    carried them rather than against the base -- those differ the moment an API
    is mounted under a prefix, which this one is.
    """
    return {
        link["rel"]: urllib.parse.urljoin(document_url, link["href"])
        for link in document.get("links", [])
    }


def follow(links: dict[str, str], name: str) -> str:
    """Resolve one link relation by its short name.

    OGC relations are full URIs -- `http://www.opengis.net/def/rel/ogc/1.0/processes`
    -- so matching on the last segment finds the right one without hardcoding
    the whole IANA-style URI, and still works if a server uses the bare name.
    """
    for rel, href in links.items():
        if rel == name or rel.rstrip("/").endswith(f"/{name}"):
            return href
    raise SystemExit(
        f"landing page offers no {name!r} link; it advertised: {sorted(links)}")


def find_scene(base: str, aoi: dict, start: str, end: str) -> str:
    """The one Bhoomi-specific call: scene discovery is not part of Processes.

    OGC API - Processes describes *computation*, not catalogues. Finding a
    scene id is OGC API - Records or STAC territory; Bhoomi exposes STAC-backed
    search on its native route, so this step uses that and says so plainly
    rather than pretending the standard covers it.
    """
    status, _, body = post(f"{base}/api/v1/scenes/search", {
        "aoi": aoi, "start_date": start, "end_date": end, "max_cloud": 20,
    })
    if status != 200:
        raise SystemExit(f"scene search failed: {body}")
    usable = [s for s in body["scenes"] if s["aoi_coverage"] >= 0.999]
    if not usable:
        raise SystemExit("no scene fully covers the AOI in that window")
    return usable[0]["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--process", default="ndvi")
    parser.add_argument("--start", default="2026-02-15")
    parser.add_argument("--end", default="2026-03-31")
    parser.add_argument("--out", default="ogc_result.tif")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    print("0. landing page")
    # The only URL this script constructs. Everything after it comes out of a
    # link relation, which is the difference between a client that speaks the
    # standard and one that has memorised a particular server's paths. It also
    # catches a class of bug hardcoding hides: the conformance declaration was
    # once linked from here but served somewhere else entirely.
    landing = get(f"{base}/ogc")
    links = link_map(f"{base}/ogc", landing)
    print(f"   {landing['title']}")

    print("1. conformance")
    classes = get(follow(links, "conformance"))["conformsTo"]
    core = [c for c in classes if c.endswith("processes-1/1.0/conf/core")]
    if not core:
        raise SystemExit("server does not declare OGC API - Processes Core")
    print(f"   declares {len(classes)} classes, including Processes Core")

    print("2. processes offered")
    offered = get(follow(links, "processes"))["processes"]
    print("   " + ", ".join(p["id"] for p in offered))
    if args.process not in {p["id"] for p in offered}:
        raise SystemExit(f"{args.process!r} is not offered")

    print(f"3. description of {args.process!r}")
    described = get(f"{base}/ogc/processes/{args.process}")
    needed = [n for n, i in described["inputs"].items() if i.get("minOccurs", 1) >= 1]
    count = described["inputs"]["scene_ids"]["schema"]["minItems"]
    print(f"   required inputs: {', '.join(needed)}")
    print(f"   scenes required: {count}")
    print(f"   execution: {', '.join(described['jobControlOptions'])}")

    print("4. finding a scene (STAC search -- not part of Processes)")
    scene_ids = [find_scene(base, AOI, args.start, args.end)]
    if count == 2:
        raise SystemExit("this example runs a single-scene process; try --process ndvi")
    print(f"   {scene_ids[0]}")

    print("5. execute")
    status, headers, body = post(
        f"{base}/ogc/processes/{args.process}/execution",
        {"inputs": {"aoi": AOI, "scene_ids": scene_ids}})
    if status != 201:
        raise SystemExit(f"execution refused ({status}): "
                         f"{body.get('detail', body)}")
    location = headers.get("Location")
    if not location:
        raise SystemExit("201 without a Location header; the standard requires one")
    job_url = f"{base}{location}"
    print(f"   201 Created -> {location}")
    if headers.get("Preference-Applied"):
        print(f"   Preference-Applied: {headers.get('Preference-Applied')}")

    print("6. poll")
    deadline = time.time() + 15 * 60
    state = None
    while time.time() < deadline:
        info = get(job_url)
        if info["status"] != state:
            state = info["status"]
            print(f"   {state:11} {info.get('progress', 0):3}%  {info.get('message', '')}")
        if state in TERMINAL:
            break
        # 5 s, not 1: PLAN.md 8 budgets 1200 polls an hour and a job takes
        # tens of seconds. Polling faster buys nothing and spends the budget.
        time.sleep(5)
    else:
        raise SystemExit("gave up waiting")

    if state != "successful":
        raise SystemExit(f"job {state}: {info.get('message')}")

    print("7. results")
    results = get(f"{job_url}/results")
    for name, value in results.items():
        print(f"   {name}: {value['type']}")
        print(f"     {value['href']}")

    first = next(iter(results.values()))
    print(f"8. fetching {first['href']}")
    with urllib.request.urlopen(first["href"], timeout=300) as response:
        data = response.read()
    with open(args.out, "wb") as handle:
        handle.write(data)
    print(f"   wrote {args.out} ({len(data):,} bytes)")

    # A GeoTIFF starts with "II" or "MM" and the magic number 42. Checked
    # rather than trusted: a saved JSON error page would also be "bytes".
    if data[:2] not in (b"II", b"MM"):
        raise SystemExit("that is not a TIFF")
    print("   verified: TIFF byte-order marker present")
    print("\nOpen it in QGIS: Layer > Add Layer > Add Raster Layer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
