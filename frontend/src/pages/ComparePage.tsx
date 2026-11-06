import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BookmarkPlus,
  Camera,
  CheckCircle2,
  CircleStop,
  Crop,
  GitCompareArrows,
  History,
  ImageIcon,
  LoaderCircle,
  Play,
  Trash2,
  UploadCloud,
} from "lucide-react";

import { captureSnapshot, getCameraStatus, startCamera, stopCamera, type CameraStartOptions } from "../lib/api";
import {
  compareImageUrl,
  createPart,
  deletePart,
  getCompareOptions,
  getCompareReport,
  listCompareHistory,
  listParts,
  partImageUrl,
  runCompare,
  type CompareHistoryRow,
  type CompareRegion,
  type ComparePart,
  type CompareReport,
  type Sensitivity,
  type SensitivityOption,
} from "../lib/compareApi";
import { useCameraStream } from "../hooks/useCameraStream";
import CameraSelector, { loadCameraOptions } from "../components/CameraSelector";
import ZoomableImage from "../components/ZoomableImage";
import type { CameraStatus, CropRect } from "../types/api";

type Mode = "inspect" | "register";
type Source = "camera" | "photo";
type View = "annotated" | "heatmap" | "golden" | "aligned";

const LAST_PART_KEY = "optical-compare.part";
const SENSITIVITY_KEY = "optical-compare.sensitivity";

const inputClass =
  "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm outline-none placeholder:text-zinc-700 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/15";
const buttonPrimary =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40";
const buttonSecondary =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm font-semibold transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40";

const VERDICT_STYLE: Record<string, string> = {
  OK: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
  REVISAR: "border-amber-500/40 bg-amber-500/10 text-amber-400",
  FALLO: "border-red-500/40 bg-red-500/10 text-red-400",
};
const SEVERITY_STYLE: Record<string, string> = {
  leve: "border-yellow-500/30 bg-yellow-500/10 text-yellow-300",
  moderada: "border-orange-500/30 bg-orange-500/10 text-orange-300",
  critica: "border-red-500/40 bg-red-500/15 text-red-300",
};
const VIEW_LABEL: Record<View, string> = {
  annotated: "Diferencias",
  heatmap: "Mapa de calor",
  golden: "Patrón",
  aligned: "Pieza alineada",
};

function readStored(key: string): string | undefined {
  try {
    return localStorage.getItem(key) ?? undefined;
  } catch {
    return undefined;
  }
}

function writeStored(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* sin almacenamiento */
  }
}

export default function ComparePage() {
  const [mode, setMode] = useState<Mode>("inspect");
  const [source, setSource] = useState<Source>("camera");
  const [camera, setCamera] = useState<CameraStatus>();
  const [cameraOptions, setCameraOptions] = useState<CameraStartOptions>(loadCameraOptions);
  const [loadingCamera, setLoadingCamera] = useState(false);

  const [photo, setPhoto] = useState<File>();
  const [photoUrl, setPhotoUrl] = useState<string>();
  const [crop, setCrop] = useState<CropRect>();
  const [cropMode, setCropMode] = useState(false);

  const [parts, setParts] = useState<ComparePart[]>([]);
  const [partId, setPartId] = useState<number>();
  const [sensitivities, setSensitivities] = useState<SensitivityOption[]>([]);
  const [sensitivity, setSensitivity] = useState<Sensitivity>("estricto");

  const [newName, setNewName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string>();

  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<CompareReport>();
  const [view, setView] = useState<View>("annotated");
  const [selected, setSelected] = useState<number>();
  const [history, setHistory] = useState<CompareHistoryRow[]>([]);
  const [error, setError] = useState<string>();

  const stream = useCameraStream(source === "camera" && Boolean(camera?.running) && !photo);
  const part = useMemo(() => parts.find((item) => item.id === partId), [parts, partId]);
  const existingName = parts.find((item) => item.name.toLowerCase() === newName.trim().toLowerCase());

  function message(caught: unknown, fallback: string) {
    return caught instanceof Error ? caught.message : fallback;
  }

  async function refreshParts(preferred?: number) {
    const list = await listParts();
    setParts(list);
    const remembered = Number(readStored(LAST_PART_KEY));
    setPartId((current) => {
      const wanted = preferred ?? current ?? remembered;
      return list.some((item) => item.id === wanted) ? wanted : list[0]?.id;
    });
  }

  useEffect(() => {
    getCameraStatus().then(setCamera).catch(() => undefined);
    refreshParts().catch((caught) => setError(message(caught, "No fue posible cargar las piezas.")));
    getCompareOptions()
      .then((options) => {
        setSensitivities(options.sensitivities);
        const saved = readStored(SENSITIVITY_KEY) as Sensitivity | undefined;
        setSensitivity(options.sensitivities.some((s) => s.id === saved) ? (saved as Sensitivity) : options.default_sensitivity);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!photo) {
      setPhotoUrl(undefined);
      return;
    }
    const url = URL.createObjectURL(photo);
    setPhotoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  useEffect(() => {
    if (part) writeStored(LAST_PART_KEY, String(part.id));
    listCompareHistory(part?.name).then(setHistory).catch(() => undefined);
  }, [part?.id, report?.id]);

  async function toggleCamera(start: boolean) {
    try {
      setLoadingCamera(true);
      setError(undefined);
      setCamera(start ? await startCamera(cameraOptions) : await stopCamera());
    } catch (caught) {
      setError(message(caught, "No fue posible cambiar el estado de la cámara."));
    } finally {
      setLoadingCamera(false);
    }
  }

  function clearResult() {
    setReport(undefined);
    setSelected(undefined);
    setNotice(undefined);
    setView("annotated");
  }

  function discardPhoto() {
    setPhoto(undefined);
    setCrop(undefined);
    setCropMode(false);
    clearResult();
  }

  async function handleCapture() {
    try {
      setError(undefined);
      discardPhoto();
      setPhoto(await captureSnapshot());
    } catch (caught) {
      setError(message(caught, "No fue posible capturar la imagen."));
    }
  }

  function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError(undefined);
    discardPhoto();
    setPhoto(file);
  }

  async function handleInspect() {
    if (!photo || !part) return;
    try {
      setRunning(true);
      setError(undefined);
      clearResult();
      const result = await runCompare({ photo, partId: part.id, sensitivity, crop });
      setReport(result);
      setSelected(result.regions[0]?.id);
    } catch (caught) {
      setError(message(caught, "No fue posible inspeccionar la pieza."));
    } finally {
      setRunning(false);
    }
  }

  async function handleSavePart() {
    if (!photo) return;
    try {
      setSaving(true);
      setError(undefined);
      const saved = await createPart({ photo, name: newName.trim(), description: description.trim(), crop });
      await refreshParts(saved.id);
      setNewName("");
      setDescription("");
      discardPhoto();
      setNotice(`Patrón «${saved.name}» guardado (revisión ${saved.revision}). Ya puede inspeccionar piezas contra él.`);
    } catch (caught) {
      setError(message(caught, "No fue posible guardar el patrón."));
    } finally {
      setSaving(false);
    }
  }

  async function handleDeletePart() {
    if (!part || !window.confirm(`¿Eliminar la revisión ${part.revision} de «${part.name}»?`)) return;
    try {
      await deletePart(part.id);
      clearResult();
      await refreshParts();
    } catch (caught) {
      setError(message(caught, "No fue posible eliminar la pieza."));
    }
  }

  async function openHistory(id: number) {
    try {
      setError(undefined);
      const result = await getCompareReport(id);
      setReport(result);
      setSelected(result.regions[0]?.id);
      setView("annotated");
      setPhoto(undefined);
      setCrop(undefined);
    } catch (caught) {
      setError(message(caught, "No fue posible abrir la inspección."));
    }
  }

  const showingReport = mode === "inspect" && Boolean(report);
  const reportImage = report ? compareImageUrl(report.images[view] ?? report.images.annotated) : undefined;
  const [workWidth, workHeight] = report?.metrics.work_size ?? [1, 1];
  const selectedRegion = report?.regions.find((region) => region.id === selected);

  const canInspect = Boolean(photo && part) && !running;
  const canSave = Boolean(photo && newName.trim()) && !saving;

  return (
    <div className="mx-auto max-w-[1900px] space-y-6 px-5 py-6 lg:px-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <GitCompareArrows size={21} className="text-emerald-400" /> Comparador óptico
          </h2>
          <p className="mt-1 text-sm text-zinc-500">
            Compara cualquier pieza contra su patrón: tome la foto, recorte el área, elija la pieza e inspeccione.
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-xl border border-zinc-800 bg-zinc-950 p-1">
          {(["inspect", "register"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setMode(value);
                discardPhoto();
              }}
              className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${
                mode === value ? "bg-emerald-500/15 text-emerald-400" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {value === "inspect" ? "Inspeccionar" : "Guardar patrón"}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          <AlertTriangle size={17} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {notice && (
        <div className="flex items-start gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
          <CheckCircle2 size={17} className="mt-0.5 shrink-0" />
          <span>{notice}</span>
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        {/* IMAGEN */}
        <section className="overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/60">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
            {showingReport ? (
              <div className="flex flex-wrap items-center gap-1 rounded-xl border border-zinc-800 bg-zinc-950 p-1">
                {(Object.keys(VIEW_LABEL) as View[]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setView(value)}
                    className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                      view === value ? "bg-emerald-500/15 text-emerald-400" : "text-zinc-500 hover:text-zinc-300"
                    }`}
                  >
                    {VIEW_LABEL[value]}
                  </button>
                ))}
              </div>
            ) : (
              <>
                <div className="flex w-fit items-center gap-1 rounded-xl border border-zinc-800 bg-zinc-950 p-1">
                  {(["camera", "photo"] as const).map((value) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => {
                        setSource(value);
                        discardPhoto();
                      }}
                      className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                        source === value ? "bg-emerald-500/15 text-emerald-400" : "text-zinc-500 hover:text-zinc-300"
                      }`}
                    >
                      {value === "camera" ? <Camera size={15} /> : <ImageIcon size={15} />}
                      {value === "camera" ? "Cámara" : "Fotografía"}
                    </button>
                  ))}
                </div>
                {source === "camera" && (
                  <div className="flex flex-wrap items-center gap-2">
                    <CameraSelector value={cameraOptions} onChange={setCameraOptions} disabled={loadingCamera || camera?.running} />
                    <button type="button" className={buttonPrimary} onClick={() => toggleCamera(true)} disabled={loadingCamera || camera?.running}>
                      {loadingCamera ? <LoaderCircle size={16} className="animate-spin" /> : <Play size={16} />}
                      Iniciar
                    </button>
                    <button type="button" className={buttonSecondary} onClick={() => toggleCamera(false)} disabled={loadingCamera || !camera?.running}>
                      <CircleStop size={16} /> Detener
                    </button>
                  </div>
                )}
              </>
            )}
            {showingReport && (
              <button type="button" className={buttonSecondary} onClick={discardPhoto}>
                Nueva inspección
              </button>
            )}
          </div>

          <div className="relative flex aspect-video items-center justify-center bg-black">
            {showingReport && reportImage ? (
              <ZoomableImage
                key={reportImage}
                src={reportImage}
                alt={VIEW_LABEL[view]}
                className="absolute inset-0"
                overlay={
                  selectedRegion && (view === "annotated" || view === "heatmap") ? (
                    <rect
                      x={selectedRegion.x - 8}
                      y={selectedRegion.y - 8}
                      width={selectedRegion.w + 16}
                      height={selectedRegion.h + 16}
                      fill="none"
                      stroke="#34d399"
                      strokeWidth={3}
                      strokeDasharray="8 5"
                      vectorEffect="non-scaling-stroke"
                      pointerEvents="none"
                    />
                  ) : undefined
                }
              />
            ) : photoUrl ? (
              <ZoomableImage
                src={photoUrl}
                alt="Foto"
                className="absolute inset-0"
                selection={crop}
                selectMode={cropMode}
                onSelection={setCrop}
              />
            ) : source === "camera" ? (
              stream.imageUrl && camera?.running ? (
                <img src={stream.imageUrl} alt="Vista en vivo" className="max-h-full max-w-full" />
              ) : (
                <p className="text-sm text-zinc-500">
                  {camera?.running ? "Esperando imagen..." : "Inicie la cámara para ver la pieza."}
                </p>
              )
            ) : (
              <label className="flex cursor-pointer flex-col items-center gap-2 text-zinc-400 hover:text-zinc-200">
                <UploadCloud size={32} />
                <span className="text-sm">Seleccionar fotografía de la pieza</span>
                <input type="file" accept="image/*" className="hidden" onChange={handleFile} />
              </label>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800 px-4 py-3">
            <p className="text-xs text-zinc-500">
              {showingReport
                ? "Rueda o pellizco: zoom. Pulse una zona de la lista para resaltarla. Azul claro = no evaluable (reflejo saturado en ambas fotos)."
                : cropMode
                  ? "Arrastre sobre la imagen para elegir el área a analizar (rueda: zoom). Pulse Listo al terminar."
                  : crop
                    ? `Área a analizar: ${crop.w} × ${crop.h} px.`
                    : photo
                      ? "Recorte el área a analizar (si no recorta, se usa toda la foto)."
                      : "Capture o seleccione una foto."}
            </p>
            {!showingReport && (
              <div className="flex gap-2">
                {source === "camera" && !photo && (
                  <button type="button" className={buttonPrimary} onClick={handleCapture} disabled={!camera?.running}>
                    <Camera size={16} /> Capturar
                  </button>
                )}
                {photo && (
                  <>
                    <button
                      type="button"
                      className={`${buttonSecondary} ${cropMode ? "border-emerald-500/60 text-emerald-400" : ""}`}
                      onClick={() => setCropMode((value) => !value)}
                    >
                      <Crop size={16} /> {cropMode ? "Listo" : crop ? "Cambiar recorte" : "Recortar"}
                    </button>
                    {crop && !cropMode && (
                      <button type="button" className={buttonSecondary} onClick={() => setCrop(undefined)}>
                        Quitar recorte
                      </button>
                    )}
                    <button type="button" className={buttonSecondary} onClick={discardPhoto}>
                      Descartar
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </section>

        {/* PANEL LATERAL */}
        <aside className="space-y-4">
          {mode === "inspect" ? (
            <section className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
              <label className="block text-sm">
                <span className="mb-1.5 block font-medium text-zinc-300">Pieza</span>
                <select
                  className={inputClass}
                  value={partId ?? ""}
                  onChange={(event) => {
                    setPartId(Number(event.target.value));
                    clearResult();
                  }}
                >
                  {!parts.length && <option value="">No hay piezas: guarde un patrón primero</option>}
                  {parts.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} (rev. {item.revision})
                    </option>
                  ))}
                </select>
              </label>

              {part && (
                <div className="flex gap-3 rounded-xl border border-zinc-800 bg-zinc-950 p-2">
                  <img src={partImageUrl(part.id, 220)} alt="Patrón" className="h-20 w-28 rounded-lg object-contain bg-black" />
                  <div className="min-w-0 text-xs text-zinc-500">
                    <p className="truncate font-medium text-zinc-300">{part.name}</p>
                    <p>
                      Revisión {part.revision} · {part.width}×{part.height} px
                    </p>
                    <p>{part.inspections ?? 0} inspecciones</p>
                    {part.description && <p className="truncate">{part.description}</p>}
                    <button type="button" onClick={handleDeletePart} className="mt-1 inline-flex items-center gap-1 text-red-400 hover:text-red-300">
                      <Trash2 size={12} /> Eliminar revisión
                    </button>
                  </div>
                </div>
              )}

              <label className="block text-sm">
                <span className="mb-1.5 block font-medium text-zinc-300">Sensibilidad</span>
                <select
                  className={inputClass}
                  value={sensitivity}
                  onChange={(event) => {
                    setSensitivity(event.target.value as Sensitivity);
                    writeStored(SENSITIVITY_KEY, event.target.value);
                  }}
                >
                  {sensitivities.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id[0].toUpperCase() + item.id.slice(1)} (dE ≥ {item.delta_e}, zonas ≥ {item.min_area_pct}%)
                    </option>
                  ))}
                </select>
              </label>

              <button type="button" className={`${buttonPrimary} w-full`} onClick={handleInspect} disabled={!canInspect}>
                {running ? <LoaderCircle size={16} className="animate-spin" /> : <GitCompareArrows size={16} />}
                {running ? "Comparando..." : "Inspeccionar"}
              </button>
              {!photo && !showingReport && <p className="text-xs text-zinc-500">Tome o cargue una foto de la pieza para inspeccionar.</p>}
            </section>
          ) : (
            <section className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
              <label className="block text-sm">
                <span className="mb-1.5 block font-medium text-zinc-300">Nombre de la pieza</span>
                <input
                  className={inputClass}
                  list="compare-part-names"
                  value={newName}
                  maxLength={80}
                  onChange={(event) => setNewName(event.target.value)}
                  placeholder="Ej. Soporte trasero"
                />
                <datalist id="compare-part-names">
                  {parts.map((item) => (
                    <option key={item.id} value={item.name} />
                  ))}
                </datalist>
              </label>
              <label className="block text-sm">
                <span className="mb-1.5 block font-medium text-zinc-300">Descripción (opcional)</span>
                <input className={inputClass} value={description} onChange={(event) => setDescription(event.target.value)} />
              </label>
              {existingName && (
                <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  Ya existe «{existingName.name}»: se guardará como revisión {existingName.revision + 1} y pasará a ser la usada para
                  inspeccionar. La anterior se conserva.
                </p>
              )}
              <button type="button" className={`${buttonPrimary} w-full`} onClick={handleSavePart} disabled={!canSave}>
                {saving ? <LoaderCircle size={16} className="animate-spin" /> : <BookmarkPlus size={16} />}
                Guardar como patrón
              </button>
              <p className="text-xs text-zinc-500">
                Tome una foto nítida y bien iluminada de una pieza buena y recorte el área que se va a comparar. Las siguientes
                inspecciones deben recortar la misma zona (no tiene que ser exacta ni estar en la misma posición).
              </p>
            </section>
          )}

          {mode === "inspect" && report && (
            <section className="space-y-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
              <div className="flex items-center justify-between gap-3">
                <span className={`rounded-full border px-4 py-1.5 text-sm font-bold ${VERDICT_STYLE[report.status]}`}>{report.status}</span>
                <span className="text-sm text-zinc-400">
                  Puntaje <b className="text-zinc-100">{report.score.toFixed(0)}</b>/100
                </span>
              </div>
              <p className="text-sm leading-relaxed text-zinc-300">{report.summary}</p>
              <dl className="grid grid-cols-3 gap-2 text-center text-xs">
                {[
                  ["Zonas", String(report.metrics.regions)],
                  ["Área distinta", `${report.metrics.anomalous_area_pct.toFixed(2)}%`],
                  ["Similitud", report.metrics.similarity.toFixed(3)],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-lg border border-zinc-800 bg-zinc-950 py-2">
                    <dt className="text-zinc-500">{label}</dt>
                    <dd className="mt-0.5 font-semibold text-zinc-200">{value}</dd>
                  </div>
                ))}
              </dl>
              <p className="text-[11px] text-zinc-600">
                {report.part.name} rev. {report.part.revision} · alineamiento {report.alignment.method.toLowerCase()} (
                {report.alignment.inliers} puntos, cobertura {report.alignment.coverage_pct}%) · sensibilidad {report.sensitivity}
              </p>
            </section>
          )}
        </aside>
      </div>

      {/* DISCREPANCIAS */}
      {mode === "inspect" && report && report.regions.length > 0 && (
        <section className="space-y-3">
          <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-500">Discrepancias ({report.regions.length})</h3>
          <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {report.regions.map((region) => (
              <RegionCard
                key={region.id}
                region={region}
                active={region.id === selected}
                imageUrl={report.images[`region-${region.id}`] ? compareImageUrl(report.images[`region-${region.id}`]) : undefined}
                onSelect={() => {
                  setSelected(region.id);
                  setView("annotated");
                }}
                width={workWidth}
                height={workHeight}
              />
            ))}
          </div>
        </section>
      )}

      {/* HISTORIAL */}
      {mode === "inspect" && history.length > 0 && (
        <section className="space-y-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-zinc-500">
            <History size={15} /> Últimas inspecciones{part ? ` de ${part.name}` : ""}
          </h3>
          <div className="overflow-hidden rounded-xl border border-zinc-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-zinc-900 text-xs uppercase text-zinc-500">
                <tr>
                  <th className="px-3 py-2">#</th>
                  <th className="px-3 py-2">Fecha</th>
                  <th className="px-3 py-2">Pieza</th>
                  <th className="px-3 py-2">Resultado</th>
                  <th className="px-3 py-2">Zonas</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.id} onClick={() => openHistory(row.id)} className="cursor-pointer border-t border-zinc-800 hover:bg-zinc-900/70">
                    <td className="px-3 py-2 text-zinc-500">{row.id}</td>
                    <td className="px-3 py-2 text-zinc-400">{row.created_at.replace("T", " ")}</td>
                    <td className="px-3 py-2">
                      {row.part_name} <span className="text-zinc-600">rev. {row.part_revision}</span>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${VERDICT_STYLE[row.status]}`}>{row.status}</span>
                    </td>
                    <td className="px-3 py-2 text-zinc-400">{row.regions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function RegionCard({
  region,
  active,
  imageUrl,
  onSelect,
  width,
  height,
}: {
  region: CompareRegion;
  active: boolean;
  imageUrl?: string;
  onSelect: () => void;
  width: number;
  height: number;
}) {
  return (
    <article
      onClick={onSelect}
      className={`cursor-pointer space-y-3 rounded-2xl border p-4 transition ${
        active ? "border-emerald-500/50 bg-emerald-500/5" : "border-zinc-800 bg-zinc-900/60 hover:border-zinc-700"
      }`}
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-semibold text-zinc-100">
          <span className="mr-2 inline-grid h-6 w-6 place-items-center rounded-md bg-zinc-800 text-xs">{region.id}</span>
          {region.kind_label}
        </h4>
        <span className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${SEVERITY_STYLE[region.severity]}`}>{region.severity}</span>
      </header>
      {imageUrl && <img src={imageUrl} alt={`Zona ${region.id}: patrón y pieza`} className="w-full rounded-lg border border-zinc-800 bg-black" />}
      <p className="text-xs text-zinc-500">
        {region.zone} · x {Math.round((100 * region.x) / width)}–{Math.round((100 * (region.x + region.w)) / width)} %, y{" "}
        {Math.round((100 * region.y) / height)}–{Math.round((100 * (region.y + region.h)) / height)} % · {region.area_px} px (
        {region.area_pct.toFixed(2)} % de la pieza) · dE máx {region.delta_e_max}
      </p>
      <ul className="space-y-1 text-sm text-zinc-300">
        {region.facts.map((fact) => (
          <li key={fact}>• {fact}</li>
        ))}
      </ul>
      <p className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-400">
        <b className="text-zinc-300">Causa probable:</b> {region.probable_cause}
      </p>
    </article>
  );
}
