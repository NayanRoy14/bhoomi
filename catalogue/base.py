"""Catalogue abstraction: a Scene, and the protocol every catalogue implements.

Kept free of any web framework so it stays importable from a notebook and
testable without a server -- the same rule that governs ``processing/``.

A second implementation (Bhoonidhi) will not serve COGs over HTTP range
requests, so ``Scene.assets`` may hold local staged paths rather than URLs.
Nothing downstream should care which it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


class CatalogueError(RuntimeError):
    """A catalogue request failed or returned something unusable."""


class SceneNotFoundError(CatalogueError):
    """No scene with the requested identifier exists in this catalogue."""


@dataclass(frozen=True)
class SearchQuery:
    """A catalogue search. ``aoi`` is a GeoJSON geometry in EPSG:4326."""

    aoi: dict
    start: str | None = None          # "YYYY-MM-DD"
    end: str | None = None
    max_cloud: float | None = None
    collections: tuple[str, ...] = ("sentinel-2-l2a",)
    limit: int = 50

    def bbox(self) -> tuple[float, float, float, float]:
        """Bounding box of the AOI geometry."""
        coords = _flatten_coords(self.aoi["coordinates"])
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return min(xs), min(ys), max(xs), max(ys)

    def datetime_range(self) -> str | None:
        if not self.start and not self.end:
            return None
        start = f"{self.start}T00:00:00Z" if self.start else ".."
        end = f"{self.end}T23:59:59Z" if self.end else ".."
        return f"{start}/{end}"


def _flatten_coords(coords) -> list:
    """Yield [x, y] pairs from arbitrarily nested GeoJSON coordinate arrays."""
    if coords and isinstance(coords[0], (int, float)):
        return [coords]
    out: list = []
    for part in coords:
        out.extend(_flatten_coords(part))
    return out


@dataclass(frozen=True)
class Scene:
    """One catalogue item, carrying everything the pipeline needs.

    ``properties`` is retained verbatim because harmonisation and change
    compatibility checks read from it, and because provenance written into the
    output COG should reflect what the catalogue actually said.
    """

    id: str
    collection: str
    acquired_at: datetime
    bbox: tuple[float, float, float, float]
    geometry: dict
    assets: dict[str, str]
    properties: dict = field(default_factory=dict)
    catalogue: str = "unknown"

    @property
    def cloud_cover(self) -> float | None:
        return self.properties.get("eo:cloud_cover")

    @property
    def processing_baseline(self) -> str | None:
        return self.properties.get("s2:processing_baseline")

    @property
    def satellite(self) -> str | None:
        return self.properties.get("platform") or self.properties.get("constellation")

    def href(self, band: str) -> str:
        """Asset location for a band. Raises if this scene lacks it."""
        try:
            return self.assets[band]
        except KeyError:
            raise CatalogueError(
                f"Scene {self.id} has no asset {band!r}. Available: "
                f"{sorted(self.assets)}"
            ) from None

    def has_bands(self, bands) -> bool:
        return all(b in self.assets for b in bands)

    def aoi_coverage(self, aoi: dict) -> float:
        """Fraction of ``aoi`` that falls inside this scene's footprint.

        Below 1.0 means the AOI crosses a scene boundary. Bhoomi V1 processes
        one scene at a time (PLAN.md D3), so callers must reject those rather
        than silently returning a partial raster.
        """
        from shapely.geometry import shape

        aoi_geom = shape(aoi)
        if aoi_geom.is_empty or aoi_geom.area == 0:
            return 0.0
        return float(shape(self.geometry).intersection(aoi_geom).area / aoi_geom.area)

    @classmethod
    def from_stac_item(cls, item: dict, catalogue: str = "unknown") -> "Scene":
        props = item.get("properties", {})
        raw = props.get("datetime") or props.get("start_datetime")
        if not raw:
            raise CatalogueError(f"STAC item {item.get('id')!r} has no datetime")

        assets = {
            key: asset["href"]
            for key, asset in (item.get("assets") or {}).items()
            if isinstance(asset, dict) and "href" in asset
        }
        bbox = item.get("bbox")
        return cls(
            id=item["id"],
            collection=item.get("collection", ""),
            acquired_at=_parse_datetime(raw),
            bbox=tuple(bbox) if bbox else (0.0, 0.0, 0.0, 0.0),
            geometry=item.get("geometry") or {},
            assets=assets,
            properties=props,
            catalogue=catalogue,
        )

    def __repr__(self) -> str:
        cloud = "?" if self.cloud_cover is None else f"{self.cloud_cover:.2f}%"
        return (f"Scene({self.id}, {self.acquired_at:%Y-%m-%d}, cloud={cloud}, "
                f"baseline={self.processing_baseline})")


def _parse_datetime(raw: str) -> datetime:
    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise CatalogueError(f"Unparseable datetime {raw!r}") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@runtime_checkable
class Catalogue(Protocol):
    """What every catalogue backend must provide."""

    name: str

    def search(self, query: SearchQuery) -> list[Scene]:
        """Scenes matching the query, newest first."""

    def get(self, scene_id: str, collection: str | None = None) -> Scene:
        """One scene by identifier. Raises SceneNotFoundError if absent."""
