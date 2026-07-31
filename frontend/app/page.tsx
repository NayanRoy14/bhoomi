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

const SwipeMap = dynamic(() => import("@/components/SwipeMap"), {
  ssr: false,
  loading: () => <div className="map-wrap map-loading">Loading comparison…</div>,
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
  // Every output of the last job, primary first. A change job publishes three
  // rasters: the difference, and the two dates it was taken between.
  const [outputs, setOutputs] = useState<Output[]>([]);
  const [swipeDates, setSwipeDates] = useState<[string, string] | null>(null);
  const [swiping, setSwiping] = useState(false);
  //  Below 1 so the basemap shows through -- an index raster with no
  //  landmarks under it is hard to place.
  const [opacity, setOpacity] = useState(0.75);

  const selectedScene = scenes.find((s) => s.id === selectedId) ?? null;
  const output = outputs[0] ?? null;

  // The swipe needs both dates *and* a tile server. Without TiTiler there are
  // no tiles to compare, and offering the control anyway would be a button
  // that does nothing.
  const earlier = outputs.find((o) => o.type.startsWith("earlier_")) ?? null;
  const later = outputs.find((o) => o.type.startsWith("later_")) ?? null;
  const canSwipe = earlier?.tiles != null && later?.tiles != null;

  // A new job's outputs replace the old ones; a swipe left open over rasters
  // that no longer exist would show two dead tile sources.
  useEffect(() => {
    if (!canSwipe) setSwiping(false);
  }, [canSwipe]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  async function search() {
    if (!aoi) return;
    setSearching(true);
    setError(null);
    setSelectedId(null);
    setOutputs([]);
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
            setOutputs([]);
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
            setOutputs([]);
          }}
          areaKm2={areaKm2}
        />

        <AnalysisPanel
          aoi={aoi}
          scene={selectedScene}
          scenes={scenes}
          searchStart={startDate}
          searchEnd={endDate}
          onOutput={(next, dates) => {
            setOutputs(next);
            setSwipeDates(dates ?? null);
          }}
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

      <div className="stage">
        {swiping && canSwipe ? (
          <SwipeMap
            earlierTiles={earlier!.tiles!}
            laterTiles={later!.tiles!}
            earlierLabel={sideLabel(earlier!.type, swipeDates?.[0])}
            laterLabel={sideLabel(later!.type, swipeDates?.[1])}
            bounds={earlier!.bounds}
            opacity={opacity}
          />
        ) : (
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
        )}

        {canSwipe && (
          <button
            className="stage-toggle"
            onClick={() => setSwiping((on) => !on)}
            title={
              swiping
                ? "Back to the difference raster and the AOI tools"
                : "Compare the two dates side by side"
            }
          >
            {swiping ? "Show the difference" : "Compare the two dates"}
          </button>
        )}
      </div>
    </main>
  );
}

/**
 * A label for one side of the swipe: the acquisition date and the index.
 *
 * The date is passed in rather than read off the output, because output rows
 * describe rasters and do not know when they were taken. When it is missing --
 * a result restored without the job that made it -- this falls back to
 * "Earlier"/"Later", which is still unambiguous beside its opposite. Better a
 * correct side than a date guessed from the wrong field.
 */
function sideLabel(type: string, date: string | undefined): string {
  const [side, ...rest] = type.split("_");
  const index = rest.join("_").toUpperCase();
  const when = date ?? (side === "earlier" ? "Earlier" : "Later");
  return index ? `${when} · ${index}` : when;
}
