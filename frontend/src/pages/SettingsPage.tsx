import { useCallback, useEffect, useState } from "react";
import { getReadingCapabilities, testReading, type ReadingCapabilities } from "../lib/readingApi";
import {
  Camera,
  CheckCircle2,
  Database,
  Download,
  FlaskConical,
  KeyRound,
  LoaderCircle,
  Lock,
  LogOut,
  Play,
  CircleStop,
  RotateCcw,
  Ruler,
  SlidersHorizontal,
  Trash2,
  Upload,
  XCircle,
  ScanBarcode,
} from "lucide-react";

import {
  cfgBlob,
  cfgJson,
  cfgSend,
  cfgUpload,
  configSession,
  login,
  UnauthorizedError,
  type CalibrationInfo,
  type ConfigSettings,
  type PartRow,
  type ReadingSettings,
  type StorageStatus,
  type ValidationEntry,
  type ValidationRun,
} from "../lib/configApi";
import { getCameraStatus, startCamera, stopCamera } from "../lib/api";
import { useCameraStream } from "../hooks/useCameraStream";
import { resetCameraOptions } from "../components/CameraSelector";
import type { CameraStatus } from "../types/api";

// ============================================================
// ESTILOS Y UTILIDADES
// ============================================================

const input =
  "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/15";
const primary =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-zinc-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40";
const secondary =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm font-semibold transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40";
const card = "rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5";

type Guard = <T>(action: () => Promise<T>, okMessage?: string) => Promise<T | undefined>;

function formatBytes(bytes?: number | null) {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function NumberField({
  label,
  hint,
  value,
  step = 0.05,
  placeholder,
  onChange,
}: {
  label: string;
  hint?: string;
  value: number | undefined;
  step?: number;
  placeholder?: string;
  onChange: (value: number | undefined) => void;
}) {
  const [text, setText] = useState(value === undefined ? "" : String(value));
  useEffect(() => setText(value === undefined ? "" : String(value)), [value]);
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-zinc-300">{label}</span>
      <input
        type="number"
        step={step}
        value={text}
        placeholder={placeholder}
        className={input}
        onChange={(event) => {
          setText(event.target.value);
          const parsed = parseFloat(event.target.value);
          onChange(Number.isFinite(parsed) ? parsed : undefined);
        }}
      />
      {hint && <span className="mt-1 block text-[11px] text-zinc-600">{hint}</span>}
    </label>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-300">
      <input type="checkbox" className="accent-emerald-500" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

// ============================================================
// ACCESO
// ============================================================

interface ResetResult {
  camera_restarted: boolean | null;
  camera_error: string | null;
}

/** Restablece la configuracion de fabrica; las piezas, inspecciones y la clave no se tocan. */
function ResetDialog({
  guard, onClose, onDone,
}: { guard: Guard; onClose: () => void; onDone: (result: ResetResult) => void }) {
  const [options, setOptions] = useState({ general: true, parts: true, calibration: true });
  const [busy, setBusy] = useState(false);
  const nothing = !options.general && !options.parts && !options.calibration;

  async function confirm() {
    setBusy(true);
    const result = await guard(() => cfgSend<ResetResult>("/reset", "POST", options));
    setBusy(false);
    if (result) {
      if (options.general) resetCameraOptions();
      onDone(result);
    }
  }

  const choices: { key: keyof typeof options; title: string; detail: string }[] = [
    {
      key: "general",
      title: "Ajustes generales",
      detail: "Tolerancias y compuertas de calidad, parámetros fijos de la cámara (exposición, enfoque), DXF, respaldos y la cámara/resolución elegida en este equipo.",
    },
    { key: "parts", title: "Ajustes por número de parte", detail: "Las tolerancias propias de cada pieza vuelven a las generales." },
    { key: "calibration", title: "Calibración", detail: "Calibración del lente y escala del plano: habrá que volver a calibrar." },
  ];

  return (
    <div onClick={onClose} className="fixed inset-0 z-[100] grid place-items-center bg-black/80 p-4 backdrop-blur-sm">
      <div onClick={(event) => event.stopPropagation()} className="w-full max-w-lg rounded-2xl border border-zinc-700 bg-zinc-950 p-6 shadow-2xl">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <RotateCcw size={18} className="text-red-400" /> Restablecer configuración
        </h2>
        <p className="mt-2 text-sm text-zinc-400">
          Vuelve a los valores de fábrica. <b className="text-zinc-200">No</b> borra las piezas dadas de alta, las inspecciones ni la clave
          de configuración. Si la cámara está encendida se reinicia para aplicar los cambios.
        </p>
        <div className="mt-4 space-y-2">
          {choices.map((choice) => (
            <label key={choice.key} className="flex cursor-pointer items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 accent-red-500"
                checked={options[choice.key]}
                onChange={(event) => setOptions((current) => ({ ...current, [choice.key]: event.target.checked }))}
              />
              <span>
                <span className="font-medium text-zinc-100">{choice.title}</span>
                <span className="mt-0.5 block text-xs text-zinc-500">{choice.detail}</span>
              </span>
            </label>
          ))}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className={secondary} onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={busy || nothing}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-red-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-400 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? <LoaderCircle size={16} className="animate-spin" /> : <RotateCcw size={16} />}
            Restablecer
          </button>
        </div>
      </div>
    </div>
  );
}

function LoginGate({ onSuccess }: { onSuccess: (defaultPassword: boolean) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    try {
      setBusy(true);
      setError(undefined);
      const result = await login(password);
      onSuccess(result.default_password);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No fue posible iniciar sesión.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className={`${card} mx-auto mt-16 w-full max-w-sm space-y-4`}>
      <div className="flex items-center gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-500/10 text-emerald-400">
          <Lock size={20} />
        </div>
        <div>
          <h1 className="font-semibold">Configuración</h1>
          <p className="text-xs text-zinc-500">Ingrese la clave de configuración.</p>
        </div>
      </div>
      <input
        type="password"
        autoFocus
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        placeholder="Clave"
        className={input}
      />
      {error && <p className="text-xs text-red-400">{error}</p>}
      <button type="submit" className={`${primary} w-full`} disabled={!password || busy}>
        {busy ? <LoaderCircle size={16} className="animate-spin" /> : <KeyRound size={16} />}
        Entrar
      </button>
    </form>
  );
}

// ============================================================
// PESTAÑA: INSPECCION
// ============================================================

const INSPECTION_FIELDS: { key: string; label: string; hint: string; step?: number }[] = [
  { key: "position_ok_radii", label: "Posición OK (radios)", hint: "Desviación máxima aceptada, en radios del feature." },
  { key: "position_review_radii", label: "Posición REVISAR hasta (radios)", hint: "Entre OK y este valor = REVISAR; más = FALLO." },
  { key: "diameter_review_ratio", label: "Tolerancia de diámetro", hint: "Fracción (0.35 = ±35%)." },
  { key: "brass_saturation", label: "Saturación mínima de latón", hint: "0–255. Separa latón de acero por color.", step: 1 },
  { key: "min_presence", label: "Presencia mínima", hint: "0–1. Bajo este valor el feature se considera ausente." },
  { key: "dimension_tolerance_pct", label: "Tolerancia de dimensiones (%)", hint: "Largo y ancho del contorno (pieza real)." },
  { key: "distance_tolerance_pct", label: "Tolerancia de distancias (%)", hint: "Distancias entre features (pieza real)." },
  { key: "min_inlier_fraction", label: "Coincidencia mínima de alineación", hint: "0–1. Bajo este valor: NO CONCLUYENTE." },
  { key: "min_sharpness", label: "Nitidez mínima", hint: "Varianza del Laplaciano. Bajo este valor: NO CONCLUYENTE.", step: 1 },
  { key: "min_brightness", label: "Brillo mínimo", hint: "Promedio de gris (0–255).", step: 1 },
  { key: "max_brightness", label: "Brillo máximo", hint: "Promedio de gris (0–255).", step: 1 },
  { key: "max_clipped_fraction", label: "Píxeles saturados máx.", hint: "0–1. Reflejos o subexposición." },
];

const CHECKS: [string, string][] = [
  ["check_position", "Posición"],
  ["check_type", "Tipo / material"],
  ["check_diameter", "Diámetro"],
  ["check_edges", "Bordes CAD"],
];

function InspectionTab({ settings, guard, reload }: { settings: ConfigSettings; guard: Guard; reload: () => void }) {
  const [values, setValues] = useState<Record<string, number | boolean>>(settings.inspection);
  useEffect(() => setValues(settings.inspection), [settings]);

  return (
    <section className={card}>
      <h2 className="font-semibold">Tolerancias y compuertas de calidad (globales)</h2>
      <p className="mt-1 text-xs text-zinc-500">
        Valen para todas las piezas; cada número de parte puede sobrescribirlos en la pestaña Piezas.
      </p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {INSPECTION_FIELDS.map((field) => (
          <NumberField
            key={field.key}
            label={field.label}
            hint={field.hint}
            step={field.step}
            value={values[field.key] as number}
            onChange={(value) => value !== undefined && setValues((current) => ({ ...current, [field.key]: value }))}
          />
        ))}
      </div>
      <div className="mt-5 flex flex-wrap gap-5 border-t border-zinc-800 pt-4">
        {CHECKS.map(([key, label]) => (
          <Toggle key={key} label={label} checked={Boolean(values[key])} onChange={(value) => setValues((c) => ({ ...c, [key]: value }))} />
        ))}
      </div>
      <button
        type="button"
        className={`${primary} mt-5`}
        onClick={() => guard(async () => { await cfgSend("/settings/inspection", "PUT", values); reload(); }, "Ajustes de inspección guardados.")}
      >
        <CheckCircle2 size={16} /> Guardar
      </button>
    </section>
  );
}

// ============================================================
// PESTAÑA: CAMARA Y CALIBRACION
// ============================================================

function CameraTab({ settings, guard, reload }: { settings: ConfigSettings; guard: Guard; reload: () => void }) {
  const [lock, setLock] = useState(settings.camera);
  const [calibrationCfg, setCalibrationCfg] = useState(settings.calibration);
  const [camera, setCamera] = useState<CameraStatus>();
  const [info, setInfo] = useState<{ calibration: CalibrationInfo | null; session: { frames: number; required: number } }>();
  const [lastFrame, setLastFrame] = useState<string>();
  const stream = useCameraStream(Boolean(camera?.running));

  useEffect(() => setLock(settings.camera), [settings]);
  useEffect(() => setCalibrationCfg(settings.calibration), [settings]);

  const refresh = useCallback(async () => {
    await guard(async () => setInfo(await cfgJson("/calibration")));
  }, [guard]);

  useEffect(() => {
    getCameraStatus().then(setCamera).catch(() => undefined);
    refresh();
  }, [refresh]);

  async function downloadBoard() {
    const blob = await cfgBlob("/calibration/board.png");
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `tablero_charuco_${calibrationCfg.square_mm}mm.png`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const calibration = info?.calibration;

  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <section className={card}>
        <h2 className="font-semibold">Parámetros fijos de la cámara</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Evitan que la exposición y el balance de blancos automáticos cambien el color (latón vs acero). Se aplican al
          iniciar la cámara.
        </p>
        <div className="mt-4 space-y-4">
          <Toggle label="Bloquear exposición, balance de blancos y enfoque" checked={lock.lock_enabled} onChange={(value) => setLock({ ...lock, lock_enabled: value })} />
          <div className="grid gap-4 sm:grid-cols-2">
            <NumberField label="Exposición (log2 s)" hint="BRIO: -6 ≈ 1/64 s. Más negativo = más oscuro." step={1} value={lock.exposure} onChange={(v) => v !== undefined && setLock({ ...lock, exposure: v })} />
            <NumberField label="Temperatura de blancos (K)" hint="Fije el valor que dé blancos neutros." step={100} value={lock.wb_temperature} onChange={(v) => v !== undefined && setLock({ ...lock, wb_temperature: v })} />
            <NumberField label="Enfoque manual (0–255)" hint="Solo si el autoenfoque está apagado." step={5} value={lock.focus} onChange={(v) => v !== undefined && setLock({ ...lock, focus: v })} />
          </div>
          <Toggle label="Autoenfoque activo" checked={lock.autofocus} onChange={(value) => setLock({ ...lock, autofocus: value })} />
          <button type="button" className={primary} onClick={() => guard(async () => { await cfgSend("/settings/camera", "PUT", lock); reload(); }, "Parámetros de cámara guardados. Reinicie la cámara para aplicarlos.")}>
            <CheckCircle2 size={16} /> Guardar
          </button>
        </div>
      </section>

      <section className={card}>
        <h2 className="flex items-center gap-2 font-semibold"><Ruler size={17} /> Calibración del lente (ChArUco)</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Corrige la distorsión del lente en cada imagen. Imprima el tablero al 100 % (sin escalar), péguelo en una
          superficie plana y capture al menos 8 fotogramas variando posición e inclinación.
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <NumberField label="Lado del cuadro impreso (mm)" hint="Mídalo con calibrador tras imprimir." step={0.1} value={calibrationCfg.square_mm} onChange={(v) => v !== undefined && setCalibrationCfg({ ...calibrationCfg, square_mm: v })} />
          <div className="flex items-end">
            <Toggle label="Aplicar calibración al inspeccionar" checked={calibrationCfg.apply} onChange={(value) => setCalibrationCfg({ ...calibrationCfg, apply: value })} />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className={secondary} onClick={() => guard(async () => { await cfgSend("/settings/calibration", "PUT", calibrationCfg); reload(); }, "Ajustes de calibración guardados.")}>
            <CheckCircle2 size={16} /> Guardar
          </button>
          <button type="button" className={secondary} onClick={() => guard(downloadBoard)}>
            <Download size={16} /> Descargar tablero
          </button>
        </div>

        <div className="mt-5 border-t border-zinc-800 pt-4">
          <div className="flex flex-wrap gap-2">
            <button type="button" className={secondary} disabled={camera?.running} onClick={() => guard(async () => setCamera(await startCamera()))}>
              <Play size={16} /> Iniciar cámara
            </button>
            <button type="button" className={secondary} disabled={!camera?.running} onClick={() => guard(async () => setCamera(await stopCamera()))}>
              <CircleStop size={16} /> Detener
            </button>
          </div>
          <div className="mt-3 aspect-video overflow-hidden rounded-lg bg-black">
            {stream.imageUrl && camera?.running ? (
              <img src={stream.imageUrl} alt="Vista en vivo" className="h-full w-full object-contain" />
            ) : (
              <div className="grid h-full place-items-center text-xs text-zinc-600">Cámara detenida</div>
            )}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button type="button" className={primary} disabled={!camera?.running}
              onClick={() => guard(async () => {
                const result = await cfgSend<{ message: string }>("/calibration/frame", "POST");
                setLastFrame(result.message);
                await refresh();
              })}>
              <Camera size={16} /> Capturar fotograma ({info?.session.frames ?? 0}/{info?.session.required ?? 8})
            </button>
            <button type="button" className={secondary} disabled={(info?.session.frames ?? 0) < (info?.session.required ?? 8)}
              onClick={() => guard(async () => { await cfgSend("/calibration/compute", "POST"); await refresh(); }, "Calibración calculada y guardada.")}>
              Calcular
            </button>
            <button type="button" className={secondary} onClick={() => guard(async () => { await cfgSend("/calibration/reset-session", "POST"); setLastFrame(undefined); await refresh(); })}>
              Reiniciar
            </button>
          </div>
          {lastFrame && <p className="mt-2 text-xs text-zinc-400">{lastFrame}</p>}
        </div>

        <div className="mt-5 border-t border-zinc-800 pt-4 text-sm">
          {calibration ? (
            <>
              <p className="flex items-center gap-2">
                <span className={`rounded px-2 py-0.5 text-xs font-semibold ${calibration.quality === "BUENA" ? "bg-emerald-500/15 text-emerald-400" : calibration.quality === "ACEPTABLE" ? "bg-amber-500/15 text-amber-400" : "bg-red-500/15 text-red-400"}`}>
                  {calibration.quality}
                </span>
                Error de reproyección {calibration.rms.toFixed(2)} px · {calibration.frames_used} fotogramas · {calibration.image_size.join("×")}
              </p>
              <p className="mt-1 text-xs text-zinc-500">
                Calibrada el {calibration.calibrated_at}. Es válida solo para esta resolución.
                {calibration.plane_mm_per_px ? ` Escala del plano: ${calibration.plane_mm_per_px.toFixed(4)} mm/px.` : ""}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" className={secondary} disabled={!camera?.running}
                  onClick={() => guard(async () => { await cfgSend("/calibration/plane-scale", "POST"); await refresh(); }, "Escala del plano guardada. Se usará en el alta por pieza real sin dimensión conocida.")}>
                  <Ruler size={16} /> Medir escala en el plano de la pieza
                </button>
                <button type="button" className={secondary} onClick={() => guard(async () => { await cfgJson("/calibration", { method: "DELETE" }); await refresh(); })}>
                  <Trash2 size={16} /> Eliminar calibración
                </button>
              </div>
              <p className="mt-2 text-[11px] text-zinc-600">
                Para medir la escala coloque el tablero sobre la superficie donde irá la pieza, a la misma distancia de la cámara.
              </p>
            </>
          ) : (
            <p className="text-xs text-zinc-500">Sin calibración: las imágenes se usan sin corregir.</p>
          )}
        </div>
      </section>
    </div>
  );
}

// ============================================================
// PESTAÑA: PIEZAS (override, piezas buenas, sets de validacion)
// ============================================================

const OVERRIDE_KEYS: { key: string; label: string }[] = [
  { key: "position_ok_radii", label: "Posición OK (radios)" },
  { key: "position_review_radii", label: "Posición REVISAR (radios)" },
  { key: "dimension_tolerance_pct", label: "Dimensiones (%)" },
  { key: "distance_tolerance_pct", label: "Distancias (%)" },
  { key: "min_inlier_fraction", label: "Coincidencia mínima" },
  { key: "brass_saturation", label: "Saturación latón" },
  { key: "min_sharpness", label: "Nitidez mínima" },
];

function PartDetail({ part, settings, guard, reload }: { part: PartRow; settings: ConfigSettings; guard: Guard; reload: () => void }) {
  const [overrides, setOverrides] = useState<Record<string, number | undefined>>(part.overrides as Record<string, number>);
  const [entries, setEntries] = useState<ValidationEntry[]>([]);
  const [expected, setExpected] = useState<"OK" | "FALLO">("OK");
  const [note, setNote] = useState("");
  const [run, setRun] = useState<ValidationRun>();
  const [busy, setBusy] = useState(false);

  const loadEntries = useCallback(async () => {
    await guard(async () => setEntries(await cfgJson(`/validation/${encodeURIComponent(part.part_number)}`)));
  }, [guard, part.part_number]);

  useEffect(() => {
    setOverrides(part.overrides as Record<string, number>);
    setRun(undefined);
    loadEntries();
  }, [part, loadEntries]);

  async function upload(path: string, file: File, extra?: Record<string, string>, okMessage?: string) {
    const form = new FormData();
    form.append("photo", file);
    Object.entries(extra ?? {}).forEach(([key, value]) => form.append(key, value));
    setBusy(true);
    await guard(async () => { await cfgUpload(path, form); }, okMessage);
    setBusy(false);
  }

  return (
    <div className="space-y-6">
      <section className={card}>
        <h3 className="font-semibold">Tolerancias propias de {part.part_number}</h3>
        <p className="mt-1 text-xs text-zinc-500">Deje vacío para usar el valor global.</p>
        <div className="mt-4 grid gap-4 sm:grid-cols-3 xl:grid-cols-4">
          {OVERRIDE_KEYS.map((field) => (
            <NumberField
              key={field.key}
              label={field.label}
              value={overrides[field.key]}
              placeholder={String(settings.inspection[field.key])}
              onChange={(value) => setOverrides((current) => ({ ...current, [field.key]: value }))}
            />
          ))}
        </div>
        <button type="button" className={`${primary} mt-4`}
          onClick={() => guard(async () => {
            const clean = Object.fromEntries(Object.entries(overrides).filter(([, v]) => v !== undefined));
            await cfgSend(`/parts/${encodeURIComponent(part.part_number)}/overrides`, "PUT", clean);
            reload();
          }, "Tolerancias de la pieza guardadas.")}>
          <CheckCircle2 size={16} /> Guardar
        </button>
      </section>

      {part.source === "golden" && part.samples && (
        <section className={card}>
          <h3 className="font-semibold">Piezas buenas (patrón real)</h3>
          <p className="mt-1 text-xs text-zinc-500">
            {part.samples.sample_count} pieza(s) en total. Con 3 o más, la posición de cada feature se promedia y las
            tolerancias se amplían a 3–5 veces la variación observada
            {part.samples.max_position_std != null ? ` (dispersión máxima ${part.samples.max_position_std.toFixed(2)} mm)` : ""}.
            Una pieza con fallos no se acepta como buena.
          </p>
          <ul className="mt-3 space-y-1 text-xs">
            {part.samples.samples.map((sample) => (
              <li key={sample.id} className="flex items-center justify-between rounded border border-zinc-800 bg-zinc-950/60 px-3 py-1.5">
                <span>{sample.added} · puntuación {sample.score?.toFixed(1) ?? "—"}</span>
                <button type="button" aria-label="Eliminar muestra" className="text-zinc-500 hover:text-red-400"
                  onClick={() => guard(async () => { await cfgJson(`/drawings/${part.drawing_id}/samples/${sample.id}`, { method: "DELETE" }); reload(); }, "Muestra eliminada.")}>
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
          <label className={`${secondary} mt-3 cursor-pointer`}>
            {busy ? <LoaderCircle size={16} className="animate-spin" /> : <Upload size={16} />} Agregar pieza buena (foto)
            <input type="file" accept="image/*" className="hidden" disabled={busy}
              onChange={async (event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) { await upload(`/drawings/${part.drawing_id}/samples`, file, undefined, "Pieza buena agregada."); reload(); }
              }} />
          </label>
        </section>
      )}

      <section className={card}>
        <h3 className="flex items-center gap-2 font-semibold"><FlaskConical size={17} /> Set de validación</h3>
        <p className="mt-1 text-xs text-zinc-500">
          Imágenes etiquetadas como buena (OK) o mala (FALLO). Ejecútelo tras cambiar tolerancias o calibración: un
          “falso OK” (pieza mala aprobada) es el error crítico.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <select value={expected} onChange={(e) => setExpected(e.target.value as "OK" | "FALLO")} className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm">
            <option value="OK">Debe dar OK</option>
            <option value="FALLO">Debe fallar</option>
          </select>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Nota (opcional)" className={`${input} max-w-xs`} />
          <label className={`${secondary} cursor-pointer`}>
            <Upload size={16} /> Agregar imagen
            <input type="file" accept="image/*" className="hidden"
              onChange={async (event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) { await upload(`/validation/${encodeURIComponent(part.part_number)}`, file, { expected, note }); setNote(""); await loadEntries(); reload(); }
              }} />
          </label>
          <button type="button" className={primary} disabled={!entries.length || busy}
            onClick={() => guard(async () => setRun(await cfgSend<ValidationRun>(`/validation/${encodeURIComponent(part.part_number)}/run`, "POST")))}>
            <Play size={16} /> Ejecutar set
          </button>
        </div>
        {entries.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-2 text-xs">
            {entries.map((entry) => (
              <li key={entry.id} className="flex items-center gap-2 rounded border border-zinc-800 bg-zinc-950/60 px-2.5 py-1">
                <span className={entry.expected === "OK" ? "text-emerald-400" : "text-red-400"}>{entry.expected}</span>
                <span className="text-zinc-400">{entry.note || entry.file}</span>
                <button type="button" aria-label="Eliminar imagen" className="text-zinc-500 hover:text-red-400"
                  onClick={() => guard(async () => { await cfgJson(`/validation/${encodeURIComponent(part.part_number)}/${entry.id}`, { method: "DELETE" }); await loadEntries(); reload(); })}>
                  <Trash2 size={13} />
                </button>
              </li>
            ))}
          </ul>
        )}
        {run && (
          <div className="mt-4 border-t border-zinc-800 pt-4">
            <p className={`flex items-center gap-2 text-sm font-semibold ${run.passed ? "text-emerald-400" : "text-red-400"}`}>
              {run.passed ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
              {run.correct}/{run.total} correctas · falsos OK: {run.false_ok} · falsos rechazos: {run.false_reject} · no concluyentes: {run.inconclusive}
            </p>
            <table className="mt-2 w-full text-left text-xs">
              <thead className="text-zinc-500"><tr><th className="py-1">Esperado</th><th>Resultado</th><th>Puntuación</th><th>Veredicto</th><th>Nota</th></tr></thead>
              <tbody>
                {run.rows.map((row) => (
                  <tr key={row.id} className="border-t border-zinc-800">
                    <td className="py-1">{row.expected}</td>
                    <td>{row.status}</td>
                    <td>{row.score?.toFixed(1) ?? "—"}</td>
                    <td className={row.outcome === "CORRECTO" ? "text-emerald-400" : row.outcome === "FALSO_OK" ? "font-semibold text-red-400" : "text-amber-400"}>{row.outcome}</td>
                    <td className="text-zinc-500">{row.note || row.detail || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function PartsTab({ settings, guard, reloadSettings }: { settings: ConfigSettings; guard: Guard; reloadSettings: () => void }) {
  const [parts, setParts] = useState<PartRow[]>([]);
  const [selected, setSelected] = useState<string>();

  const load = useCallback(async () => {
    await guard(async () => setParts(await cfgJson<PartRow[]>("/parts")));
  }, [guard]);

  useEffect(() => { load(); }, [load]);

  const current = parts.find((part) => part.part_number === selected);

  return (
    <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
      <section className={`${card} h-fit`}>
        <h2 className="font-semibold">Números de parte</h2>
        <ul className="mt-3 space-y-1.5">
          {parts.map((part) => (
            <li key={part.part_number}>
              <button type="button" onClick={() => setSelected(part.part_number)}
                className={`w-full rounded-lg border px-3 py-2 text-left text-sm transition ${selected === part.part_number ? "border-emerald-500/50 bg-emerald-500/10" : "border-zinc-800 bg-zinc-950/60 hover:bg-zinc-800"}`}>
                <span className="font-medium">{part.part_number}</span>
                <span className="ml-2 text-xs text-zinc-500">rev {part.revision}</span>
                <span className="mt-0.5 block text-[11px] text-zinc-500">
                  {part.source === "golden" ? `Pieza real · ${part.sample_count} buena(s)` : "DXF"} · {part.features} features
                  {Object.keys(part.overrides).length ? " · ajustes propios" : ""}
                </span>
              </button>
            </li>
          ))}
          {!parts.length && <li className="text-xs text-zinc-500">Sin piezas dadas de alta.</li>}
        </ul>
      </section>
      {current ? (
        <PartDetail key={current.part_number} part={current} settings={settings} guard={guard} reload={() => { load(); reloadSettings(); }} />
      ) : (
        <p className="text-sm text-zinc-500">Seleccione un número de parte.</p>
      )}
    </div>
  );
}

// ============================================================
// PESTAÑA: ALMACENAMIENTO Y SEGURIDAD
// ============================================================

function StorageTab({ settings, guard, reload, onLogout }: { settings: ConfigSettings; guard: Guard; reload: () => void; onLogout: () => void }) {
  const [status, setStatus] = useState<StorageStatus>();
  const [backup, setBackup] = useState(settings.backup);
  const [unit, setUnit] = useState(settings.dxf.default_unit);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");

  const load = useCallback(async () => {
    await guard(async () => setStatus(await cfgJson<StorageStatus>("/storage")));
  }, [guard]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setBackup(settings.backup); setUnit(settings.dxf.default_unit); }, [settings]);

  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <section className={`${card} xl:col-span-2`}>
        <h2 className="flex items-center gap-2 font-semibold"><Database size={17} /> Rutas de archivos</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Se definen en <code className="text-zinc-300">{status?.env_file ?? ".env"}</code> (variables OI_*). Acepta rutas de red
          para usar un servidor de archivos; los cambios requieren reiniciar la aplicación. Servidor: {status?.host}:{status?.port}.
        </p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-zinc-500"><tr><th className="py-1">Variable</th><th>Ruta</th><th>Archivos</th><th>Tamaño</th><th>Libre</th></tr></thead>
            <tbody>
              {status?.paths.map((entry) => (
                <tr key={entry.key} className="border-t border-zinc-800">
                  <td className="py-1.5 font-mono text-zinc-400">{entry.key}</td>
                  <td className="break-all">{entry.path}{!entry.exists && <span className="ml-2 text-red-400">no existe</span>}</td>
                  <td>{entry.files ?? "—"}</td>
                  <td>{formatBytes(entry.bytes)}</td>
                  <td>{formatBytes(entry.free_bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!!status?.legacy_inspections && (
          <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-300">
            {status.legacy_inspections} inspección(es) antigua(s) guardan sus imágenes dentro de la base de datos.
            <button type="button" className={secondary} onClick={() => guard(async () => { await cfgSend("/storage/migrate-images", "POST"); await load(); }, "Imágenes movidas a disco.")}>
              Mover a disco
            </button>
          </div>
        )}
      </section>

      <section className={card}>
        <h2 className="font-semibold">Respaldos de la base de datos</h2>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <NumberField label="Conservar últimos" step={1} value={backup.keep} onChange={(v) => v !== undefined && setBackup({ ...backup, keep: Math.round(v) })} />
          <NumberField label="Cada (horas)" step={1} value={backup.interval_hours} onChange={(v) => v !== undefined && setBackup({ ...backup, interval_hours: v })} />
        </div>
        <div className="mt-3"><Toggle label="Respaldo automático" checked={backup.enabled} onChange={(value) => setBackup({ ...backup, enabled: value })} /></div>
        <div className="mt-3 flex gap-2">
          <button type="button" className={primary} onClick={() => guard(async () => { await cfgSend("/settings/backup", "PUT", backup); reload(); }, "Ajustes de respaldo guardados.")}><CheckCircle2 size={16} /> Guardar</button>
          <button type="button" className={secondary} onClick={() => guard(async () => { await cfgSend("/storage/backup", "POST"); await load(); }, "Respaldo creado.")}>Respaldar ahora</button>
        </div>
        <ul className="mt-3 max-h-40 space-y-1 overflow-y-auto text-xs text-zinc-400">
          {status?.backups.map((item) => <li key={item.name}>{item.modified} · {item.name} · {formatBytes(item.size)}</li>)}
          {!status?.backups.length && <li className="text-zinc-600">Sin respaldos todavía.</li>}
        </ul>
      </section>

      <div className="space-y-6">
        <section className={card}>
          <h2 className="font-semibold">DXF</h2>
          <p className="mt-1 text-xs text-zinc-500">Unidad que se asume cuando el DXF no la declara (se convierte a mm al dar de alta).</p>
          <div className="mt-3 flex gap-2">
            <select value={unit} onChange={(e) => setUnit(e.target.value)} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm">
              {settings.dxf_units.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
            <button type="button" className={secondary} onClick={() => guard(async () => { await cfgSend("/settings/dxf", "PUT", { default_unit: unit }); reload(); }, "Unidad guardada.")}>Guardar</button>
          </div>
        </section>

        <section className={card}>
          <h2 className="flex items-center gap-2 font-semibold"><KeyRound size={17} /> Clave de configuración</h2>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <input type="password" placeholder="Clave actual" value={current} onChange={(e) => setCurrent(e.target.value)} className={input} />
            <input type="password" placeholder="Clave nueva (mín. 4)" value={next} onChange={(e) => setNext(e.target.value)} className={input} />
          </div>
          <button type="button" className={`${primary} mt-3`} disabled={!current || next.length < 4}
            onClick={() => guard(async () => { await cfgSend("/password", "POST", { current, new: next }); setCurrent(""); setNext(""); onLogout(); }, "Clave cambiada. Inicie sesión de nuevo.")}>
            Cambiar clave
          </button>
        </section>
      </div>
    </div>
  );
}

// ============================================================
// PESTAÑA: LECTURA (CODIGOS / OCR)
// ============================================================

const READING_DEFAULTS: ReadingSettings = {
  codes_enabled: true, text_enabled: true, languages: "spa+eng", psm: 7, min_text_confidence: 60, timeout_s: 8,
  code_formats: "comunes", unreadable_status: "REVISAR", mismatch_status: "FALLO",
};

function ReadingTab({ settings, guard, reload }: { settings: ConfigSettings; guard: Guard; reload: () => void }) {
  const [values, setValues] = useState<ReadingSettings>({ ...READING_DEFAULTS, ...(settings.reading ?? {}) });
  const [caps, setCaps] = useState<ReadingCapabilities>();
  const [probe, setProbe] = useState<string>();
  const [probing, setProbing] = useState(false);
  useEffect(() => setValues({ ...READING_DEFAULTS, ...(settings.reading ?? {}) }), [settings]);
  const refresh = useCallback(async (force = false) => {
    await guard(async () => setCaps(await getReadingCapabilities(force)));
  }, [guard]);
  useEffect(() => { refresh(); }, [refresh]);

  async function test(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setProbing(true);
    setProbe(undefined);
    try {
      const result = await testReading(file, undefined, values.text_enabled && Boolean(caps?.text.available));
      const lines = result.codes.map((code) => `${code.format}: ${code.text}`);
      if (result.text?.text) lines.push(`Texto (${Math.round(result.text.confidence)}%): ${result.text.text}`);
      if (result.text?.error) lines.push(`OCR: ${result.text.error}`);
      setProbe(lines.length ? lines.join("\n") : "No se encontró ningún código ni texto en la foto.");
    } catch (caught) {
      setProbe(caught instanceof Error ? caught.message : "No fue posible leer la foto.");
    } finally {
      setProbing(false);
    }
  }

  const select = "rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm";
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <section className={card}>
        <h2 className="flex items-center gap-2 font-semibold"><ScanBarcode size={17} /> Motores instalados</h2>
        <dl className="mt-3 space-y-2 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-zinc-400">Códigos de barras / QR</dt>
            <dd className={caps?.codes.engine === "zxing-cpp" ? "text-emerald-400" : "text-amber-400"}>
              {caps ? `${caps.codes.engine}${caps.codes.version ? ` ${caps.codes.version}` : ""}` : "…"}
            </dd>
          </div>
          {caps?.codes.note && <p className="text-xs text-amber-300">{caps.codes.note}</p>}
          <div className="flex justify-between gap-3">
            <dt className="text-zinc-400">Texto (Tesseract OCR)</dt>
            <dd className={caps?.text.available ? "text-emerald-400" : "text-zinc-500"}>
              {caps ? (caps.text.available ? caps.text.version ?? "instalado" : "no instalado") : "…"}
            </dd>
          </div>
          {caps?.text.available && (
            <p className="break-all text-xs text-zinc-500">
              {caps.text.path} · idiomas: {caps.text.languages.filter((l) => l !== "osd").join(", ") || "—"}
            </p>
          )}
          {caps && !caps.text.available && <p className="text-xs text-zinc-500">{caps.text.note}</p>}
        </dl>
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" className={secondary} onClick={() => refresh(true)}>Volver a buscar</button>
          <label className={`${secondary} cursor-pointer`}>
            {probing ? "Leyendo…" : "Probar con una foto"}
            <input type="file" accept="image/*" className="hidden" onChange={test} disabled={probing} />
          </label>
        </div>
        {probe && <pre className="mt-3 whitespace-pre-wrap rounded-lg bg-zinc-950 p-3 text-xs text-zinc-300">{probe}</pre>}
        <p className="mt-3 text-xs text-zinc-600">
          El OCR solo se ejecuta cuando una pieza tiene zonas de texto: si no se usa, no consume tiempo. Las zonas se dibujan al
          dar de alta la pieza (Pieza real) o al guardar un patrón (Comparador).
        </p>
      </section>

      <section className={card}>
        <h2 className="font-semibold">Ajustes de lectura</h2>
        <div className="mt-3 space-y-3">
          <Toggle label="Leer códigos de barras / QR" checked={values.codes_enabled} onChange={(v) => setValues({ ...values, codes_enabled: v })} />
          <Toggle label="Leer texto (OCR)" checked={values.text_enabled} onChange={(v) => setValues({ ...values, text_enabled: v })} />
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-xs font-medium text-zinc-300">Tipos de código
              <select className={`${select} mt-1 w-full`} value={values.code_formats} onChange={(e) => setValues({ ...values, code_formats: e.target.value })}>
                <option value="comunes">Comunes (QR, DataMatrix, Code128/39/93, EAN/UPC, ITF, PDF417…)</option>
                <option value="2d">Solo 2D (QR, DataMatrix, Aztec, PDF417)</option>
                <option value="lineales">Solo lineales (Code128/39/93, EAN/UPC, ITF…)</option>
                <option value="todos">Todos los que soporta el lector</option>
              </select>
            </label>
            <label className="block text-xs font-medium text-zinc-300">Idiomas del OCR
              <select className={`${select} mt-1 w-full`} value={values.languages} onChange={(e) => setValues({ ...values, languages: e.target.value })}>
                <option value="spa+eng">Español + inglés</option>
                <option value="spa">Español</option>
                <option value="eng">Inglés</option>
              </select>
            </label>
            <label className="block text-xs font-medium text-zinc-300">Modo de página (PSM)
              <select className={`${select} mt-1 w-full`} value={values.psm} onChange={(e) => setValues({ ...values, psm: Number(e.target.value) })}>
                <option value={7}>7 · una línea (recomendado)</option>
                <option value={6}>6 · bloque de texto</option>
                <option value={8}>8 · una palabra</option>
                <option value={11}>11 · texto disperso</option>
                <option value={13}>13 · línea cruda</option>
              </select>
            </label>
            <NumberField label="Confianza mínima del OCR (%)" step={5} value={values.min_text_confidence} onChange={(v) => v !== undefined && setValues({ ...values, min_text_confidence: v })} />
            <NumberField label="Tiempo máximo por zona (s)" step={1} value={values.timeout_s} onChange={(v) => v !== undefined && setValues({ ...values, timeout_s: v })} />
            <label className="block text-xs font-medium text-zinc-300">Si no se puede leer
              <select className={`${select} mt-1 w-full`} value={values.unreadable_status} onChange={(e) => setValues({ ...values, unreadable_status: e.target.value })}>
                <option value="REVISAR">REVISAR</option>
                <option value="FALLO">FALLO</option>
              </select>
            </label>
            <label className="block text-xs font-medium text-zinc-300">Si se lee otro valor
              <select className={`${select} mt-1 w-full`} value={values.mismatch_status} onChange={(e) => setValues({ ...values, mismatch_status: e.target.value })}>
                <option value="FALLO">FALLO</option>
                <option value="REVISAR">REVISAR</option>
              </select>
            </label>
          </div>
          <button type="button" className={primary} onClick={() => guard(async () => { await cfgSend("/settings/reading", "PUT", values); reload(); }, "Ajustes de lectura guardados.")}>
            <CheckCircle2 size={16} /> Guardar
          </button>
        </div>
      </section>
    </div>
  );
}

// ============================================================
// PAGINA
// ============================================================

type Tab = "inspection" | "camera" | "parts" | "reading" | "storage";

const TABS: { id: Tab; label: string; icon: typeof Camera }[] = [
  { id: "inspection", label: "Inspección", icon: SlidersHorizontal },
  { id: "camera", label: "Cámara y calibración", icon: Camera },
  { id: "parts", label: "Piezas y validación", icon: FlaskConical },
  { id: "reading", label: "Lectura (códigos / OCR)", icon: ScanBarcode },
  { id: "storage", label: "Almacenamiento y seguridad", icon: Database },
];

export default function SettingsPage() {
  const [authed, setAuthed] = useState(Boolean(configSession.get()));
  const [defaultPassword, setDefaultPassword] = useState(false);
  const [settings, setSettings] = useState<ConfigSettings>();
  const [tab, setTab] = useState<Tab>("inspection");
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [resetOpen, setResetOpen] = useState(false);

  const logout = useCallback(() => {
    configSession.clear();
    setAuthed(false);
    setSettings(undefined);
  }, []);

  const guard: Guard = useCallback(
    async (action, okMessage) => {
      try {
        setError(undefined);
        const result = await action();
        if (okMessage) {
          setNotice(okMessage);
          window.setTimeout(() => setNotice(undefined), 5000);
        }
        return result;
      } catch (caught) {
        if (caught instanceof UnauthorizedError) {
          logout();
        } else {
          setError(caught instanceof Error ? caught.message : "Error inesperado.");
        }
        return undefined;
      }
    },
    [logout],
  );

  const loadSettings = useCallback(async () => {
    const data = await guard(() => cfgJson<ConfigSettings>("/settings"));
    if (data) setSettings(data);
  }, [guard]);

  useEffect(() => {
    if (authed) loadSettings();
  }, [authed, loadSettings]);

  if (!authed) {
    return (
      <LoginGate
        onSuccess={(isDefault) => {
          setDefaultPassword(isDefault);
          setAuthed(true);
        }}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-emerald-400">Administración</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">Configuración</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm font-semibold text-red-300 transition hover:bg-red-500/20"
            onClick={() => setResetOpen(true)}
          >
            <RotateCcw size={16} /> Restablecer configuración
          </button>
          <button type="button" className={secondary} onClick={() => guard(async () => { await cfgSend("/logout", "POST"); }).finally(logout)}>
            <LogOut size={16} /> Cerrar sesión
          </button>
        </div>
      </div>

      {resetOpen && (
        <ResetDialog
          guard={guard}
          onClose={() => setResetOpen(false)}
          onDone={(result) => {
            setResetOpen(false);
            loadSettings();
            if (result.camera_error) {
              setError(`Configuración restablecida, pero la cámara no se pudo reiniciar: ${result.camera_error}`);
            } else {
              setNotice(
                result.camera_restarted
                  ? "Configuración restablecida a valores de fábrica y cámara reiniciada."
                  : "Configuración restablecida a valores de fábrica.",
              );
              window.setTimeout(() => setNotice(undefined), 6000);
            }
          }}
        />
      )}

      {defaultPassword && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
          Está usando la clave predeterminada. Cámbiela en “Almacenamiento y seguridad”.
        </div>
      )}
      {error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}
      {notice && <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">{notice}</div>}

      <div className="flex w-fit max-w-full flex-wrap gap-1 rounded-xl border border-zinc-800 bg-zinc-950 p-1">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" onClick={() => setTab(id)}
            className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition ${tab === id ? "bg-emerald-500/15 text-emerald-400" : "text-zinc-500 hover:text-zinc-300"}`}>
            <Icon size={15} /> {label}
          </button>
        ))}
      </div>

      {!settings ? (
        <p className="text-sm text-zinc-500">Cargando…</p>
      ) : tab === "inspection" ? (
        <InspectionTab settings={settings} guard={guard} reload={loadSettings} />
      ) : tab === "camera" ? (
        <CameraTab settings={settings} guard={guard} reload={loadSettings} />
      ) : tab === "parts" ? (
        <PartsTab settings={settings} guard={guard} reloadSettings={loadSettings} />
      ) : tab === "reading" ? (
        <ReadingTab settings={settings} guard={guard} reload={loadSettings} />
      ) : (
        <StorageTab settings={settings} guard={guard} reload={loadSettings} onLogout={logout} />
      )}
    </div>
  );
}
