"use client";

/**
 * Colour ramp for the rendered raster.
 *
 * The ramps mirror `backend/tiles.py`; they are duplicated because the browser
 * cannot ask matplotlib what "rdylgn" looks like, and TiTiler's own
 * `/colorMaps` endpoint would be a second round trip to draw a strip 8 pixels
 * tall. If a ramp changes on the server it must change here -- the same
 * hand-mirroring the API client already lives with.
 *
 * The end labels are fixed at -1 and +1 rather than the output's own min and
 * max, because the tiles are rescaled to that fixed range (see tiles.py). A
 * legend showing the data's range beside an image stretched to a different one
 * would misstate every colour on the map.
 */

const RAMPS: Record<string, string[]> = {
  // Matplotlib RdYlGn, sampled. Red = no vegetation, green = dense.
  ndvi: ["#a50026", "#f46d43", "#fee08b", "#d9ef8b", "#66bd63", "#006837"],
  // RdBu. Red = dry, blue = water.
  ndwi: ["#b2182b", "#ef8a62", "#fddbc7", "#d1e5f0", "#67a9cf", "#2166ac"],
  // RdYlBu reversed. Blue = not built-up, red = built-up.
  ndbi: ["#313695", "#74add1", "#e0f3f8", "#fee090", "#f46d43", "#a50026"],
};

const MEANING: Record<string, [string, string]> = {
  ndvi: ["bare", "dense vegetation"],
  ndwi: ["dry", "water"],
  ndbi: ["not built-up", "built-up"],
};

export default function Legend({
  process,
  opacity,
  onOpacity,
}: {
  process: string;
  opacity: number;
  onOpacity: (value: number) => void;
}) {
  const ramp = RAMPS[process];
  if (!ramp) return null;
  const [low, high] = MEANING[process] ?? ["low", "high"];

  return (
    <div className="legend">
      <div
        className="ramp"
        style={{ background: `linear-gradient(to right, ${ramp.join(", ")})` }}
      />
      <div className="ramp-ends">
        <span>−1</span>
        <span className="muted small">{process.toUpperCase()}</span>
        <span>+1</span>
      </div>
      <div className="ramp-ends muted small">
        <span>{low}</span>
        <span>{high}</span>
      </div>

      <label className="field opacity">
        <span>
          Layer opacity — {Math.round(opacity * 100)}%
        </span>
        <input
          type="range"
          min={0}
          max={100}
          value={Math.round(opacity * 100)}
          onChange={(e) => onOpacity(Number(e.target.value) / 100)}
        />
      </label>
    </div>
  );
}
