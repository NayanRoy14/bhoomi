/**
 * Typed client for the Bhoomi API.
 *
 * Mirrors backend/api/schemas.py. When that changes, this must change with it --
 * until the OpenAPI schema is used to generate this file (February, alongside
 * OGC API - Processes).
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** PLAN.md 8. Also enforced server-side; this is for immediate feedback. */
export const MAX_AOI_KM2 = 500;

export interface Polygon {
  type: "Polygon";
  coordinates: number[][][];
}

export interface Scene {
  id: string;
  collection: string;
  satellite: string | null;
  acquired_at: string;
  cloud_cover: number | null;
  processing_baseline: string | null;
  bbox: number[];
  geometry: Polygon;
  thumbnail: string | null;
  /** Fraction of the AOI inside this scene. Below 1 means it spans a boundary. */
  aoi_coverage: number;
  available_processes: string[];
}

export interface SceneSearchResponse {
  count: number;
  aoi_area_km2: number;
  scenes: Scene[];
}

export interface SceneSearchRequest {
  aoi: Polygon;
  start_date?: string;
  end_date?: string;
  max_cloud?: number;
  collection?: string;
  limit?: number;
}

export interface Health {
  status: "ok" | "degraded";
  version: string;
  catalogue: string;
  queue_depth: number | null;
  workers: number | null;
}

/* --------------------------------------------------------------- jobs (7.3) */

export interface JobCreateRequest {
  process: string;
  scene_ids: string[];
  aoi: Polygon;
  parameters?: Record<string, unknown>;
}

export interface Link {
  rel: string;
  href: string;
  type: string;
}

export interface JobCreated {
  job_id: string;
  status: string;
  position_in_queue: number;
  estimated_seconds: number;
  links: Link[];
}

/** The `job_status` enum in backend/db/jobs.py. */
export type JobStatus =
  | "queued"
  | "searching"
  | "reading"
  | "processing"
  | "writing_cog"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out";

export const TERMINAL_STATUSES: readonly JobStatus[] = [
  "completed",
  "failed",
  "cancelled",
  "timed_out",
];

export function isTerminal(status: string): boolean {
  return (TERMINAL_STATUSES as readonly string[]).includes(status);
}

export interface Job {
  job_id: string;
  process: string;
  status: JobStatus;
  progress: number;
  message: string;
  /** User-facing only. The traceback is never served (PLAN.md 4.3). */
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface Output {
  type: string;
  cog: string;
  download: string;
  bounds: number[];
  crs: string;
  resolution_m: number;
  valid_fraction: number | null;
  stats: Record<string, number> | null;
  expires_at: string | null;
  /** Null until TiTiler is wired up; better absent than a URL that 404s. */
  tiles: string | null;
  /** How to read this result -- unmasked cloud, mixed baselines. Usually empty. */
  warnings: string[];
}

export interface JobResult {
  job_id: string;
  outputs: Output[];
}

/** An API error carrying the server's code and message. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "network_error",
      `Could not reach the Bhoomi API at ${API_URL}. Is the backend running?`,
      0,
    );
  }

  if (!response.ok) {
    // FastAPI wraps HTTPException payloads in "detail"; our own handlers do not.
    const body = await response.json().catch(() => null);
    const payload = body?.detail ?? body;
    throw new ApiError(
      payload?.code ?? "error",
      payload?.message ??
        (typeof payload === "string" ? payload : `Request failed (${response.status})`),
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/health"),

  searchScenes: (body: SceneSearchRequest) =>
    request<SceneSearchResponse>("/api/v1/scenes/search", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createJob: (body: JobCreateRequest) =>
    request<JobCreated>("/api/v1/jobs", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getJob: (id: string) => request<Job>(`/api/v1/jobs/${id}`),

  getJobResult: (id: string) => request<JobResult>(`/api/v1/jobs/${id}/result`),

  /** Absolute, so it works as an href and as a GIS data source. */
  downloadUrl: (id: string) => `${API_URL}/api/v1/jobs/${id}/download`,
};
