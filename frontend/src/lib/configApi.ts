import { API_URL } from "./api";

const TOKEN_KEY = "optical-inspector.config-token";

export const configSession = {
  get(): string | null {
    try {
      return sessionStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string) {
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* sin almacenamiento */
    }
  },
  clear() {
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      /* sin almacenamiento */
    }
  },
};

export class UnauthorizedError extends Error {}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : `Error HTTP ${response.status}`;
  } catch {
    return `Error HTTP ${response.status}`;
  }
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = configSession.get();
  if (token) headers.set("X-Config-Token", token);
  const response = await fetch(`${API_URL}/config${path}`, { ...init, headers });
  if (response.status === 401 && path !== "/login") {
    configSession.clear();
    throw new UnauthorizedError("La sesión de configuración expiró.");
  }
  if (!response.ok) throw new Error(await readError(response));
  return response;
}

export async function cfgJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await request(path, init)).json();
}

export function cfgSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  return cfgJson<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function cfgUpload<T>(path: string, form: FormData, method = "POST"): Promise<T> {
  return cfgJson<T>(path, { method, body: form });
}

export async function cfgBlob(path: string): Promise<Blob> {
  return (await request(path)).blob();
}

export async function login(password: string): Promise<{ token: string; default_password: boolean }> {
  const result = await cfgJson<{ token: string; default_password: boolean }>("/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  configSession.set(result.token);
  return result;
}

export interface ConfigSettings {
  inspection: Record<string, number | boolean>;
  camera: { lock_enabled: boolean; exposure: number; wb_temperature: number; autofocus: boolean; focus: number };
  calibration: { apply: boolean; square_mm: number };
  dxf: { default_unit: string };
  backup: { enabled: boolean; keep: number; interval_hours: number };
  parts: Record<string, Record<string, number | boolean>>;
  dxf_units: string[];
}

export interface PartRow {
  part_number: string;
  revision: string;
  drawing_id: number;
  source: "dxf" | "golden";
  features: number;
  sample_count: number | null;
  samples: {
    sample_count: number;
    samples: { id: string; added: string; score: number | null }[];
    max_position_std: number | null;
    statistics_active: boolean;
  } | null;
  overrides: Record<string, number | boolean>;
  validation_images: number;
}

export interface CalibrationInfo {
  rms: number;
  quality: string;
  frames_used: number;
  square_mm: number;
  calibrated_at: string;
  image_size: [number, number];
  plane_mm_per_px: number | null;
}

export interface StoragePath {
  key: string;
  path: string;
  exists: boolean;
  bytes?: number;
  files?: number;
  free_bytes?: number | null;
}

export interface StorageStatus {
  env_file: string;
  paths: StoragePath[];
  host: string;
  port: number;
  legacy_inspections: number;
  backups: { name: string; size: number; modified: string }[];
}

export interface ValidationEntry {
  id: string;
  file: string;
  expected: "OK" | "FALLO";
  note: string;
  added: string;
}

export interface ValidationRun {
  total: number;
  correct: number;
  false_ok: number;
  false_reject: number;
  inconclusive: number;
  errors: number;
  passed: boolean;
  rows: { id: string; expected: string; status: string; score: number | null; outcome: string; note: string; detail?: string }[];
}
