"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import AnalysisPanel from "@/components/AnalysisPanel";
import SceneList from "@/components/SceneList";
import SearchPanel from "@/components/SearchPanel";
import {
  ApiError,
  api,
  type Health,
  type Output,
  type Polygon,
  type Scene,
} from "@/lib/api";

// MapLibre touches `window` at import time, so it cannot be server-rendered.
const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => <div className="map-wrap map-loading">Loading map…</div>,
});

export default function Page() {
  const [aoi, setAoi] = useState<Polygon | null>(null);
  const [drawing, setDrawing] = useState(false);

  const [startDate, setStartDate] = useState("2020-02-15");
  const [endDate, setEndDate] = useState("2020-03-31");
  const [maxCloud, setMaxCloud] = useState(20);

  const [scenes, setScenes] = useState<Scene[]>([]);
  const [areaKm2, setAreaKm2] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [output, setOutput] = useState<Output | null>(null);
  //  Below 1 so the basemap shows through -- an index raster with no
  //  landmarks under it is hard to place.
  const [opacity, setOpacity] = useState(0.75);

  const selectedScene = scenes.find((s) => s.id === selectedId) ?? null;

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  async function search() {
    if (!aoi) return;
    setSearching(true);
    setError(null);
    setSelectedId(null);
    setOutput(null);
    try {
      const result = await api.searchScenes({
        aoi,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        max_cloud: maxCloud,
      });
      setScenes(result.scenes);
      setAreaKm2(result.aoi_area_km2);
      if (result.count === 0) {
        setError("No scenes matched. Try widening the dates or raising the cloud limit.");
      }
    } catch (err) {
      setScenes([]);
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setSearching(false);
    }
  }

  return (
    <main className="layout">
      <aside className="sidebar">
        <header className="brand">
          <h1>Bhoomi</h1>
          <p className="tagline">On-demand Earth Observation processing</p>
        </header>

        <SearchPanel
          aoi={aoi}
          drawing={drawing}
          onStartDrawing={() => setDrawing(true)}
          onClearAoi={() => {
            setAoi(null);
            setScenes([]);
            setAreaKm2(null);
            setSelectedId(null);
            setOutput(null);
          }}
          startDate={startDate}
          endDate={endDate}
          maxCloud={maxCloud}
          onStartDate={setStartDate}
          onEndDate={setEndDate}
          onMaxCloud={setMaxCloud}
          onSearch={search}
          searching={searching}
        />

        {error && <p className="error">{error}</p>}

        <SceneList
          scenes={scenes}
          selectedId={selectedId}
          onSelect={(id) => {
            setSelectedId(id);
            // A result belongs to the scene it came from; keeping it visible
            // after switching scenes would attach it to the wrong one.
            setOutput(null);
          }}
          areaKm2={areaKm2}
        />

        <AnalysisPanel
          aoi={aoi}
          scene={selectedScene}
          scenes={scenes}
          onOutput={setOutput}
          opacity={opacity}
          onOpacity={setOpacity}
        />

        <footer className="foot">
          {health ? (
            <span>
              API {health.version} · {health.catalogue}
            </span>
          ) : (
            <span className="warn">API unreachable</span>
          )}
          <br />
          {health?.workers !== null && health?.workers !== undefined && (
            <>
              <span className="muted">
                {health.workers} worker{health.workers === 1 ? "" : "s"}
                {health.queue_depth !== null && ` · ${health.queue_depth} queued`}
              </span>
              <br />
            </>
          )}
          <span className="muted">Contains modified Copernicus Sentinel data</span>
          <br />
          <span className="muted small">
            Results render as map tiles and download as Cloud-Optimized GeoTIFF.
          </span>
        </footer>
      </aside>

      <MapView
        aoi={aoi}
        onAoiChange={setAoi}
        drawing={drawing}
        onDrawingChange={setDrawing}
        scenes={scenes}
        selectedSceneId={selectedId}
        onSelectScene={setSelectedId}
        outputBounds={output?.bounds ?? null}
        outputTiles={output?.tiles ?? null}
        outputOpacity={opacity}
      />
    </main>
  );
}
