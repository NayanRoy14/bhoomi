"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import SceneList from "@/components/SceneList";
import SearchPanel from "@/components/SearchPanel";
import { ApiError, api, type Health, type Polygon, type Scene } from "@/lib/api";

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

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  async function search() {
    if (!aoi) return;
    setSearching(true);
    setError(null);
    setSelectedId(null);
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
          onSelect={setSelectedId}
          areaKm2={areaKm2}
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
          <span className="muted">Contains modified Copernicus Sentinel data</span>
          <br />
          <span className="muted small">
            Processing arrives in January — this release finds scenes.
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
      />
    </main>
  );
}
