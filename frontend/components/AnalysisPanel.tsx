"use client";

/**
 * Pick an analysis, submit it, watch it run (PLAN.md 7.3-7.5).
 *
 * Polls at 2 s while the job is active, which is what 7.4 specifies. Polling
 * rather than SSE because the plan calls SSE a nice-to-have, not P0, and a
 * poll is one line of code against a status endpoint that has to exist anyway.
 *
 * The panel deliberately shows what the server said rather than a spinner and
 * a guess: the stage name, the percentage, and -- when it fails -- the API's
 * own message. Those messages are written to be actionable ("this AOI is only
 * 38% inside scene X"), so replacing them with "Processing failed" would throw
 * away the most useful thing the backend produces.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import Legend from "@/components/Legend";
import {
  ApiError,
  api,
  isTerminal,
  type Job,
  type Output,
  type Polygon,
  type Scene,
} from "@/lib/api";

/** 7.4: "Frontend polls at 2 s while active." */
const POLL_MS = 2000;

const DESCRIPTIONS: Record<string, string> = {
  ndvi: "Vegetation. (NIR − Red) / (NIR + Red), 10 m.",
  ndwi: "Water. (Green − NIR) / (Green + NIR), 10 m.",
  ndbi: "Built-up. (SWIR − NIR) / (SWIR + NIR), 20 m.",
  fake: "Queue check. Sleeps ten seconds and produces no raster.",
};

interface Props {
  aoi: Polygon | null;
  scene: Scene | null;
  onOutput: (output: Output | null) => void;
  opacity: number;
  onOpacity: (value: number) => void;
}

export default function AnalysisPanel({
  aoi,
  scene,
  onOutput,
  opacity,
  onOpacity,
}: Props) {
  const [process, setProcess] = useState<string>("ndvi");
  const [job, setJob] = useState<Job | null>(null);
  const [output, setOutput] = useState<Output | null>(null);
  const [estimate, setEstimate] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // The interval must not outlive the component or the job; a stray poll after
  // unmount sets state on a dead tree, and after completion it is pure noise.
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopPolling = useCallback(() => {
    if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, []);
  useEffect(() => stopPolling, [stopPolling]);

  const available = scene?.available_processes ?? [];
  const partial = scene !== null && scene.aoi_coverage < 0.999;
  const canSubmit =
    aoi !== null && scene !== null && !partial && !submitting && !isRunning(job);

  // A scene that cannot run the selected process should not leave a stale
  // selection in the dropdown -- the submit would fail server-side for a
  // reason the user never chose.
  useEffect(() => {
    if (available.length > 0 && !available.includes(process)) {
      setProcess(available[0]);
    }
  }, [available, process]);

  function reset() {
    stopPolling();
    setJob(null);
    setOutput(null);
    setEstimate(null);
    setError(null);
    onOutput(null);
  }

  async function collect(id: string) {
    try {
      const result = await api.getJobResult(id);
      const first = result.outputs[0] ?? null;
      setOutput(first);
      onOutput(first);
      if (!first) {
        setError(
          "The job completed but produced no raster. That is expected for the " +
            "queue-check process.",
        );
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not fetch the result.");
    }
  }

  async function submit() {
    if (!aoi || !scene) return;
    reset();
    setSubmitting(true);
    try {
      const created = await api.createJob({
        process,
        scene_ids: [scene.id],
        aoi,
      });
      setEstimate(created.estimated_seconds);
      setJob({
        job_id: created.job_id,
        process,
        status: "queued",
        progress: 0,
        message: "Waiting for a worker",
        error_message: null,
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
      });

      timer.current = setInterval(async () => {
        try {
          const latest = await api.getJob(created.job_id);
          setJob(latest);
          if (isTerminal(latest.status)) {
            stopPolling();
            if (latest.status === "completed") {
              await collect(created.job_id);
            } else {
              setError(latest.error_message ?? `Job ${latest.status.replace("_", " ")}.`);
            }
          }
        } catch (err) {
          // A transient poll failure should not kill the run -- the job is
          // still going on the server. Only stop if the job itself is gone.
          if (err instanceof ApiError && err.status === 404) {
            stopPolling();
            setError(err.message);
          }
        }
      }, POLL_MS);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not submit the job.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!scene) return null;

  return (
    <section className="panel analysis">
      <h2>Analyse</h2>

      <label className="field">
        <span>Process</span>
        <select
          value={process}
          onChange={(e) => setProcess(e.target.value)}
          disabled={isRunning(job)}
        >
          {available.map((name) => (
            <option key={name} value={name}>
              {name.toUpperCase()}
            </option>
          ))}
        </select>
      </label>
      <p className="muted small">{DESCRIPTIONS[process] ?? ""}</p>

      {partial ? (
        <p className="warn small">
          This scene covers only {(scene.aoi_coverage * 100).toFixed(0)}% of your area.
          Bhoomi processes one scene at a time — pick a scene that fully contains it,
          or draw a smaller area.
        </p>
      ) : (
        <button className="primary" onClick={submit} disabled={!canSubmit}>
          {submitting ? "Submitting…" : isRunning(job) ? "Running…" : `Run ${process.toUpperCase()}`}
        </button>
      )}

      {job && (
        <div className="job">
          <div className="job-head">
            <span className={`status ${job.status}`}>{job.message}</span>
            <span className="muted small">{job.progress}%</span>
          </div>
          <div className="bar">
            <div
              className={`bar-fill ${job.status}`}
              style={{ width: `${Math.max(job.progress, 3)}%` }}
            />
          </div>
          {isRunning(job) && estimate !== null && (
            <p className="muted small">Typically about {estimate} s.</p>
          )}
          <p className="muted small mono">{job.job_id}</p>
        </div>
      )}

      {error && <p className="error small">{error}</p>}

      {output && (
        <Result
          output={output}
          jobId={job!.job_id}
          process={job!.process}
          opacity={opacity}
          onOpacity={onOpacity}
        />
      )}
    </section>
  );
}

function isRunning(job: Job | null): boolean {
  return job !== null && !isTerminal(job.status);
}

function Result({
  output,
  jobId,
  process,
  opacity,
  onOpacity,
}: {
  output: Output;
  jobId: string;
  process: string;
  opacity: number;
  onOpacity: (value: number) => void;
}) {
  const stats = output.stats ?? {};
  const [west, south, east, north] = output.bounds;

  return (
    <div className="result">
      <h3>Result</h3>

      {output.tiles && (
        <Legend process={process} opacity={opacity} onOpacity={onOpacity} />
      )}
      <dl className="stats">
        {"median" in stats && <Stat label="median" value={stats.median} />}
        {"mean" in stats && <Stat label="mean" value={stats.mean} />}
        {"min" in stats && <Stat label="min" value={stats.min} />}
        {"max" in stats && <Stat label="max" value={stats.max} />}
      </dl>

      <p className="muted small">
        {output.crs} · {output.resolution_m} m
        {output.valid_fraction !== null && (
          <> · {(output.valid_fraction * 100).toFixed(1)}% of pixels valid after masking</>
        )}
      </p>
      <p className="muted small mono">
        {west.toFixed(3)}, {south.toFixed(3)} → {east.toFixed(3)}, {north.toFixed(3)}
      </p>

      <a className="primary block" href={api.downloadUrl(jobId)} download>
        Download GeoTIFF
      </a>

      {output.tiles === null && (
        <p className="muted small">
          No map preview — the tile server is not configured. The download opens in
          QGIS and carries its own provenance tags.
        </p>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>{value >= 0 ? `+${value.toFixed(3)}` : value.toFixed(3)}</dd>
    </div>
  );
}
