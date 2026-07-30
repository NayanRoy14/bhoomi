"use client";

import type { Polygon } from "@/lib/api";
import { MAX_AOI_KM2 } from "@/lib/api";
import { formatArea, polygonAreaKm2 } from "@/lib/geo";

interface Props {
  aoi: Polygon | null;
  drawing: boolean;
  onStartDrawing: () => void;
  onClearAoi: () => void;
  startDate: string;
  endDate: string;
  maxCloud: number;
  onStartDate: (value: string) => void;
  onEndDate: (value: string) => void;
  onMaxCloud: (value: number) => void;
  onSearch: () => void;
  searching: boolean;
}

export default function SearchPanel({
  aoi,
  drawing,
  onStartDrawing,
  onClearAoi,
  startDate,
  endDate,
  maxCloud,
  onStartDate,
  onEndDate,
  onMaxCloud,
  onSearch,
  searching,
}: Props) {
  const areaKm2 = aoi ? polygonAreaKm2(aoi) : 0;
  const tooLarge = areaKm2 > MAX_AOI_KM2;
  const datesReversed = Boolean(startDate && endDate && startDate > endDate);
  const canSearch = Boolean(aoi) && !tooLarge && !datesReversed && !searching;

  return (
    <section className="panel">
      <h2>Area of interest</h2>

      <div className="row">
        <button
          type="button"
          onClick={onStartDrawing}
          disabled={drawing}
          className="primary"
        >
          {aoi ? "Redraw" : "Draw polygon"}
        </button>
        <button type="button" onClick={onClearAoi} disabled={!aoi || drawing}>
          Clear
        </button>
      </div>

      {aoi && (
        <p className={tooLarge ? "area over" : "area"}>
          {formatArea(areaKm2)}
          {tooLarge && (
            <span className="warn">
              {" "}
              — over the {MAX_AOI_KM2} km² limit. Draw a smaller area.
            </span>
          )}
        </p>
      )}
      {!aoi && !drawing && (
        <p className="muted">Draw an area on the map to search for imagery.</p>
      )}
      {aoi && !drawing && !tooLarge && (
        <p className="muted small">Drag a vertex to adjust.</p>
      )}

      <h2>Dates</h2>
      <div className="row">
        <label>
          From
          <input type="date" value={startDate} onChange={(e) => onStartDate(e.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={endDate} onChange={(e) => onEndDate(e.target.value)} />
        </label>
      </div>
      {datesReversed && <p className="warn">The start date is after the end date.</p>}

      <h2>
        Cloud cover <span className="muted">— at most {maxCloud}%</span>
      </h2>
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={maxCloud}
        onChange={(e) => onMaxCloud(Number(e.target.value))}
      />

      <button
        type="button"
        className="primary search"
        onClick={onSearch}
        disabled={!canSearch}
      >
        {searching ? "Searching…" : "Search scenes"}
      </button>
    </section>
  );
}
