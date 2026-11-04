import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import {
  Box,
  Camera,
  CheckCircle2,
  CircleStop,
  ImageIcon,
  Gauge,
  LoaderCircle,
  MousePointerClick,
  Play,
  Scan,
  ScanSearch,
  Trash2,
  UploadCloud,
} from "lucide-react";

import {
  analyzeReference,
  captureSnapshot,
  getCameraStatus,
  saveReference,
  scanDepth,
  startCamera,
  stopCamera,
  type CameraStartOptions,
} from "../lib/api";
import { useCameraStream } from "../hooks/useCameraStream";
import CameraSelector, { loadCameraOptions } from "../components/CameraSelector";
import ZoomableImage, { type ImageMarker } from "../components/ZoomableImage";

// three.js pesa bastante: solo se descarga al abrir la vista 3D.
const PartViewer3D = lazy(() => import("../components/PartViewer3D"));
import { EDITABLE_TYPES, FEATURE_TYPES, typeLabel } from "../components/FeatureInfo";
import type {
  CameraStatus,
  ScanInfo,
  ReferenceAnalysis,
  ReferenceCategories,
  ReferenceFeature,
  UploadResponse,
} from "../types/api";

type Source = "camera" | "photo";

const CATEGORY_LABELS: Record<keyof ReferenceCategories, string> = {
  features: "Insertos / agujeros",
  contour: "Contorno y dimensiones",
  distances: "Distancias entre features",
  label: "Etiqueta",
};

const inputClass =
  "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm outline-none placeholder:text-zinc-700 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/15";

const buttonPrimary =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40";

const buttonSecondary =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm font-semibold transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40";

export default function ReferencePage() {
  const [source, setSource] = useState<Source>("camera");
  const [photo, setPhoto] = useState<File>();
  const [photoUrl, setPhotoUrl] = useState<string>();
  const [correctedUrl, setCorrectedUrl] = useState<string>();

  const [camera, setCamera] = useState<CameraStatus>();
  const [cameraOptions, setCameraOptions] = useState<CameraStartOptions>(loadCameraOptions);
  const [loadingCamera, setLoadingCamera] = useState(false);

  const [knownLength, setKnownLength] = useState("");
  const [knownAxis, setKnownAxis] = useState<"long" | "short">("long");
  const [analysis, setAnalysis] = useState<ReferenceAnalysis>();
  const [analyzing, setAnalyzing] = useState(false);
  const [addMode, setAddMode] = useState(false);
  const [view, setView] = useState<"2d" | "3d">("2d");
  const [scanId, setScanId] = useState<string>();
  const [scanInfo, setScanInfo] = useState<ScanInfo>();
  const [scanning, setScanning] = useState(false);
  const [imageDims, setImageDims] = useState<{ width: number; height: number }>();

  const [partNo, setPartNo] = useState("");
  const [revision, setRevision] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<UploadResponse>();
  const [error, setError] = useState<string>();

  const stream = useCameraStream(source === "camera" && Boolean(camera?.running) && !photo);

  useEffect(() => {
    getCameraStatus().then(setCamera).catch(() => undefined);
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

  function message(caught: unknown, fallback: string) {
    return caught instanceof Error ? caught.message : fallback;
  }

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

  function resetAnalysis() {
    setView("2d");
    setAnalysis(undefined);
    setCorrectedUrl(undefined);
    setSaved(undefined);
    setAddMode(false);
  }

  async function handleCapture() {
    try {
      setError(undefined);
      resetAnalysis();
      setScanId(undefined);
      setScanInfo(undefined);
      setPhoto(await captureSnapshot());
    } catch (caught) {
      setError(message(caught, "No fue posible capturar la imagen."));
    }
  }

  // Escaneo 3D: foto a color + profundidad de la RealSense promediada en el mismo instante.
  async function handleScan() {
    try {
      setScanning(true);
      setError(undefined);
      resetAnalysis();
      const { photo: scanned, info } = await scanDepth(16);
      setPhoto(scanned);
      setScanId(info.scan_id);
      setScanInfo(info);
    } catch (caught) {
      setError(message(caught, "No fue posible escanear la pieza."));
    } finally {
      setScanning(false);
    }
  }

  function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError(undefined);
    resetAnalysis();
    setScanId(undefined);
    setScanInfo(undefined);
    setPhoto(file);
  }

  function discardPhoto() {
    setPhoto(undefined);
    setScanId(undefined);
    setScanInfo(undefined);
    resetAnalysis();
  }

  async function handleAnalyze() {
    if (!photo) return;
    try {
      setAnalyzing(true);
      setError(undefined);
      setSaved(undefined);
      const length = Number(knownLength);
      const result = await analyzeReference(
        photo,
        Number.isFinite(length) && length > 0 ? length : undefined,
        knownAxis,
        scanId,
      );
      // Con calibración, las coordenadas están en la imagen corregida.
      setCorrectedUrl(result.image_jpeg ? `data:image/jpeg;base64,${result.image_jpeg}` : undefined);
      setAnalysis(result);
    } catch (caught) {
      setError(message(caught, "No fue posible analizar la pieza."));
    } finally {
      setAnalyzing(false);
    }
  }

  function updateFeature(id: number, patch: Partial<ReferenceFeature>) {
    setAnalysis((current) =>
      current && {
        ...current,
        features: current.features.map((item) => (item.id === id ? { ...item, ...patch } : item)),
      },
    );
  }

  function removeFeature(id: number) {
    setAnalysis((current) => current && { ...current, features: current.features.filter((item) => item.id !== id) });
  }

  function addFeatureAt(px: number, py: number) {
    setAnalysis((current) => {
      if (!current) return current;
      const radii = current.features.map((item) => item.radius).sort((a, b) => a - b);
      const radius = radii.length ? radii[Math.floor(radii.length / 2)] : 5 * current.scale;
      const nextId = current.features.reduce((max, item) => Math.max(max, item.id), 0) + 1;
      return {
        ...current,
        features: [
          ...current.features,
          {
            id: nextId,
            type: "INSERTO",
            material: "ACERO",
            x: px * current.scale,
            y: -py * current.scale,
            radius,
            enabled: true,
            radius_reliable: false,
          },
        ],
      };
    });
  }

  function toggleCategory(key: keyof ReferenceCategories) {
    setAnalysis((current) =>
      current && { ...current, enabled: { ...current.enabled, [key]: !current.enabled[key] } },
    );
  }

  async function handleSave() {
    if (!photo || !analysis) return;
    try {
      setSaving(true);
      setError(undefined);
      const { preview_png: _preview, image_jpeg: _image, relief: _relief, scan_id: _scan, ...reference } = analysis;
      void _preview;
      void _image;
      void _relief;
      void _scan;
      setSaved(await saveReference({ photo, partNo, revision, description, reference, scanId }));
    } catch (caught) {
      setError(message(caught, "No fue posible guardar la pieza patrón."));
    } finally {
      setSaving(false);
    }
  }

  const activeCount = useMemo(
    () => analysis?.features.filter((item) => item.enabled).length ?? 0,
    [analysis],
  );

  const canSave =
    Boolean(analysis && partNo.trim() && revision.trim()) &&
    (!analysis?.enabled.features || activeCount >= 4) &&
    !saving;

  const unit = analysis?.dimensions.unit ?? "mm";
  const scale = analysis?.scale ?? 1;

  const markers: ImageMarker[] = (analysis?.features ?? []).map((item) => ({
    key: item.id,
    x: item.x / scale,
    y: -item.y / scale,
    r: Math.max(6, item.radius / scale) + 4,
    color: !item.enabled || !analysis?.enabled.features
      ? "#71717a"
      : FEATURE_TYPES[item.type]?.color ?? "#fb923c",
    label: String(item.id),
    dim: !item.enabled,
  }));

  function setFeatureType(id: number, type: string) {
    updateFeature(id, {
      type,
      material: type === "INSERTO" ? "ACERO" : type === "INSERTO_LATON" ? "LATON" : type === "BARRENO" ? "BARRENO" : "DELRON",
    });
  }

  function renderFeatureTooltip(key: string | number, pinned: boolean) {
    const item = analysis?.features.find((feature) => feature.id === key);
    if (!item) return null;
    return (
      <div className="space-y-2 pr-4">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: FEATURE_TYPES[item.type]?.color ?? "#a1a1aa" }} />
          <span className="font-semibold">#{item.id} · {typeLabel(item.type)}</span>
          {!item.enabled && <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">Inactivo</span>}
        </div>
        <p className="text-zinc-400">{FEATURE_TYPES[item.type]?.description}</p>
        <p className="text-[10px] text-zinc-500">
          Radio {item.radius.toFixed(2)} {unit}
          {item.radius_reliable === false ? " · radio estimado (bajo contraste)" : ""}
        </p>
        {pinned ? (
          <div className="space-y-2 border-t border-zinc-800 pt-2">
            <div>
              <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500">Tipo</p>
              <div className="grid grid-cols-2 gap-1">
                {EDITABLE_TYPES.map((type) => (
                  <button
                    key={type}
                    type="button"
                    onClick={() => setFeatureType(item.id, type)}
                    className={`rounded-md border px-1.5 py-1 text-[11px] transition ${
                      item.type === type
                        ? "border-transparent font-semibold text-zinc-950"
                        : "border-zinc-700 text-zinc-300 hover:bg-zinc-800"
                    }`}
                    style={item.type === type ? { background: FEATURE_TYPES[type].color } : undefined}
                  >
                    {FEATURE_TYPES[type].label}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex items-center justify-between gap-2">
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  className="accent-emerald-500"
                  checked={item.enabled}
                  onChange={(event) => updateFeature(item.id, { enabled: event.target.checked })}
                />
                Verificar este feature
              </label>
              <button
                type="button"
                className="inline-flex items-center gap-1 text-zinc-500 hover:text-red-400"
                onClick={() => removeFeature(item.id)}
              >
                <Trash2 size={13} /> Eliminar
              </button>
            </div>
          </div>
        ) : (
          <p className="text-[10px] text-zinc-500">Clic para cambiar tipo, activar o eliminar.</p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm font-medium text-emerald-400">Comparador óptico</p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">Alta por pieza real</h1>
        <p className="mt-2 max-w-3xl text-sm text-zinc-500">
          Captura una pieza buena (patrón). El sistema extrae insertos, contorno, dimensiones y
          etiqueta; elige qué se verificará. La comparación se alinea por la geometría de los
          propios features, así que el cero no necesita estar bien definido.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        {/* IMAGEN */}
        <section className="overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/60">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
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
                <CameraSelector
                  value={cameraOptions}
                  onChange={setCameraOptions}
                  disabled={loadingCamera || camera?.running}
                />
                <button
                  type="button"
                  className={buttonPrimary}
                  onClick={() => toggleCamera(true)}
                  disabled={loadingCamera || camera?.running}
                >
                  {loadingCamera ? <LoaderCircle size={16} className="animate-spin" /> : <Play size={16} />}
                  Iniciar
                </button>
                <button
                  type="button"
                  className={buttonSecondary}
                  onClick={() => toggleCamera(false)}
                  disabled={loadingCamera || !camera?.running}
                >
                  <CircleStop size={16} />
                  Detener
                </button>
              </div>
            )}
          </div>

          <div className="relative flex aspect-video items-center justify-center bg-black">
            {photoUrl && analysis && view === "3d" ? (
              <Suspense fallback={<LoaderCircle size={28} className="animate-spin text-emerald-400" />}>
                <PartViewer3D
                  fill
                  model={{
                    unit,
                    contour: analysis.contour,
                    features: analysis.features,
                    relief: analysis.relief,
                    texture: imageDims
                      ? { url: correctedUrl ?? photoUrl, width: imageDims.width, height: imageDims.height, scale }
                      : null,
                  }}
                />
              </Suspense>
            ) : photoUrl ? (
              <ZoomableImage
                onLoad={setImageDims}
                src={correctedUrl ?? photoUrl}
                alt="Pieza patrón"
                className="absolute inset-0"
                crosshair={addMode}
                markers={analysis ? markers : []}
                renderTooltip={analysis ? renderFeatureTooltip : undefined}
                onImageClick={addMode ? addFeatureAt : undefined}
                overlay={
                  analysis && (
                    <>
                      {analysis.enabled.contour && (
                        <polygon
                          points={analysis.contour.map(([x, y]) => `${x / scale},${-y / scale}`).join(" ")}
                          fill="none"
                          stroke="#facc15"
                          strokeWidth={2}
                          vectorEffect="non-scaling-stroke"
                          pointerEvents="none"
                        />
                      )}
                      {analysis.enabled.label && analysis.label && (
                        <rect
                          x={analysis.label.x / scale - analysis.label.w / scale / 2}
                          y={-analysis.label.y / scale - analysis.label.h / scale / 2}
                          width={analysis.label.w / scale}
                          height={analysis.label.h / scale}
                          transform={`rotate(${analysis.label.angle + 90} ${analysis.label.x / scale} ${-analysis.label.y / scale})`}
                          fill="none"
                          stroke="#38bdf8"
                          strokeWidth={2}
                          vectorEffect="non-scaling-stroke"
                          pointerEvents="none"
                        />
                      )}
                    </>
                  )
                }
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
                <span className="text-sm">Seleccionar fotografía de la pieza patrón</span>
                <input type="file" accept="image/*" className="hidden" onChange={handleFile} />
              </label>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800 px-4 py-3">
            <p className="text-xs text-zinc-500">
              {analysis
                ? addMode
                  ? "Haga clic sobre la imagen para agregar un feature."
                  : "Pase el cursor sobre un feature para ver qué es; clic para cambiar su tipo."
                : "Capture o seleccione una imagen y analícela."}
            </p>
            <div className="flex gap-2">
              {source === "camera" && !photo && (
                <>
                  <button
                    type="button"
                    className={camera?.depth_available ? buttonSecondary : buttonPrimary}
                    onClick={handleCapture}
                    disabled={!camera?.running || scanning}
                  >
                    <Camera size={16} /> Capturar
                  </button>
                  {camera?.depth_available && (
                    <button
                      type="button"
                      className={buttonPrimary}
                      onClick={handleScan}
                      disabled={!camera?.running || scanning}
                      title="Foto + profundidad de la RealSense (16 cuadros promediados): mide escala real, espesor, planitud y alturas."
                    >
                      {scanning ? <LoaderCircle size={16} className="animate-spin" /> : <Scan size={16} />}
                      {scanning ? "Escaneando..." : "Escanear 3D"}
                    </button>
                  )}
                </>
              )}
              {photo && (
                <button type="button" className={buttonSecondary} onClick={discardPhoto}>
                  Descartar imagen
                </button>
              )}
              {analysis && (
                <button
                  type="button"
                  className={`${buttonSecondary} ${view === "3d" ? "border-emerald-500/60 text-emerald-400" : ""}`}
                  onClick={() => {
                    setAddMode(false);
                    setView((current) => (current === "3d" ? "2d" : "3d"));
                  }}
                >
                  <Box size={16} /> {view === "3d" ? "Vista 2D" : "Vista 3D"}
                </button>
              )}
              {analysis && view === "2d" && (
                <button
                  type="button"
                  className={`${buttonSecondary} ${addMode ? "border-emerald-500/60 text-emerald-400" : ""}`}
                  onClick={() => setAddMode((value) => !value)}
                >
                  <MousePointerClick size={16} /> {addMode ? "Terminar" : "Agregar feature"}
                </button>
              )}
            </div>
          </div>
        </section>

        {/* PANEL LATERAL */}
        <div className="space-y-6">
          <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
            <h2 className="font-semibold">Escala y análisis</h2>
            <div className="mt-4 space-y-3">
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-zinc-400">
                  Dimensión real conocida (mm) — opcional
                </span>
                <div className="flex gap-2">
                  <input
                    value={knownLength}
                    onChange={(event) => setKnownLength(event.target.value)}
                    placeholder="512.7"
                    inputMode="decimal"
                    className={inputClass}
                  />
                  <select
                    value={knownAxis}
                    onChange={(event) => setKnownAxis(event.target.value as "long" | "short")}
                    className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 text-sm"
                  >
                    <option value="long">Lado largo</option>
                    <option value="short">Lado corto</option>
                  </select>
                </div>
              </label>
              <p className="text-xs text-zinc-600">
                {scanId
                  ? "Escaneo 3D listo: sin dimensión conocida, la escala se mide con la profundidad de la cámara."
                  : "Sin dimensión conocida se usa la escala medida en Configuración › Cámara (si existe); si no, las medidas quedan en píxeles."}
              </p>
              {scanInfo && (
                <p className="rounded-lg bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
                  Profundidad capturada: {scanInfo.frames} cuadros, {(scanInfo.valid_fraction * 100).toFixed(0)} % de píxeles
                  válidos{scanInfo.noise_mm !== null ? `, ruido ${scanInfo.noise_mm.toFixed(2)} mm` : ""}.
                </p>
              )}
              <button type="button" className={`${buttonPrimary} w-full`} onClick={handleAnalyze} disabled={!photo || analyzing}>
                {analyzing ? <LoaderCircle size={16} className="animate-spin" /> : <ScanSearch size={16} />}
                {analyzing ? "Analizando..." : "Analizar pieza"}
              </button>
            </div>
          </section>

          {analysis && (
            <>
              {analysis.relief && (
                <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
                  <div className="flex items-center justify-between">
                    <h2 className="flex items-center gap-2 font-semibold">
                      <Gauge size={16} className="text-emerald-400" /> Profundidad (RealSense)
                    </h2>
                    {analysis.relief.available && (
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                          analysis.relief.measures.quality === "Buena"
                            ? "bg-emerald-500/15 text-emerald-300"
                            : analysis.relief.measures.quality === "Regular"
                              ? "bg-amber-500/15 text-amber-300"
                              : "bg-red-500/15 text-red-300"
                        }`}
                      >
                        {analysis.relief.measures.quality}
                      </span>
                    )}
                  </div>
                  {analysis.relief.available ? (
                    <>
                      <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
                        <div>
                          <dt className="text-zinc-500">Inclinación</dt>
                          <dd className={`font-medium ${analysis.relief.measures.tilt_deg > 3 ? "text-amber-300" : ""}`}>
                            {analysis.relief.measures.tilt_deg.toFixed(1)}°
                          </dd>
                        </div>
                        <div>
                          <dt className="text-zinc-500">Espesor</dt>
                          <dd className="font-medium">
                            {analysis.relief.measures.thickness_mm !== null
                              ? `${analysis.relief.measures.thickness_mm.toFixed(2)} mm`
                              : "no medido"}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-zinc-500">Planitud (P-V)</dt>
                          <dd className="font-medium">
                            {analysis.relief.measures.flatness_mm !== null
                              ? `${analysis.relief.measures.flatness_mm.toFixed(2)} mm`
                              : "—"}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-zinc-500">Distancia</dt>
                          <dd className="font-medium">{analysis.relief.measures.distance_mm.toFixed(0)} mm</dd>
                        </div>
                      </dl>
                      {analysis.relief.measures.warnings.map((warning) => (
                        <p key={warning} className="mt-3 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                          {warning}
                        </p>
                      ))}
                    </>
                  ) : (
                    <p className="mt-3 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                      {analysis.relief.reason ?? "No se pudo medir el relieve."} Se usa el modelo estimado a partir de la foto.
                    </p>
                  )}
                </section>
              )}

              <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
                <h2 className="font-semibold">Qué verificar</h2>
                <div className="mt-3 space-y-2">
                  {(Object.keys(CATEGORY_LABELS) as (keyof ReferenceCategories)[]).map((key) => (
                    <label key={key} className="flex cursor-pointer items-center gap-2 text-sm text-zinc-300">
                      <input
                        type="checkbox"
                        className="accent-emerald-500"
                        checked={analysis.enabled[key]}
                        disabled={(key === "label" && !analysis.label) || (key === "contour" && !analysis.contour.length)}
                        onChange={() => toggleCategory(key)}
                      />
                      {CATEGORY_LABELS[key]}
                      {key === "label" && !analysis.label && <span className="text-xs text-zinc-600">(no detectada)</span>}
                    </label>
                  ))}
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-2 border-t border-zinc-800 pt-4 text-xs">
                  <div>
                    <dt className="text-zinc-500">Largo</dt>
                    <dd className="font-medium">{analysis.dimensions.length.toFixed(1)} {unit}</dd>
                  </div>
                  <div>
                    <dt className="text-zinc-500">Ancho</dt>
                    <dd className="font-medium">{analysis.dimensions.width.toFixed(1)} {unit}</dd>
                  </div>
                  <div>
                    <dt className="text-zinc-500">Features activos</dt>
                    <dd className="font-medium">{activeCount} / {analysis.features.length}</dd>
                  </div>
                  <div>
                    <dt className="text-zinc-500">Escala</dt>
                    <dd className="font-medium">{analysis.relative ? "relativa" : `${(1 / scale).toFixed(2)} px/mm`}
                      {analysis.scale_source === "calibracion" ? " (calibración)" : ""}
                      {analysis.scale_source === "profundidad" ? " (profundidad)" : ""}</dd>
                  </div>
                </dl>
              </section>

              <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
                <h2 className="font-semibold">Features</h2>
                <ul className="mt-3 max-h-64 space-y-1.5 overflow-y-auto pr-1">
                  {analysis.features.map((item) => (
                    <li key={item.id} className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950/60 px-2.5 py-1.5 text-xs">
                      <input
                        type="checkbox"
                        className="accent-emerald-500"
                        checked={item.enabled}
                        onChange={(event) => updateFeature(item.id, { enabled: event.target.checked })}
                      />
                      <span className="w-6 font-semibold text-zinc-400">{item.id}</span>
                      <select
                        value={item.type}
                        onChange={(event) => setFeatureType(item.id, event.target.value)}
                        className="flex-1 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1"
                      >
                        <option value="INSERTO">Inserto</option>
                        <option value="DELRON_CONICO">Delron cónico</option>
                        <option value="DELRON_PLANO">Delron plano</option>
                        <option value="BARRENO">Barreno</option>
                        {item.type === "INSERTO_LATON" && <option value="INSERTO_LATON">Inserto (latón)</option>}
                      </select>
                      <button
                        type="button"
                        aria-label={`Eliminar feature ${item.id}`}
                        className="text-zinc-500 transition hover:text-red-400"
                        onClick={() => removeFeature(item.id)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              </section>

              <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
                <h2 className="font-semibold">Guardar como alta</h2>
                <div className="mt-4 space-y-3">
                  <input className={inputClass} placeholder="Número de parte" value={partNo} onChange={(event) => setPartNo(event.target.value)} />
                  <input className={inputClass} placeholder="Revisión" value={revision} onChange={(event) => setRevision(event.target.value)} />
                  <input className={inputClass} placeholder="Descripción (opcional)" value={description} onChange={(event) => setDescription(event.target.value)} />
                  <button type="button" className={`${buttonPrimary} w-full`} onClick={handleSave} disabled={!canSave}>
                    {saving ? <LoaderCircle size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
                    {saving ? "Guardando..." : "Guardar pieza patrón"}
                  </button>
                  {analysis.enabled.features && activeCount < 4 && (
                    <p className="text-xs text-amber-400">Se necesitan al menos 4 features activos para alinear.</p>
                  )}
                  {saved && (
                    <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
                      {saved.message} Drawing #{saved.drawing_id}, {saved.roi_count} features.
                    </p>
                  )}
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
