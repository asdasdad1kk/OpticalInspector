import { Barcode, ScanText, Trash2 } from "lucide-react";
import { RULE_LABEL, type ReadingResult, type ReadingRule, type ReadingZone } from "../lib/readingApi";

const inputClass =
  "w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs outline-none placeholder:text-zinc-700 focus:border-emerald-500";

const STATUS_STYLE: Record<string, string> = {
  OK: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
  REVISAR: "border-amber-500/40 bg-amber-500/10 text-amber-400",
  FALLO: "border-red-500/40 bg-red-500/10 text-red-400",
  OMITIDO: "border-zinc-700 bg-zinc-800 text-zinc-400",
};

/** Rectángulos de las zonas para el `overlay` de ZoomableImage (coordenadas naturales de la imagen). */
export function ReadingZonesOverlay({ zones, offset = { x: 0, y: 0 } }: { zones: ReadingZone[]; offset?: { x: number; y: number } }) {
  return (
    <>
      {zones.map((zone) => (
        <g key={zone.id} pointerEvents="none">
          <rect
            x={zone.x + offset.x}
            y={zone.y + offset.y}
            width={zone.w}
            height={zone.h}
            fill="rgba(56,189,248,0.10)"
            stroke="#38bdf8"
            strokeWidth={2}
            strokeDasharray="6 4"
            vectorEffect="non-scaling-stroke"
          />
          <text x={zone.x + offset.x + 4} y={zone.y + offset.y - 6} fill="#38bdf8" fontSize={Math.max(14, zone.h * 0.18)} fontWeight={700}>
            {zone.name}
          </text>
        </g>
      ))}
    </>
  );
}

/** Lista editable de zonas de lectura (nombre, tipo, regla y valor esperado). */
export function ReadingZonesEditor({
  zones,
  onChange,
  textAvailable,
}: {
  zones: ReadingZone[];
  onChange: (zones: ReadingZone[]) => void;
  textAvailable: boolean;
}) {
  function update(id: string, patch: Partial<ReadingZone>) {
    onChange(zones.map((zone) => (zone.id === id ? { ...zone, ...patch } : zone)));
  }
  if (!zones.length) return null;
  return (
    <div className="space-y-2">
      {zones.map((zone) => (
        <div key={zone.id} className="space-y-2 rounded-lg border border-sky-500/20 bg-sky-500/5 p-2">
          <div className="flex items-center gap-2">
            {zone.kind === "code" ? <Barcode size={15} className="shrink-0 text-sky-400" /> : <ScanText size={15} className="shrink-0 text-sky-400" />}
            <input className={inputClass} value={zone.name} maxLength={60} onChange={(event) => update(zone.id, { name: event.target.value })} />
            <button
              type="button"
              title="Quitar zona"
              onClick={() => onChange(zones.filter((item) => item.id !== zone.id))}
              className="shrink-0 rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-red-400"
            >
              <Trash2 size={14} />
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <select className={inputClass} value={zone.kind} onChange={(event) => update(zone.id, { kind: event.target.value as ReadingZone["kind"] })}>
              <option value="code">Código / QR</option>
              <option value="text" disabled={!textAvailable}>
                Texto (OCR){textAvailable ? "" : " — no instalado"}
              </option>
            </select>
            <select className={inputClass} value={zone.rule} onChange={(event) => update(zone.id, { rule: event.target.value as ReadingRule })}>
              {(Object.keys(RULE_LABEL) as ReadingRule[]).map((rule) => (
                <option key={rule} value={rule}>
                  {RULE_LABEL[rule]}
                </option>
              ))}
            </select>
          </div>
          {zone.rule === "equals" && (
            <input className={inputClass} placeholder="Valor esperado" value={zone.expected} onChange={(event) => update(zone.id, { expected: event.target.value })} />
          )}
          {zone.rule === "regex" && (
            <input className={inputClass} placeholder="Ej. ^PN-[0-9]{4}$" value={zone.pattern} onChange={(event) => update(zone.id, { pattern: event.target.value })} />
          )}
        </div>
      ))}
    </div>
  );
}

/** Resultado de las lecturas de una inspección. */
export function ReadingResultsList({ results, imageUrl }: { results: ReadingResult[]; imageUrl?: (id: string) => string | undefined }) {
  if (!results.length) return null;
  return (
    <div className="space-y-2">
      {results.map((result) => {
        const url = imageUrl?.(result.id);
        return (
          <div key={result.id} className="flex gap-3 rounded-xl border border-zinc-800 bg-zinc-950 p-3">
            {url && <img src={url} alt={result.name} className="h-16 w-24 shrink-0 rounded bg-black object-contain" />}
            <div className="min-w-0 flex-1 space-y-1 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-semibold text-zinc-200">
                  {result.name}
                  {result.format ? <span className="ml-1 font-normal text-zinc-500">({result.format})</span> : null}
                </span>
                <span className={`shrink-0 rounded-full border px-2 py-0.5 font-semibold ${STATUS_STYLE[result.status] ?? STATUS_STYLE.OMITIDO}`}>{result.status}</span>
              </div>
              <p className="break-all text-zinc-300">
                Leído: <b className="font-mono">{result.value || "—"}</b>
                {result.confidence != null && <span className="text-zinc-500"> · confianza {Math.round(result.confidence)}%</span>}
              </p>
              {result.expected && (
                <p className="break-all text-zinc-500">
                  {result.rule === "regex" ? "Patrón" : "Esperado"}: <span className="font-mono">{result.expected}</span>
                </p>
              )}
              {result.status !== "OK" && <p className="text-zinc-400">{result.reason}</p>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
