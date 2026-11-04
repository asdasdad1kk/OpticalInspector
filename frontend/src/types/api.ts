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
  material: "LATON" | "ACERO";
  x: number;
  y: number;
  radius: number;
  enabled: boolean;
  radius_reliable?: boolean;
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
}
