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
  change: "Difference between two dates: later − earlier.",
  fake: "Queue check. Sleeps ten seconds and produces no raster.",
};

/** Processes needing a second scene, and how many in total. */
const SCENE_COUNT: Record<string, number> = { change: 2 };

const CHANGEABLE = ["ndvi", "ndwi", "ndbi"];

function sceneCount(process: string): number {
  return SCENE_COUNT[process] ?? 1;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Sentinel-2 L2A is not usefully available before this. */
const ARCHIVE_START_YEAR = 2016;

/** How far back the comparison window is offered by default. */
const DEFAULT_LOOKBACK_YEARS = 6;

/**
 * The same calendar window, some years earlier.
 *
 * Day-of-year is preserved deliberately: 5.4.4 wants the two dates in the same
 * part of the year, because a March/September pair measures the season as much
 * as the land. Only the year moves.
 */
function shiftYears(iso: string, years: number): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const year = Math.max(date.getFullYear() - years, ARCHIVE_START_YEAR);
  return `${year}-${iso.slice(5)}`;
}

interface Props {
  aoi: Polygon | null;
  scene: Scene | null;
  /** Every scene from the search, so change detection can pick its second date. */
  scenes: Scene[];
  /** The primary search window, used to seed the comparison window. */
  searchStart: string;
  searchEnd: string;
  onOutput: (output: Output | null) => void;
  opacity: number;
  onOpacity: (value: number) => void;
}

export default function AnalysisPanel({
  aoi,
  scene,
  scenes,
  searchStart,
  searchEnd,
  onOutput,
  opacity,
  onOpacity,
}: Props) {
  const [process, setProcess] = useState<string>("ndvi");
  const [compareId, setCompareId] = useState<string | null>(null);
  const [index, setIndex] = useState<string>("ndvi");
  const [job, setJob] = useState<Job | null>(null);
  const [output, setOutput] = useState<Output | null>(null);
  const [estimate, setEstimate] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // The comparison date gets its own search.
  //
  // 8 caps a *single* search at 366 days, to bound catalogue query cost. The
  // change picker used to offer only scenes from the primary search, so the
  // cap became a cap on the GAP between the two dates -- which made this
  // project's own flagship comparison, 2020 against 2026, impossible to express
  // in the interface at all. It was API-only.
  //
  // Searching twice fixes that without weakening anything: each query is still
  // bounded, and only the interval between them is free.
  const [compareStart, setCompareStart] = useState("");
  const [compareEnd, setCompareEnd] = useState("");
  const [compareScenes, setCompareScenes] = useState<Scene[] | null>(null);
  const [compareSearching, setCompareSearching] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);

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

  // Change is offered whenever the selected scene supports any index, since it
  // differences one; the per-scene list only names the single-scene processes.
  const available = scene?.available_processes ?? [];
  const offered = available.length > 0 ? [...available, "change"] : available;

  const needsTwo = sceneCount(process) === 2;
  // Candidates come from the comparison search once one has been run, and from
  // the primary search otherwise -- so comparing two dates inside one search
  // still works without the extra step.
  const pool = compareScenes ?? scenes;
  // Only scenes that fully cover the AOI can be the second date, and a scene
  // cannot be differenced against itself.
  const candidates = pool.filter(
    (s) => s.id !== scene?.id && s.aoi_coverage >= 0.999,
  );
  const compare = candidates.find((s) => s.id === compareId) ?? null;

  const partial = scene !== null && scene.aoi_coverage < 0.999;
  const canSubmit =
    aoi !== null &&
    scene !== null &&
    !partial &&
    !submitting &&
    !isRunning(job) &&
    (!needsTwo || compare !== null);

  // A baseline mismatch is not fatal -- PLAN.md 5.3 asks the API to flag it,
  // not block it -- but the user should see it before spending a job, because
  // part of the "change" would be Sen2Cor version drift rather than ground.
  const baselineMismatch =
    needsTwo &&
    compare !== null &&
    scene !== null &&
    scene.processing_baseline !== null &&
    compare.processing_baseline !== null &&
    scene.processing_baseline !== compare.processing_baseline;

  // Same month across years keeps phenology comparable (5.4.4); a March/September
  // pair measures the season as much as the land.
  const seasonalGap =
    needsTwo && compare !== null && scene !== null
      ? Math.abs(
          ((new Date(scene.acquired_at).getMonth() -
            new Date(compare.acquired_at).getMonth() +
            18) %
            12) -
            6,
        )
      : 0;

  // A scene that cannot run the selected process should not leave a stale
  // selection in the dropdown -- the submit would fail server-side for a
  // reason the user never chose.
  useEffect(() => {
    if (offered.length > 0 && !offered.includes(process)) {
      setProcess(offered[0]);
    }
  }, [offered, process]);

  // The second date belongs to the search that produced it. Keeping a stale id
  // would submit a scene the user can no longer see in the list.
  useEffect(() => {
    if (compareId && !candidates.some((s) => s.id === compareId)) {
      setCompareId(null);
    }
  }, [candidates, compareId]);

  // Seed the comparison window from the primary one, day-of-year preserved.
  // Only while untouched: re-seeding after the user has edited it would undo
  // their choice every time the primary search changed.
  useEffect(() => {
    if (compareScenes !== null || compareStart || compareEnd) return;
    if (!searchStart || !searchEnd) return;
    setCompareStart(shiftYears(searchStart, DEFAULT_LOOKBACK_YEARS));
    setCompareEnd(shiftYears(searchEnd, DEFAULT_LOOKBACK_YEARS));
  }, [searchStart, searchEnd, compareScenes, compareStart, compareEnd]);

  async function searchCompare() {
    if (!aoi) return;
    setCompareSearching(true);
    setCompareError(null);
    setCompareId(null);
    try {
      const result = await api.searchScenes({
        aoi,
        start_date: compareStart || undefined,
        end_date: compareEnd || undefined,
        // Deliberately not the primary search's cloud limit. A second date is
        // scarcer than a first -- there may be only one usable scene in the
        // window -- and a partly cloudy one the user can see and reject beats
        // an empty list. The valid-pixel fraction on the result says what the
        // masking actually cost.
        max_cloud: 40,
      });
      setCompareScenes(result.scenes);
      if (result.scenes.filter((s) => s.aoi_coverage >= 0.999).length === 0) {
        setCompareError(
          result.count === 0
            ? "No scenes in that window. Try widening it or moving it."
            : "Scenes exist in that window but none covers your whole area.",
        );
      }
    } catch (err) {
      setCompareScenes(null);
      setCompareError(
        err instanceof ApiError ? err.message : "Could not search for a second date.",
      );
    } finally {
      setCompareSearching(false);
    }
  }

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
        // Chronological order is the server's job -- `compute_change` sorts by
        // acquisition time, so a pair submitted either way round gives the same
        // sign rather than a flipped one.
        scene_ids: needsTwo && compare ? [compare.id, scene.id] : [scene.id],
        aoi,
        parameters: needsTwo ? { index } : {},
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
          {offered.map((name) => (
            <option key={name} value={name}>
              {name.toUpperCase()}
            </option>
          ))}
        </select>
      </label>
      <p className="muted small">{DESCRIPTIONS[process] ?? ""}</p>

      {needsTwo && (
        <>
          <label className="field">
            <span>Index to difference</span>
            <select
              value={index}
              onChange={(e) => setIndex(e.target.value)}
              disabled={isRunning(job)}
            >
              {CHANGEABLE.filter((i) => available.includes(i)).map((i) => (
                <option key={i} value={i}>
                  {i.toUpperCase()}
                </option>
              ))}
            </select>
          </label>

          <fieldset className="subsearch" disabled={isRunning(job)}>
            <legend>Find the second date</legend>
            <p className="muted small">
              Its own search, so the two dates can be years apart. Each search
              still covers at most a year.
            </p>
            <div className="dates">
              <label className="field">
                <span>From</span>
                <input
                  type="date"
                  value={compareStart}
                  onChange={(e) => setCompareStart(e.target.value)}
                />
              </label>
              <label className="field">
                <span>To</span>
                <input
                  type="date"
                  value={compareEnd}
                  onChange={(e) => setCompareEnd(e.target.value)}
                />
              </label>
            </div>
            <button onClick={searchCompare} disabled={!aoi || compareSearching}>
              {compareSearching ? "Searching…" : "Search this window"}
            </button>
            {compareError && <p className="error small">{compareError}</p>}
          </fieldset>

          <label className="field">
            <span>Compare against</span>
            <select
              value={compareId ?? ""}
              onChange={(e) => setCompareId(e.target.value || null)}
              disabled={isRunning(job)}
            >
              <option value="">Pick a second date…</option>
              {candidates.map((s) => (
                <option key={s.id} value={s.id}>
                  {formatDate(s.acquired_at)}
                  {s.cloud_cover !== null && ` — ${s.cloud_cover.toFixed(1)}% cloud`}
                </option>
              ))}
            </select>
          </label>

          {candidates.length > 0 && (
            <p className="muted small">
              {candidates.length} candidate{candidates.length === 1 ? "" : "s"} from{" "}
              {compareScenes === null ? "the main search" : "the window above"}.
            </p>
          )}

          {candidates.length === 0 && compareScenes === null && (
            <p className="warn small">
              No other scene in the main search covers your whole area. Search a
              window above to find one — it can be any year.
            </p>
          )}

          {compare && scene && (
            <p className="muted small">
              {formatDate(compare.acquired_at)} → {formatDate(scene.acquired_at)}.
              Green is a rise, brown a fall.
            </p>
          )}

          {baselineMismatch && (
            <p className="warn small">
              These two scenes have different processing baselines
              ({compare?.processing_baseline} and {scene?.processing_baseline}).
              Some of the difference will be Sen2Cor version drift rather than
              change on the ground. The job will still run and the result says so.
            </p>
          )}

          {seasonalGap >= 3 && (
            <p className="warn small">
              These dates are about {seasonalGap} months apart in the year. Vegetation
              differs between seasons for reasons that are not land-use change — a
              pair from the same month in different years compares better.
            </p>
          )}
        </>
      )}

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

      {output.warnings.length > 0 && (
        <div className="result-warnings">
          {/* Shown above the numbers, not below: these change how the numbers
              should be read, so reading them second is too late. */}
          {output.warnings.map((w) => (
            <p key={w} className="warn small">
              {w}
            </p>
          ))}
        </div>
      )}

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
