import type { CameraStatus, DatabaseResponse, DrawingRecord, InspectionRecord, InspectionResponse, PartRecord, UploadResponse } from "../types/api";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";
export const WS_URL = import.meta.env.VITE_WS_URL ?? "ws://127.0.0.1:8000";

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body.detail ?? body.message ?? `Error HTTP ${response.status}`;
  } catch {
    return `Error HTTP ${response.status}`;
  }
}

export async function startCamera(): Promise<CameraStatus> {
  console.log(`${API_URL}/camera/start`)
  const response = await fetch(`${API_URL}/camera/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ width: 1280, height: 720, fps: 10, preview_quality: 75 }),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function stopCamera(): Promise<CameraStatus> {
  const response = await fetch(`${API_URL}/camera/stop`, { method: "POST" });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function getCameraStatus(): Promise<CameraStatus> {
  const response = await fetch(`${API_URL}/camera/status`, { cache: "no-store" });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

// export async function inspectCapture(partNo: string, inspector: string): Promise<InspectionResponse> {
//   const data = new FormData();
//   data.append("part_no", partNo.trim());
//   data.append("inspector", inspector.trim());
//   const response = await fetch(`${API_URL}/inspect/capture`, { method: "POST", body: data });
//   if (!response.ok) throw new Error(await parseError(response));
//   const result = await response.json();
//   if (!result.inspection_id) throw new Error(result.message ?? "La inspección no regresó un ID.");
//   return result;
// }
export async function inspectCapture(
  partNo: string,
  inspector: string,
): Promise<InspectionResponse> {
  const data = new FormData();

  data.append(
    "part_no",
    partNo.trim(),
  );

  data.append(
    "inspector",
    inspector.trim(),
  );

  const response = await fetch(
    `${API_URL}/inspect/capture`,
    {
      method: "POST",
      body: data,
    },
  );

  if (!response.ok) {
    throw new Error(
      await parseError(response),
    );
  }

  const result =
    await response.json();

  if (!result.inspection_id) {
    throw new Error(
      result.message ??
        "La inspección no regresó un ID.",
    );
  }

  return result;
}


export async function inspectPhoto(
  partNo: string,
  inspector: string,
  photo: File,
): Promise<InspectionResponse> {
  const data = new FormData();

  data.append(
    "part_no",
    partNo.trim(),
  );

  data.append(
    "inspector",
    inspector.trim(),
  );

  data.append(
    "photo",
    photo,
  );

  const response = await fetch(
    `${API_URL}/inspect`,
    {
      method: "POST",
      body: data,
    },
  );

  if (!response.ok) {
    throw new Error(
      await parseError(response),
    );
  }

  const result =
    await response.json();

  if (!result.inspection_id) {
    throw new Error(
      result.message ??
        "La inspección no regresó un ID.",
    );
  }

  return result;
}
export async function uploadDrawing(data: {
  dxf: File;
  partNo: string;
  revision: string;
  description: string;
}): Promise<UploadResponse> {
  const form = new FormData();
  form.append("dxf", data.dxf);
  form.append("part_no", data.partNo.trim());
  form.append("revision", data.revision.trim());
  form.append("description", data.description.trim());
  const response = await fetch(`${API_URL}/upload/dxf`, { method: "POST", body: form });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}
export function drawingProcessedImageUrl(
  drawingId: number,
): string {
  return `${API_URL}/upload/${drawingId}/processed-image?v=${Date.now()}`;
}
export function inspectionImageUrl(id: number, kind: "original-image" | "rectified-image" | "overlay-image") {
  return `${API_URL}/inspect/${id}/${kind}?v=${Date.now()}`;
}
export async function getParts(): Promise<
  DatabaseResponse<PartRecord>
> {
  const response = await fetch(
    `${API_URL}/database/parts`,
    {
      cache: "no-store",
    },
  );

  if (!response.ok) {
    throw new Error(
      await parseError(response),
    );
  }

  return response.json();
}


export async function getDrawings(): Promise<
  DatabaseResponse<DrawingRecord>
> {
  const response = await fetch(
    `${API_URL}/database/drawings`,
    {
      cache: "no-store",
    },
  );

  if (!response.ok) {
    throw new Error(
      await parseError(response),
    );
  }

  return response.json();
}


export async function getInspections(): Promise<
  DatabaseResponse<InspectionRecord>
> {
  const response = await fetch(
    `${API_URL}/database/inspections`,
    {
      cache: "no-store",
    },
  );

  if (!response.ok) {
    throw new Error(
      await parseError(response),
    );
  }

  return response.json();
}


// export function drawingProcessedImageUrl(
//   drawingId: number,
// ): string {
//   return (
//     `${API_URL}/upload/` +
//     `${drawingId}/processed-image` +
//     `?v=${Date.now()}`
//   );
// }


export function inspectionOriginalImageUrl(
  inspectionId: number,
): string {
  return (
    `${API_URL}/inspect/` +
    `${inspectionId}/original-image` +
    `?v=${Date.now()}`
  );
}


export function inspectionRectifiedImageUrl(
  inspectionId: number,
): string {
  return (
    `${API_URL}/inspect/` +
    `${inspectionId}/rectified-image` +
    `?v=${Date.now()}`
  );
}


export function inspectionOverlayImageUrl(
  inspectionId: number,
): string {
  return (
    `${API_URL}/inspect/` +
    `${inspectionId}/overlay-image` +
    `?v=${Date.now()}`
  );
}