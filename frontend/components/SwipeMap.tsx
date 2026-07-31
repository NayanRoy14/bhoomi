"use client";

/**
 * Before/after swipe for a change result (PLAN.md 11, February exit criterion).
 *
 * Two MapLibre instances stacked, cameras synchronised, the top one clipped by
 * CSS to the left of a draggable handle. MapLibre has no way to clip one layer
 * against a screen-space line -- `raster-opacity` cross-fades rather than
 * wipes, and a cross-fade of two similar rasters shows nothing. So the divide
 * has to happen outside the canvas, which means two canvases.
 *
 * The cost is a second set of basemap tiles. Accepted because the alternative
 * is not a worse swipe, it is no swipe.
 *
 * Why this exists at all: a difference raster cannot be un-differenced. +0.3
 * could be bare ground becoming scrub or forest becoming denser forest, and
 * only seeing both dates distinguishes them. The change raster answers "how
 * much"; this answers "from what".
 */

import maplibregl, { Map as MapLibreMap } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";

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

const RASTER_ID = "swipe-raster";

interface Props {
  /** XYZ template for the earlier date. Shown on the left. */
  earlierTiles: string;
  /** XYZ template for the later date. Shown on the right. */
  laterTiles: string;
  earlierLabel: string;
  laterLabel: string;
  /** [west, south, east, north] shared by both rasters. */
  bounds: number[];
  opacity: number;
}

export default function SwipeMap({
  earlierTiles,
  laterTiles,
  earlierLabel,
  laterLabel,
  bounds,
  opacity,
}: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const leftBox = useRef<HTMLDivElement>(null);
  const rightBox = useRef<HTMLDivElement>(null);
  const left = useRef<MapLibreMap | null>(null);
  const right = useRef<MapLibreMap | null>(null);

  const [position, setPosition] = useState(50);
  const dragging = useRef(false);

  /* ------------------------------------------------------------- the maps */

  useEffect(() => {
    if (!leftBox.current || !rightBox.current || left.current) return;

    const fit =
      bounds.length === 4
        ? new maplibregl.LngLatBounds(
            [bounds[0], bounds[1]],
            [bounds[2], bounds[3]],
          )
        : undefined;

    const make = (container: HTMLDivElement, tiles: string, controls: boolean) => {
      const m = new maplibregl.Map({
        container,
        style: STYLE,
        center: fit ? fit.getCenter() : [88.43, 22.62],
        zoom: 11,
        // Only one map takes input. The other is a follower, so letting it
        // handle gestures too would mean two cameras fighting over one drag.
        interactive: controls,
        attributionControl: controls ? undefined : false,
      });
      if (controls) {
        m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
        m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
      }
      m.on("load", () => {
        m.addSource(RASTER_ID, {
          type: "raster",
          tiles: [tiles],
          tileSize: 256,
          bounds: bounds.length === 4
            ? (bounds as [number, number, number, number])
            : undefined,
        });
        m.addLayer({
          id: RASTER_ID,
          type: "raster",
          source: RASTER_ID,
          paint: {
            "raster-opacity": opacity,
            // Nearest, as everywhere else: these are measured 10 m values and
            // smoothing invents readings that were never taken.
            "raster-resampling": "nearest",
          },
        });
        if (fit) m.fitBounds(fit, { padding: 40, animate: false });
      });
      return m;
    };

    // The RIGHT map is the interactive one. The left is clipped to a sliver at
    // the default position, so putting the controls there would hide them
    // behind the handle at position 0.
    right.current = make(rightBox.current, laterTiles, true);
    left.current = make(leftBox.current, earlierTiles, false);

    // One-way sync: the follower copies the driver after every camera change.
    // `jumpTo` rather than an animated move, so the two never disagree mid-flight.
    const follow = () => {
      const driver = right.current;
      const follower = left.current;
      if (!driver || !follower) return;
      follower.jumpTo({
        center: driver.getCenter(),
        zoom: driver.getZoom(),
        bearing: driver.getBearing(),
        pitch: driver.getPitch(),
      });
    };
    right.current.on("move", follow);
    right.current.on("moveend", follow);

    return () => {
      left.current?.remove();
      right.current?.remove();
      left.current = null;
      right.current = null;
    };
    // Rebuilt when the rasters change: a raster source's tile template is
    // fixed at creation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [earlierTiles, laterTiles]);

  // Opacity adjusts the existing layers rather than rebuilding them, so
  // dragging the slider does not refetch every tile.
  useEffect(() => {
    for (const m of [left.current, right.current]) {
      if (m?.getLayer(RASTER_ID)) {
        m.setPaintProperty(RASTER_ID, "raster-opacity", opacity);
      }
    }
  }, [opacity]);

  /* ----------------------------------------------------------- the handle */

  const moveTo = useCallback((clientX: number) => {
    const box = wrap.current?.getBoundingClientRect();
    if (!box || box.width === 0) return;
    const pct = ((clientX - box.left) / box.width) * 100;
    setPosition(Math.min(100, Math.max(0, pct)));
  }, []);

  useEffect(() => {
    const move = (e: MouseEvent) => dragging.current && moveTo(e.clientX);
    const touch = (e: TouchEvent) => {
      if (dragging.current && e.touches[0]) moveTo(e.touches[0].clientX);
    };
    const up = () => {
      dragging.current = false;
    };
    // On window, not the handle: a fast drag outruns the cursor and would
    // otherwise drop the gesture the moment it left a 12-pixel target.
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    window.addEventListener("touchmove", touch);
    window.addEventListener("touchend", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      window.removeEventListener("touchmove", touch);
      window.removeEventListener("touchend", up);
    };
  }, [moveTo]);

  return (
    <div className="map-wrap swipe" ref={wrap}>
      <div ref={rightBox} className="map" />
      <div
        ref={leftBox}
        className="map swipe-left"
        style={{ clipPath: `inset(0 ${100 - position}% 0 0)` }}
      />

      <div
        className="swipe-handle"
        style={{ left: `${position}%` }}
        role="slider"
        tabIndex={0}
        aria-label="Comparison position"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(position)}
        aria-valuetext={`${Math.round(position)}% ${earlierLabel}`}
        onMouseDown={() => {
          dragging.current = true;
        }}
        onTouchStart={() => {
          dragging.current = true;
        }}
        onKeyDown={(e) => {
          // Keyboard-operable, because a drag-only control is unusable to
          // anyone who cannot drag.
          const step = e.shiftKey ? 10 : 2;
          if (e.key === "ArrowLeft") setPosition((p) => Math.max(0, p - step));
          if (e.key === "ArrowRight") setPosition((p) => Math.min(100, p + step));
          if (e.key === "Home") setPosition(0);
          if (e.key === "End") setPosition(100);
        }}
      >
        <span className="swipe-grip" aria-hidden="true" />
      </div>

      <div className="swipe-label left" style={{ opacity: position > 12 ? 1 : 0 }}>
        {earlierLabel}
      </div>
      <div className="swipe-label right" style={{ opacity: position < 88 ? 1 : 0 }}>
        {laterLabel}
      </div>
    </div>
  );
}
