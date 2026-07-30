"use client";

import type { Scene } from "@/lib/api";

interface Props {
  scenes: Scene[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  areaKm2: number | null;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function SceneList({ scenes, selectedId, onSelect, areaKm2 }: Props) {
  if (scenes.length === 0) return null;

  const usable = scenes.filter((s) => s.aoi_coverage >= 0.999).length;

  return (
    <section className="panel results">
      <h2>
        {scenes.length} scene{scenes.length === 1 ? "" : "s"}
        {areaKm2 !== null && <span className="muted"> — {areaKm2} km² searched</span>}
      </h2>
      {usable < scenes.length && (
        <p className="muted small">
          {scenes.length - usable} of these only partly cover your area and cannot be
          processed as a single job. They are shown dashed on the map.
        </p>
      )}

      <ul className="scenes">
        {scenes.map((scene) => {
          const partial = scene.aoi_coverage < 0.999;
          return (
            <li
              key={scene.id}
              className={[
                "scene",
                scene.id === selectedId ? "selected" : "",
                partial ? "partial" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => onSelect(scene.id === selectedId ? null : scene.id)}
            >
              <div className="scene-head">
                <strong>{formatDate(scene.acquired_at)}</strong>
                <span className="cloud">
                  {scene.cloud_cover === null
                    ? "cloud ?"
                    : `${scene.cloud_cover.toFixed(1)}% cloud`}
                </span>
              </div>
              <div className="scene-id">{scene.id}</div>
              <div className="scene-meta">
                {partial ? (
                  <span className="warn">
                    covers {(scene.aoi_coverage * 100).toFixed(0)}% of your area
                  </span>
                ) : (
                  <span className="ok">covers your whole area</span>
                )}
                {scene.processing_baseline && (
                  <span className="muted"> · baseline {scene.processing_baseline}</span>
                )}
              </div>
              <div className="processes">
                {scene.available_processes.map((p) => (
                  <span key={p} className="chip">
                    {p.toUpperCase()}
                  </span>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
