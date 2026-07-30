"""Render the Kolkata NDVI change result to PNG.

Not part of the pipeline -- Bhoomi serves tiles via TiTiler. This exists so the
result can be inspected without opening QGIS, and so the flagship figure can go
straight into the README and the report.
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import TwoSlopeNorm

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OUT = ROOT / "outputs"


def load(name: str) -> np.ndarray:
    with rasterio.open(OUT / f"{name}.tif") as src:
        a = src.read(1).astype("float32")
        nod = src.nodata
    if nod is not None:
        a[a == nod] = np.nan
    return a


ndvi_2020, ndvi_2026, diff = load("ndvi_2020"), load("ndvi_2026"), load("ndvi_change")

fig, axes = plt.subplots(1, 3, figsize=(19, 7))
fig.patch.set_facecolor("white")

for ax, data, title in [
    (axes[0], ndvi_2020, "NDVI  2020-03-10"),
    (axes[1], ndvi_2026, "NDVI  2026-03-04"),
]:
    im = ax.imshow(data, cmap="RdYlGn", vmin=-0.2, vmax=0.8, interpolation="nearest")
    ax.set_title(title, fontsize=13)
    ax.axis("off")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.03)

im = axes[2].imshow(diff, cmap="RdBu", norm=TwoSlopeNorm(vmin=-0.6, vcenter=0, vmax=0.6),
                    interpolation="nearest")
axes[2].set_title("NDVI change  (red = vegetation lost)", fontsize=13)
axes[2].axis("off")
plt.colorbar(im, ax=axes[2], fraction=0.046, pad=0.03)

fig.suptitle("Bhoomi - New Town / Rajarhat, Kolkata - Sentinel-2 L2A",
             fontsize=15, y=0.97)
fig.text(0.5, 0.02, "Contains modified Copernicus Sentinel data",
         ha="center", fontsize=9, color="#555")
plt.tight_layout(rect=(0, 0.03, 1, 0.95))
plt.savefig(OUT / "kolkata_change.png", dpi=110, facecolor="white")
print(f"wrote OUT / 'kolkata_change.png'")

# Standalone change map, larger, for close inspection.
fig, ax = plt.subplots(figsize=(11, 10))
fig.patch.set_facecolor("white")
im = ax.imshow(diff, cmap="RdBu", norm=TwoSlopeNorm(vmin=-0.6, vcenter=0, vmax=0.6),
               interpolation="nearest")
ax.set_title("NDVI change 2020 to 2026 - New Town / Rajarhat, Kolkata", fontsize=13)
ax.axis("off")
plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02, label="NDVI difference")
plt.tight_layout()
plt.savefig(OUT / "ndvi_change_large.png", dpi=115, facecolor="white")
print(f"wrote OUT / 'ndvi_change_large.png'")

# Where is the loss concentrated? Threshold map.
loss = np.where(np.isnan(diff), np.nan, (diff < -0.2).astype(float))
fig, ax = plt.subplots(figsize=(11, 10))
fig.patch.set_facecolor("white")
ax.imshow(loss, cmap="Reds", vmin=0, vmax=1, interpolation="nearest")
ax.set_title("Pixels that lost more than 0.2 NDVI (8.77% of the AOI)", fontsize=13)
ax.axis("off")
plt.tight_layout()
plt.savefig(OUT / "ndvi_loss_mask.png", dpi=115, facecolor="white")
print(f"wrote OUT / 'ndvi_loss_mask.png'")
