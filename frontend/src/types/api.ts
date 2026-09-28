export type Status = "OK" | "REVISAR" | "FALLO" | "pending";

export interface CameraStatus {
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