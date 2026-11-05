import { API_URL } from "./api";

export type ScanState = "idle" | "scanning" | "paused" | "processing" | "done" | "error";

export interface ScanAnalysis {
  dimensions_mm: [number, number, number];
  vertices: number;
  triangles: number;
  area_mm2: number;
  watertight: boolean;
  flatness?: { planar_fraction: number; peak_to_valley_mm: number; rms_mm: number; tilt_deg: number };
}

export interface ScannerStatus {
  available: boolean;
  install_hint: string | null;
  camera_running: boolean;
  depth_available: boolean;
  state: ScanState;
  message: string;
  frames: number;
  tracked_frames: number;
  fps: number;
  elapsed_s: number;
  voxel_mm: number;
  has_mesh: boolean;
  analysis: ScanAnalysis | null;
  isolated: boolean;
  isolate_message: string;
  can_reprocess: boolean;
  saved_id: string | null;
  tracking: "inicializando" | "ok" | "perdido";
  keyframes: number;
  lost_total: number;
  lost_streak: number;
  coverage_mm: [number, number, number] | null;
  camera: { position: [number, number, number]; forward: [number, number, number] } | null;
  tracking_message: string;
  speed_mm_s: number;
  alignment_quality: number;
}

export interface SavedScan {
  id: string;
  name: string;
  created_at: string;
  analysis: ScanAnalysis | null;
  isolated: boolean;
  voxel_mm: number;
  frames: number;
  duration_s: number;
}

export interface PointCloudData {
  count: number;
  positions: Float32Array;
  colors: Uint8Array;
}

export interface MeshData {
  vertexCount: number;
  triangleCount: number;
  positions: Float32Array;
  colors: Uint8Array;
  indices: Uint32Array;
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body.detail ?? body.message ?? `Error HTTP ${response.status}`;
  } catch {
    return `Error HTTP ${response.status}`;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}/scanner${path}`, { cache: "no-store", ...init });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export const scannerApi = {
  status: () => request<ScannerStatus>("/status"),
  start: (voxelMm: number, maxDepthMm: number) => post<ScannerStatus>("/start", { voxel_mm: voxelMm, max_depth_mm: maxDepthMm }),
  pause: () => post<ScannerStatus>("/pause"),
  resume: () => post<ScannerStatus>("/resume"),
  finish: (isolate: boolean) => post<ScannerStatus>("/finish", { isolate }),
  reprocess: (isolate: boolean) => post<ScannerStatus>("/reprocess", { isolate }),
  reset: () => post<ScannerStatus>("/reset"),
  saved: () => request<SavedScan[]>("/saved"),
  save: (name: string) => post<SavedScan>("/save", { name }),
  openSaved: (id: string) => post<ScannerStatus>(`/saved/${id}/open`),
  deleteSaved: (id: string) => request<{ ok: boolean }>(`/saved/${id}`, { method: "DELETE" }),
  exportUrl: (format: "stl" | "ply" | "obj") => `${API_URL}/scanner/export.${format}`,

  /** Nube de vista previa: [uint32 N][float32 N*3 (mm)][uint8 N*3]. */
  async preview(maxPoints = 60000): Promise<PointCloudData> {
    const response = await fetch(`${API_URL}/scanner/preview.bin?max_points=${maxPoints}`, { cache: "no-store" });
    if (!response.ok) throw new Error(await parseError(response));
    const buffer = await response.arrayBuffer();
    const count = new DataView(buffer).getUint32(0, true);
    return {
      count,
      positions: new Float32Array(buffer, 4, count * 3),
      colors: new Uint8Array(buffer, 4 + count * 12, count * 3),
    };
  },

  /** Malla: [uint32 V][uint32 F][float32 V*3 (mm)][uint8 V*3][uint32 F*3]. */
  async mesh(maxTriangles = 300000): Promise<MeshData> {
    const response = await fetch(`${API_URL}/scanner/mesh.bin?max_triangles=${maxTriangles}`, { cache: "no-store" });
    if (!response.ok) throw new Error(await parseError(response));
    const buffer = await response.arrayBuffer();
    const view = new DataView(buffer);
    const vertexCount = view.getUint32(0, true);
    const triangleCount = view.getUint32(4, true);
    const colorsAt = 8 + vertexCount * 12;
    const indicesAt = colorsAt + vertexCount * 3;
    return {
      vertexCount,
      triangleCount,
      positions: new Float32Array(buffer, 8, vertexCount * 3),
      colors: new Uint8Array(buffer, colorsAt, vertexCount * 3),
      // El offset de los indices no queda alineado a 4 bytes: se copia.
      indices: new Uint32Array(buffer.slice(indicesAt, indicesAt + triangleCount * 12)),
    };
  },
};
