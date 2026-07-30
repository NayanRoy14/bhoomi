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
};
