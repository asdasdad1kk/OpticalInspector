import { API_URL } from "./api";
import type { CropRect } from "../types/api";

export type CompareVerdict = "OK" | "REVISAR" | "FALLO";
export type Sensitivity = "maximo" | "estricto" | "normal" | "tolerante";
export type Severity = "leve" | "moderada" | "critica";

export interface ComparePart {
  id: number;
  name: string;
  revision: number;
  description: string;
  width: number;
  height: number;
  created_at: string;
  revisions?: number;
  inspections?: number;
  last_status?: CompareVerdict | null;
}

export interface CompareRegion {
  id: number;
  x: number;
  y: number;
  w: number;
  h: number;
  zone: string;
  severity: Severity;
  kind: string;
  kind_label: string;
  area_px: number;
  area_pct: number;
  delta_e_max: number;
  golden_color: string;
  test_color: string;
  explanation: string;
  facts: string[];
  probable_cause: string;
}

export interface CompareReport {
  id: number;
  created_at: string;
  part: { id: number; name: string; revision: number };
  status: CompareVerdict;
  score: number;
  summary: string;
  regions: CompareRegion[];
  metrics: {
    anomalous_area_pct: number;
    regions: number;
    similarity: number;
    excluded_pct: number;
    work_size: [number, number];
    threshold_delta_e: number;
    peak_delta_e: number;
    near_misses: number;
  };
  near_misses: { x: number; y: number; w: number; h: number; zone: string; area_px: number; delta_e_max: number; ratio: number }[];
  alignment: { method: string; inliers: number; coverage_pct: number; ecc: number | null; object_scale: number; rotation_deg: number };
  located: { polygon: [number, number][]; photo_size: [number, number]; area_fraction: number };
  quality: { sharpness: number; brightness: number };
  sensitivity: Sensitivity;
  images: Record<string, string>;
}

export interface CompareHistoryRow {
  id: number;
  part_name: string;
  part_revision: number;
  status: CompareVerdict;
  score: number | null;
  regions: number;
  sensitivity: string;
  created_at: string;
}

export interface SensitivityOption {
  id: Sensitivity;
  delta_e: number;
  min_area_pct: number;
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : `Error HTTP ${response.status}`;
  } catch {
    return `Error HTTP ${response.status}`;
  }
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

/** Las imagenes del informe vienen como rutas relativas a /api (p. ej. /compare/inspections/3/image/heatmap). */
export function compareImageUrl(path: string): string {
  return `${API_URL}${path}`;
}

export function partImageUrl(id: number, maxSide?: number): string {
  return `${API_URL}/compare/parts/${id}/image${maxSide ? `?max_side=${maxSide}` : ""}`;
}

export async function getCompareOptions(): Promise<{ default_sensitivity: Sensitivity; sensitivities: SensitivityOption[] }> {
  return json(await fetch(`${API_URL}/compare/options`));
}

export async function listParts(): Promise<ComparePart[]> {
  return json(await fetch(`${API_URL}/compare/parts`, { cache: "no-store" }));
}

export async function createPart(data: { photo: File; name: string; description: string; crop?: CropRect }): Promise<ComparePart> {
  const form = new FormData();
  form.append("photo", data.photo);
  form.append("name", data.name);
  form.append("description", data.description);
  if (data.crop) form.append("crop", JSON.stringify(data.crop));
  return json(await fetch(`${API_URL}/compare/parts`, { method: "POST", body: form }));
}

export async function deletePart(id: number): Promise<void> {
  await json(await fetch(`${API_URL}/compare/parts/${id}`, { method: "DELETE" }));
}

export async function runCompare(data: {
  photo: File;
  partId: number;
  sensitivity: Sensitivity;
  crop?: CropRect;
}): Promise<CompareReport> {
  const form = new FormData();
  form.append("photo", data.photo);
  form.append("part_id", String(data.partId));
  form.append("sensitivity", data.sensitivity);
  if (data.crop) form.append("crop", JSON.stringify(data.crop));
  return json(await fetch(`${API_URL}/compare/inspect`, { method: "POST", body: form }));
}

export async function listCompareHistory(part?: string): Promise<CompareHistoryRow[]> {
  const query = part ? `?part=${encodeURIComponent(part)}&limit=30` : "?limit=30";
  return json(await fetch(`${API_URL}/compare/inspections${query}`, { cache: "no-store" }));
}

export async function getCompareReport(id: number): Promise<CompareReport> {
  return json(await fetch(`${API_URL}/compare/inspections/${id}`, { cache: "no-store" }));
}
