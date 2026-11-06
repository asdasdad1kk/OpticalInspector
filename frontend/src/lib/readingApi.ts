import { API_URL } from "./api";
import type { CropRect } from "../types/api";

export type ReadingKind = "code" | "text";
export type ReadingRule = "equals" | "regex" | "present" | "record";

/** Zona de lectura. Las coordenadas son de la imagen sobre la que se dibujó (foto o patrón). */
export interface ReadingZone {
  id: string;
  name: string;
  kind: ReadingKind;
  rule: ReadingRule;
  expected: string;
  pattern: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ReadingResult {
  id: string;
  name: string;
  kind: ReadingKind;
  rule: ReadingRule;
  expected: string;
  value: string;
  format: string | null;
  confidence: number | null;
  status: "OK" | "REVISAR" | "FALLO" | "OMITIDO";
  reason: string;
  ms?: number;
}

export interface ReadingCapabilities {
  codes: { available: boolean; enabled: boolean; engine: string; version: string | null; formats: string; note?: string };
  text: {
    available: boolean;
    enabled: boolean;
    version: string | null;
    path: string | null;
    languages: string[];
    languages_wanted: string;
    note?: string;
  };
}

export const RULE_LABEL: Record<ReadingRule, string> = {
  equals: "Igual a",
  regex: "Expresión regular",
  present: "Que exista",
  record: "Solo registrar",
};

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `Error HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* sin cuerpo */
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function getReadingCapabilities(refresh = false): Promise<ReadingCapabilities> {
  return json(await fetch(`${API_URL}/reading/capabilities${refresh ? "?refresh=true" : ""}`, { cache: "no-store" }));
}

/** Lee códigos (y texto si `text`) dentro de un recorte de la foto. */
export async function testReading(
  photo: File,
  crop?: CropRect,
  text = false,
): Promise<{ codes: { format: string; text: string }[]; text?: { available: boolean; text: string; confidence: number; error?: string } }> {
  const form = new FormData();
  form.append("photo", photo);
  if (crop) form.append("crop", JSON.stringify(crop));
  form.append("text", String(text));
  return json(await fetch(`${API_URL}/reading/test`, { method: "POST", body: form }));
}

/** Crea una zona a partir del rectángulo dibujado y propone su valor esperado leyéndola en ese momento. */
export async function zoneFromRect(photo: File, rect: CropRect, index: number, textAvailable: boolean): Promise<ReadingZone> {
  const zone: ReadingZone = {
    id: `L${Date.now().toString(36)}${index}`,
    name: `Lectura ${index + 1}`,
    kind: "code",
    rule: "present",
    expected: "",
    pattern: "",
    ...rect,
  };
  try {
    const codes = await testReading(photo, rect, false);
    if (codes.codes.length) {
      return { ...zone, name: `${codes.codes[0].format} ${index + 1}`, rule: "equals", expected: codes.codes[0].text };
    }
    // El OCR solo se ejecuta si no hay código en la zona y está instalado.
    if (textAvailable) {
      const read = await testReading(photo, rect, true);
      if (read.text?.text) return { ...zone, kind: "text", name: `Texto ${index + 1}`, rule: "equals", expected: read.text.text };
      return { ...zone, kind: "text", name: `Texto ${index + 1}` };
    }
  } catch {
    /* se deja la zona sin valor propuesto */
  }
  return zone;
}
