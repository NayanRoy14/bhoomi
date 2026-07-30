/**
 * Geodesic geometry helpers.
 *
 * The AOI area readout must update while the user drags a vertex, so it is
 * computed here rather than round-tripping to the server. The server recomputes
 * it independently and is the authority -- this is feedback, not enforcement.
 */

import type { Polygon } from "./api";

const EARTH_RADIUS_M = 6_378_137;

/**
 * Area of a polygon on a sphere, in km².
 *
 * Planar area in degrees would be meaningless: a degree of longitude is 111 km
 * at the equator and 103 km at Kolkata. This uses the standard spherical excess
 * formula, which agrees with the server's UTM projection to well within 1%
 * at the scales Bhoomi allows.
 */
export function polygonAreaKm2(polygon: Polygon): number {
  const ring = polygon.coordinates[0];
  if (!ring || ring.length < 4) return 0;

  let total = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [lon1, lat1] = ring[i];
    const [lon2, lat2] = ring[i + 1];
    total +=
      toRadians(lon2 - lon1) *
      (2 + Math.sin(toRadians(lat1)) + Math.sin(toRadians(lat2)));
  }
  const areaM2 = Math.abs((total * EARTH_RADIUS_M * EARTH_RADIUS_M) / 2);
  return areaM2 / 1e6;
}

function toRadians(degrees: number): number {
  return (degrees * Math.PI) / 180;
}

/** Close an open ring so it forms a valid GeoJSON polygon. */
export function closeRing(points: number[][]): number[][] {
  if (points.length === 0) return points;
  const [first] = points;
  const last = points[points.length - 1];
  if (first[0] === last[0] && first[1] === last[1]) return points;
  return [...points, first];
}

export function toPolygon(points: number[][]): Polygon {
  return { type: "Polygon", coordinates: [closeRing(points)] };
}

/** Bounding box as [west, south, east, north]. */
export function bounds(polygon: Polygon): [number, number, number, number] {
  const ring = polygon.coordinates[0];
  const lons = ring.map((p) => p[0]);
  const lats = ring.map((p) => p[1]);
  return [
    Math.min(...lons),
    Math.min(...lats),
    Math.max(...lons),
    Math.max(...lats),
  ];
}

export function formatArea(km2: number): string {
  if (km2 < 1) return `${(km2 * 100).toFixed(0)} ha`;
  return `${km2.toLocaleString(undefined, { maximumFractionDigits: 1 })} km²`;
}
