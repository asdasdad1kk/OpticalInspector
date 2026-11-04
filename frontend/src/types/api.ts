export type Status = "OK" | "REVISAR" | "FALLO" | "pending";

export type CameraProvider = "realsense" | "brio";

export interface CameraDevice {
  provider: CameraProvider;
  index: number;
  name: string;
  serial?: string | null;
}

export interface CameraStatus {
  provider?: CameraProvider;
  device_index?: number;
  message?: string;
  running: boolean;
  frame_available: boolean;
  sequence: number;
  width: number;
  height: number;
  fps: number;
  preview_quality: number;
  device_name?: string | null;
  serial_number?: string | null;
  last_error?: string | null;
  /** RealSense con stream de profundidad activo: permite escanear en 3D. */
  depth_available?: boolean;
}

export interface InspectionResponse {
  inspection_id: number;
  drawing_id: number;
  inspector: string;
  score: number;
  status: Status;
  roi_count: number;
  summary: { OK?: number; REVISAR?: number; FALLO?: number };
  alignment_method: string;
  alignment_inliers: number;
  message: string;
}

export interface UploadResponse {
  drawing_id: number;
  part_id: number;
  part_no: string;
  revision: string;
  description?: string | null;
  file_name: string;
  width_mm: number;
  height_mm: number;
  roi_count: number;
  summary: Record<string, number>;
  message: string;
}
export type InspectionStatus =
  | "OK"
  | "REVISAR"
  | "FALLO"
  | "pending";


export interface PartRecord {
  id: number;
  part_number: string;
  revision: string;
  description: string | null;
  created_at: string;
}


export interface DrawingRecord {
  id: number;
  part_id: number;
  part_number: string;
  revision: string;
  description: string | null;

  file_name: string;

  width_mm: number;
  height_mm: number;

  roi_count: number;

  summary: Record<string, number>;

  created_at: string;
}


export interface InspectionRecord {
  id: number;
  drawing_id: number;

  part_id: number;
  part_number: string;
  revision: string;

  inspector: string;

  score: number;
  status: InspectionStatus;

  roi_count: number;

  summary: {
    OK?: number;
    REVISAR?: number;
    FALLO?: number;
  };

  alignment_method: string;
  alignment_inliers: number;

  approved: boolean;
  approved_by: string | null;
  approved_at: string | null;

  created_at: string;
}


export interface DatabaseResponse<T> {
  items: T[];
  total: number;
}

export interface InspectionOptions {
  check_position?: boolean;
  check_type?: boolean;
  check_diameter?: boolean;
  check_edges?: boolean;
  dimension_tolerance_pct?: number;
  distance_tolerance_pct?: number;
  categories?: Partial<ReferenceCategories>;
}

export interface ReferenceFeature {
  id: number;
  type: string;
  material: "LATON" | "ACERO" | "DELRON" | "BARRENO";
  x: number;
  y: number;
  radius: number;
  enabled: boolean;
  radius_reliable?: boolean;
  /** Radio del barreno central (insertos/delrones); para el modelo 3D. */
  bore_radius?: number;
  /** Radio exterior de la cabeza/disco; para el modelo 3D. */
  outer_radius?: number;
  /** Altura sobre la placa medida con profundidad (mm). */
  height_mm?: number;
  height_reliable?: boolean;
}

/** Medidas obtenidas con la profundidad de la RealSense. */
export interface ReliefMeasures {
  scale_mm_per_px: number;
  distance_mm: number;
  tilt_deg: number;
  thickness_mm: number | null;
  thickness_source: string | null;
  flatness_mm: number | null;
  rms_mm: number | null;
  plane_rms_mm: number;
  valid_fraction: number;
  noise_mm: number | null;
  quality: "Buena" | "Regular" | "Pobre";
  warnings: string[];
}

/** Malla de alturas respecto al plano de la placa (int16 little-endian en base64). */
export interface ReliefGrid {
  width: number;
  height: number;
  step_px: number;
  quant_mm: number;
  data_b64: string;
  bounds: { x0: number; x1: number; y0: number; y1: number };
}

export type Relief =
  | { available: false; reason?: string }
  | { available: true; grid: ReliefGrid; measures: ReliefMeasures };

export interface ScanInfo {
  scan_id: string;
  frames: number;
  valid_fraction: number;
  noise_mm: number | null;
  width: number;
  height: number;
}

export interface ReferenceCategories {
  features: boolean;
  contour: boolean;
  distances: boolean;
  label: boolean;
}

export interface ReferenceAnalysis {
  features: ReferenceFeature[];
  contour: [number, number][];
  bounds: { xmin: number; xmax: number; ymin: number; ymax: number };
  label: { x: number; y: number; w: number; h: number; angle: number } | null;
  dimensions: { length: number; width: number; area: number; feature_count: number; unit: string };
  scale: number;
  relative: boolean;
  enabled: ReferenceCategories;
  preview_png: string;
  scale_source?: "dimension" | "calibracion" | "relativa" | "profundidad";
  image_jpeg?: string | null;
  relief?: Relief;
  scan_id?: string;
}

/** Resultado por feature guardado en la inspección (coordenadas del canvas rectificado). */
export interface InspectionItemResult {
  id: number | string;
  type: string;
  kind?: "contour" | "label" | "distances";
  status: "OK" | "REVISAR" | "FALLO";
  score?: number;
  found?: boolean;
  center_x_px?: number;
  center_y_px?: number;
  expected_radius_px?: number;
  deviation?: number | null;
  radius_ratio?: number | null;
  observed_type?: string | null;
  box?: [number, number, number, number];
  reasons?: string[];
  checks?: Record<string, string>;
}

export interface InspectionDetail extends InspectionRecord {
  description?: string | null;
  items: InspectionItemResult[];
  alignment: Record<string, unknown>;
}

/** Respuesta de /reference/{id}/model: lo necesario para reconstruir la pieza en 3D. */
export interface PartModelResponse {
  drawing_id: number;
  part_number: string;
  revision: string;
  source: "golden" | "dxf";
  unit: string;
  contour: [number, number][];
  features: ReferenceFeature[];
  texture: { url: string; width: number; height: number; scale: number } | null;
  relief: Relief | null;
}
