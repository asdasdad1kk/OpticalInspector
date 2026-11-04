import { useMemo, useRef } from "react";

import ZoomableImage, { type ZoomableImageHandle } from "./ZoomableImage";
import { InspectionItemTooltip, StatusPill, inspectionMarkers, typeLabel } from "./FeatureInfo";
import type { InspectionItemResult } from "../types/api";

/**
 * Imagen de resultado con zoom y tooltips por feature. Los marcadores solo
 * aplican a las imagenes rectificada y de resultado (canvas de inspeccion).
 */
export default function InspectionResultView({
  url,
  alt,
  items,
  showMarkers,
  className = "h-[60vh]",
}: {
  url: string;
  alt: string;
  items: InspectionItemResult[];
  showMarkers: boolean;
  className?: string;
}) {
  const viewer = useRef<ZoomableImageHandle>(null);
  const markers = useMemo(() => (showMarkers ? inspectionMarkers(items) : []), [items, showMarkers]);
  const byKey = useMemo(() => new Map(items.map((item) => [String(item.id), item])), [items]);
  const problems = items.filter((item) => item.status !== "OK");

  return (
    <div className="space-y-3">
      <ZoomableImage
        ref={viewer}
        src={url}
        alt={alt}
        className={`rounded-xl border border-zinc-800 ${className}`}
        markers={markers}
        renderTooltip={(key) => {
          const item = byKey.get(String(key));
          return item ? <InspectionItemTooltip item={item} /> : null;
        }}
      />
      {problems.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-zinc-400">
            Features con problema ({problems.length})
            {showMarkers ? " · clic para ubicarlo en la imagen" : " · cambie a Resultado o Panel rectificado para ubicarlos"}
          </p>
          <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
            {problems.map((item) => {
              const locatable = showMarkers && markers.some((marker) => marker.key === String(item.id));
              return (
                <button
                  type="button"
                  key={String(item.id)}
                  disabled={!locatable}
                  title={(item.reasons ?? []).join("\n")}
                  onClick={() => viewer.current?.focusMarker(String(item.id))}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1 text-xs text-zinc-300 transition enabled:hover:border-zinc-600 disabled:cursor-default"
                >
                  <StatusPill status={item.status} />
                  {typeof item.id === "number" ? `#${item.id} ` : ""}
                  {typeLabel(item.type)}
                  {item.reasons?.[0] && <span className="max-w-[220px] truncate text-zinc-500">· {item.reasons[0]}</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
