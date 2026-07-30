"use client";

/**
 * MapLibre map with AOI drawing and scene footprints.
 *
 * The polygon draw is implemented directly against MapLibre rather than pulling
 * in a draw library. It is about 150 lines, has no version-coupling risk with
 * MapLibre releases, and V1 needs exactly one polygon -- PLAN.md 28 says spend
 * the effort on the pipeline, not the UI.
 */

import maplibregl, { Map as MapLibreMap, MapMouseEvent } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Polygon, Scene } from "@/lib/api";
import { toPolygon } from "@/lib/geo";

const KOLKATA: [number, number] = [88.43, 22.62];

/** OpenStreetMap raster. Fine for development; a production deploy should use
 *  its own tile source rather than leaning on the OSM Foundation's servers. */
const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

interface Props {
  aoi: Polygon | null;
  onAoiChange: (aoi: Polygon | null) => void;
  drawing: boolean;
  onDrawingChange: (drawing: boolean) => void;
  scenes: Scene[];
  selectedSceneId: string | null;
  onSelectScene: (id: string | null) => void;
}

export default function MapView({
  aoi,
  onAoiChange,
  drawing,
  onDrawingChange,
  scenes,
  selectedSceneId,
  onSelectScene,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);

  // Draft vertices while drawing, plus a rubber-band point following the cursor.
  const draft = useRef<number[][]>([]);
  const [cursor, setCursor] = useState<number[] | null>(null);
  const draggingVertex = useRef<number | null>(null);

  // Callbacks change identity every render; keep the latest in a ref so the
  // map event handlers can be attached exactly once.
  const handlers = useRef({ onAoiChange, onDrawingChange, onSelectScene, drawing });
  handlers.current = { onAoiChange, onDrawingChange, onSelectScene, drawing };

  /* ---------------------------------------------------------------- setup */

  useEffect(() => {
    if (!container.current || map.current) return;

    const m = new maplibregl.Map({
      container: container.current,
      style: STYLE,
      center: KOLKATA,
      zoom: 10,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    m.on("load", () => {
      m.addSource("footprints", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "footprint-fill",
        type: "fill",
        source: "footprints",
        paint: {
          "fill-color": ["case", ["get", "selected"], "#2563eb", "#94a3b8"],
          "fill-opacity": ["case", ["get", "selected"], 0.18, 0.05],
        },
      });
      m.addLayer({
        id: "footprint-line",
        type: "line",
        source: "footprints",
        paint: {
          "line-color": ["case", ["get", "selected"], "#2563eb", "#64748b"],
          "line-width": ["case", ["get", "selected"], 2.5, 1],
          "line-dasharray": ["case", ["get", "partial"], ["literal", [2, 2]], ["literal", [1]]],
        },
      });

      m.addSource("aoi", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "aoi-fill",
        type: "fill",
        source: "aoi",
        paint: { "fill-color": "#16a34a", "fill-opacity": 0.15 },
      });
      m.addLayer({
        id: "aoi-line",
        type: "line",
        source: "aoi",
        paint: { "line-color": "#16a34a", "line-width": 2.5 },
      });

      m.addSource("aoi-vertices", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "aoi-vertices",
        type: "circle",
        source: "aoi-vertices",
        paint: {
          "circle-radius": 5,
          "circle-color": "#ffffff",
          "circle-stroke-color": "#16a34a",
          "circle-stroke-width": 2,
        },
      });

      setReady(true);
    });

    m.on("click", (event: MapMouseEvent) => {
      if (!handlers.current.drawing) {
        const hit = m.queryRenderedFeatures(event.point, { layers: ["footprint-fill"] });
        handlers.current.onSelectScene(hit.length ? (hit[0].properties?.id ?? null) : null);
        return;
      }
      draft.current = [...draft.current, [event.lngLat.lng, event.lngLat.lat]];
      setCursor([event.lngLat.lng, event.lngLat.lat]);
    });

    m.on("dblclick", (event) => {
      if (!handlers.current.drawing) return;
      event.preventDefault();
      finish();
    });

    m.on("mousemove", (event: MapMouseEvent) => {
      if (handlers.current.drawing && draft.current.length > 0) {
        setCursor([event.lngLat.lng, event.lngLat.lat]);
      }
    });

    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* -------------------------------------------------------------- drawing */

  const finish = useCallback(() => {
    if (draft.current.length >= 3) {
      handlers.current.onAoiChange(toPolygon(draft.current));
    }
    draft.current = [];
    setCursor(null);
    handlers.current.onDrawingChange(false);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!drawing) return;
      if (event.key === "Enter") finish();
      if (event.key === "Escape") {
        draft.current = [];
        setCursor(null);
        onDrawingChange(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawing, finish, onDrawingChange]);

  // Reset the draft whenever drawing starts.
  useEffect(() => {
    if (drawing) {
      draft.current = [];
      setCursor(null);
    }
    if (map.current) {
      map.current.getCanvas().style.cursor = drawing ? "crosshair" : "";
      // Double-click zoom would fire while closing the polygon.
      if (drawing) map.current.doubleClickZoom.disable();
      else map.current.doubleClickZoom.enable();
    }
  }, [drawing]);

  /* ------------------------------------------------- vertex drag to edit */

  useEffect(() => {
    const m = map.current;
    if (!m || !ready || drawing) return;

    const down = (event: maplibregl.MapMouseEvent) => {
      const hit = m.queryRenderedFeatures(event.point, { layers: ["aoi-vertices"] });
      if (!hit.length) return;
      event.preventDefault();
      draggingVertex.current = hit[0].properties?.index ?? null;
      m.dragPan.disable();
    };
    const move = (event: maplibregl.MapMouseEvent) => {
      const index = draggingVertex.current;
      if (index === null || !aoi) return;
      const ring = [...aoi.coordinates[0]];
      ring[index] = [event.lngLat.lng, event.lngLat.lat];
      if (index === 0) ring[ring.length - 1] = ring[0];
      onAoiChange({ type: "Polygon", coordinates: [ring] });
    };
    const up = () => {
      draggingVertex.current = null;
      m.dragPan.enable();
    };

    m.on("mousedown", down);
    m.on("mousemove", move);
    m.on("mouseup", up);
    return () => {
      m.off("mousedown", down);
      m.off("mousemove", move);
      m.off("mouseup", up);
    };
  }, [ready, drawing, aoi, onAoiChange]);

  /* ------------------------------------------------------------- rendering */

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;

    const live = drawing && draft.current.length > 0
      ? toPolygon(cursor ? [...draft.current, cursor] : draft.current)
      : aoi;

    (m.getSource("aoi") as maplibregl.GeoJSONSource)?.setData(
      live ? { type: "Feature", geometry: live, properties: {} } : EMPTY,
    );

    const ring = aoi && !drawing ? aoi.coordinates[0].slice(0, -1) : [];
    (m.getSource("aoi-vertices") as maplibregl.GeoJSONSource)?.setData({
      type: "FeatureCollection",
      features: ring.map((point, index) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: point },
        properties: { index },
      })),
    });
  }, [aoi, ready, drawing, cursor]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    (m.getSource("footprints") as maplibregl.GeoJSONSource)?.setData({
      type: "FeatureCollection",
      features: scenes.map((scene) => ({
        type: "Feature" as const,
        geometry: scene.geometry,
        properties: {
          id: scene.id,
          selected: scene.id === selectedSceneId,
          partial: scene.aoi_coverage < 0.999,
        },
      })),
    });
  }, [scenes, selectedSceneId, ready]);

  return (
    <div className="map-wrap">
      <div ref={container} className="map" />
      {drawing && (
        <div className="map-hint">
          Click to add points · double-click or <kbd>Enter</kbd> to finish ·{" "}
          <kbd>Esc</kbd> to cancel
        </div>
      )}
    </div>
  );
}
