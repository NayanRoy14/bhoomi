"""Verify the SCL (Scene Classification Layer) class table against real pixels.

PLAN.md 5.2 lists which SCL classes must be masked before computing an index.
That table came from documentation. This checks it against the actual demo AOI,
where the ground truth is locally knowable: New Town/Rajarhat is built-up, and
the East Kolkata Wetlands sit in the south-west of the AOI as open water.
"""
import os

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
os.environ["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] = ".tif"

AOI = (88.35, 22.55, 88.52, 22.68)
BASE = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/45/Q/XF"
SCENES = {
    "2020": f"{BASE}/2020/3/S2A_45QXF_20200310_1_L2A/SCL.tif",
    "2026": f"{BASE}/2026/3/S2B_45QXF_20260304_0_L2A/SCL.tif",
}

NAMES = {
    0: "no data", 1: "saturated/defective", 2: "dark area / cast shadow",
    3: "cloud shadow", 4: "vegetation", 5: "not vegetated (bare/built)",
    6: "water", 7: "unclassified", 8: "cloud medium prob",
    9: "cloud high prob", 10: "thin cirrus", 11: "snow / ice",
}
MASK_PER_PLAN = {0, 1, 2, 3, 8, 9, 10, 11}

for label, url in SCENES.items():
    with rasterio.open(url) as src:
        b = transform_bounds("EPSG:4326", src.crs, *AOI)
        scl = src.read(1, window=from_bounds(*b, transform=src.transform))
        out = rf"D:\Bhoomi\data\{label}_scl.tif"
        prof = src.profile.copy()
        prof.update(driver="GTiff", height=scl.shape[0], width=scl.shape[1],
                    transform=src.window_transform(from_bounds(*b, transform=src.transform)),
                    compress="deflate")
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(scl, 1)

    total = scl.size
    print(f"\n=== {label}  ({scl.shape[1]}x{scl.shape[0]} px @ 20 m) ===")
    print(f"  {'val':>3}  {'class':<28}{'pixels':>10}{'share':>9}   masked?")
    seen = sorted(np.unique(scl).tolist())
    for v in seen:
        n = int((scl == v).sum())
        flag = "MASK" if v in MASK_PER_PLAN else "keep"
        print(f"  {v:>3}  {NAMES.get(v, '??'):<28}{n:>10,}{n / total * 100:8.2f}%   {flag}")

    masked = np.isin(scl, list(MASK_PER_PLAN))
    print(f"  --> masking removes {masked.sum() / total * 100:.2f}% of the AOI")
    unexpected = [v for v in seen if v not in NAMES]
    if unexpected:
        print(f"  !! UNEXPECTED CLASS VALUES: {unexpected}")
