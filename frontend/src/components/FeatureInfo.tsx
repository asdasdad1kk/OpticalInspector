import type { ImageMarker } from "./ZoomableImage";
import type { InspectionItemResult } from "../types/api";

export const FEATURE_TYPES: Record<string, { label: string; description: string; color: string }> = {
  INSERTO: {
    label: "Inserto",
    description: "Disco metálico gris con pestañas, más oscuro que la placa.",
    color: "#fb923c",
  },
  DELRON_CONICO: {
    label: "Delron cónico",
    description: "Cara cónica alrededor del agujero; se ve dorada y se aclara hacia el borde.",
    color: "#e879f9",
  },
  DELRON_PLANO: {
    label: "Delron plano",
    description: "Disco beige ancho y uniforme, sin cono.",
    color: "#22c55e",
  },
  BARRENO: {
    label: "Barreno",
    description: "Hueco liso, sin inserto ni delron. Incluye el hueco grande del centro.",
    color: "#22d3ee",
  },
  INSERTO_LATON: {
    label: "Inserto (latón)",
    description: "Tipo anterior; vuelva a dar de alta la pieza para clasificarlo como inserto o delron.",
    color: "#facc15",
  },
  CONTORNO: {
    label: "Contorno",
    description: "Forma y dimensiones exteriores de la pieza.",
    color: "#facc15",
  },
  DISTANCIAS: {
    label: "Distancias",
    description: "Distancias entre features vecinos.",
    color: "#a1a1aa",
  },
  ETIQUETA: {
    label: "Etiqueta",
    description: "Etiqueta de identificación de la pieza.",
    color: "#38bdf8",
  },
};

/** Tipos que se pueden asignar a mano a un feature. */
export const EDITABLE_TYPES = ["INSERTO", "DELRON_CONICO", "DELRON_PLANO", "BARRENO"] as const;

export function typeLabel(type?: string | null) {
  if (!type) return "Desconocido";
  return FEATURE_TYPES[type]?.label ?? type.replace(/_/g, " ").toLowerCase();
}

export const STATUS_COLORS: Record<string, string> = {
  OK: "#22c55e",
  REVISAR: "#f59e0b",
  FALLO: "#ef4444",
};

const STATUS_PILL: Record<string, string> = {
  OK: "bg-emerald-500/15 text-emerald-400",
  REVISAR: "bg-amber-500/15 text-amber-400",
  FALLO: "bg-red-500/15 text-red-400",
};

const CHECK_LABELS: Record<string, string> = {
  presence: "Presencia",
  position: "Posición",
  type: "Tipo",
  diameter: "Diámetro",
  iou: "Forma",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_PILL[status] ?? "bg-zinc-800 text-zinc-300"}`}>
      {status}
    </span>
  );
}

/** Marcadores sobre la imagen rectificada / resultado (mismas coordenadas). */
export function inspectionMarkers(items: InspectionItemResult[]): ImageMarker[] {
  return items
    .filter((item) => item.center_x_px != null && item.center_y_px != null && item.kind !== "contour")
    .map((item) => {
      const radius = item.box
        ? Math.max(item.box[2] - item.box[0], item.box[3] - item.box[1]) / 2
        : (item.expected_radius_px ?? 10) * 1.5;
      return {
        key: String(item.id),
        x: item.center_x_px!,
        y: item.center_y_px!,
        r: Math.max(radius, 8),
        color: STATUS_COLORS[item.status] ?? "#a1a1aa",
      };
    });
}

export function InspectionItemTooltip({ item }: { item: InspectionItemResult }) {
  const info = FEATURE_TYPES[item.type];
  const checks = Object.entries(item.checks ?? {});
  const wrongType = item.observed_type && item.observed_type !== item.type;
  return (
    <div className="space-y-2 pr-4">
      <div className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: info?.color ?? "#a1a1aa" }} />
        <span className="font-semibold">
          {typeof item.id === "number" ? `#${item.id} · ` : ""}
          {typeLabel(item.type)}
        </span>
        <StatusPill status={item.status} />
      </div>
      {info && <p className="text-zinc-400">{info.description}</p>}
      {wrongType && (
        <p className="rounded bg-red-500/10 px-2 py-1 text-red-300">
          Se esperaba <b>{typeLabel(item.type)}</b> y se observa <b>{typeLabel(item.observed_type)}</b>.
        </p>
      )}
      {checks.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {checks.map(([key, value]) => (
            <span key={key} className="inline-flex items-center gap-1 rounded bg-zinc-900 px-1.5 py-0.5 text-[10px]">
              {CHECK_LABELS[key] ?? key}
              <span style={{ color: STATUS_COLORS[value] }}>{value}</span>
            </span>
          ))}
        </div>
      )}
      {item.status !== "OK" && item.reasons && item.reasons.length > 0 && (
        <div>
          <p className="mb-1 font-medium text-zinc-300">{item.status === "FALLO" ? "Por qué falló" : "Por qué revisar"}</p>
          <ul className="list-disc space-y-0.5 pl-4 text-zinc-300">
            {item.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-zinc-500">
        <span>Score {Number(item.score ?? 0).toFixed(0)}</span>
        {item.deviation != null && <span>Desviación {item.deviation.toFixed(3)}</span>}
        {item.radius_ratio != null && <span>Diámetro {(item.radius_ratio * 100).toFixed(0)}%</span>}
      </div>
    </div>
  );
}
