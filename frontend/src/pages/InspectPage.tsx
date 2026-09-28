import { useEffect, useMemo, useState } from "react";
import {
  Camera,
  CheckCircle2,
  CircleStop,
  FileImage,
  Image as ImageIcon,
  Images,
  LoaderCircle,
  Play,
  ScanLine,
  UploadCloud,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";

import {
  getCameraStatus,
  inspectCapture,
  inspectionImageUrl,
  inspectPhoto,
  startCamera,
  stopCamera,
} from "../lib/api";
import { useCameraStream } from "../hooks/useCameraStream";
import type { CameraStatus, InspectionResponse } from "../types/api";
import StatusBadge from "../components/StatusBadge";

type InspectionSource = "camera" | "photo";
type ResultImageType =
  | "overlay-image"
  | "rectified-image"
  | "original-image";

export default function InspectPage() {
  const [partNo, setPartNo] = useState("");
  const [inspector, setInspector] = useState("");
  const [inspectionSource, setInspectionSource] =
    useState<InspectionSource>("camera");
  const [photo, setPhoto] = useState<File>();
  const [localPhotoUrl, setLocalPhotoUrl] = useState<string>();
  const [camera, setCamera] = useState<CameraStatus>();
  const [loadingCamera, setLoadingCamera] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const [error, setError] = useState<string>();
  const [result, setResult] = useState<InspectionResponse>();
  const [activeImage, setActiveImage] =
    useState<ResultImageType>("overlay-image");

  const stream = useCameraStream(
    inspectionSource === "camera" && Boolean(camera?.running),
  );

  useEffect(() => {
    getCameraStatus().then(setCamera).catch(() => undefined);
  }, []);

  useEffect(() => {
    return () => {
      if (localPhotoUrl) {
        URL.revokeObjectURL(localPhotoUrl);
      }
    };
  }, [localPhotoUrl]);

  async function handleStart() {
    try {
      setLoadingCamera(true);
      setError(undefined);
      setCamera(await startCamera());
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "No fue posible iniciar la cámara.",
      );
    } finally {
      setLoadingCamera(false);
    }
  }

  async function handleStop() {
    try {
      setLoadingCamera(true);
      setError(undefined);
      setCamera(await stopCamera());
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "No fue posible detener la cámara.",
      );
    } finally {
      setLoadingCamera(false);
    }
  }

  function handleSourceChange(source: InspectionSource) {
    setInspectionSource(source);
    setError(undefined);
    setResult(undefined);
  }

  function handlePhotoChange(event: React.ChangeEvent<HTMLInputElement>) {
    const selectedPhoto = event.target.files?.[0];

    setError(undefined);
    setResult(undefined);

    if (localPhotoUrl) {
      URL.revokeObjectURL(localPhotoUrl);
    }

    if (!selectedPhoto) {
      setPhoto(undefined);
      setLocalPhotoUrl(undefined);
      return;
    }

    const validTypes = [
      "image/jpeg",
      "image/png",
      "image/webp",
      "image/bmp",
    ];

    if (!validTypes.includes(selectedPhoto.type)) {
      setPhoto(undefined);
      setLocalPhotoUrl(undefined);
      setError("Selecciona una imagen JPG, PNG, WEBP o BMP.");
      event.target.value = "";
      return;
    }

    if (selectedPhoto.size > 25 * 1024 * 1024) {
      setPhoto(undefined);
      setLocalPhotoUrl(undefined);
      setError("La fotografía no debe exceder 25 MB.");
      event.target.value = "";
      return;
    }

    setPhoto(selectedPhoto);
    setLocalPhotoUrl(URL.createObjectURL(selectedPhoto));
  }

  function removePhoto() {
    if (localPhotoUrl) {
      URL.revokeObjectURL(localPhotoUrl);
    }

    setPhoto(undefined);
    setLocalPhotoUrl(undefined);
    setResult(undefined);
  }

  async function handleInspect() {
    if (!partNo.trim() || !inspector.trim()) {
      setError("Captura número de parte e inspector.");
      return;
    }

    if (
      inspectionSource === "camera" &&
      (!camera?.running || !stream.imageUrl)
    ) {
      setError(
        "Inicia la cámara y espera a que aparezca la imagen en vivo.",
      );
      return;
    }

    if (inspectionSource === "photo" && !photo) {
      setError("Selecciona una fotografía para inspeccionar.");
      return;
    }

    try {
      setInspecting(true);
      setError(undefined);
      setResult(undefined);

      const response =
        inspectionSource === "camera"
          ? await inspectCapture(partNo, inspector)
          : await inspectPhoto(partNo, inspector, photo!);

      setResult(response);
      setActiveImage("overlay-image");
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "No fue posible completar la inspección.",
      );
    } finally {
      setInspecting(false);
    }
  }

  const resultUrl = useMemo(() => {
    if (!result) return undefined;
    return inspectionImageUrl(result.inspection_id, activeImage);
  }, [result, activeImage]);

  const ready = Boolean(
    partNo.trim() &&
      inspector.trim() &&
      (inspectionSource === "camera"
        ? camera?.running && stream.imageUrl
        : photo),
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-sm font-medium text-emerald-400">
            Estación de inspección
          </p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">
            Inspección de panel
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-zinc-500">
            Inspecciona el último frame de la RealSense o selecciona una
            fotografía existente para compararla contra el drawing almacenado.
          </p>
        </div>

        {inspectionSource === "camera" && (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleStart}
              disabled={loadingCamera || camera?.running}
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-zinc-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {loadingCamera ? (
                <LoaderCircle size={16} className="animate-spin" />
              ) : (
                <Play size={16} />
              )}
              Iniciar
            </button>

            <button
              type="button"
              onClick={handleStop}
              disabled={loadingCamera || !camera?.running}
              className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm font-semibold transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <CircleStop size={16} />
              Detener
            </button>
          </div>
        )}
      </div>

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
          <div>
            <h2 className="text-sm font-semibold">Fuente de inspección</h2>
            <p className="mt-1 text-xs text-zinc-500">
              Selecciona de dónde se obtendrá la imagen del panel.
            </p>
          </div>

          <div className="flex w-fit items-center gap-1 rounded-xl border border-zinc-800 bg-zinc-950 p-1">
            <button
              type="button"
              onClick={() => handleSourceChange("camera")}
              className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition ${
                inspectionSource === "camera"
                  ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/20"
                  : "text-zinc-400 hover:bg-zinc-800 hover:text-white"
              }`}
            >
              <Camera size={16} />
              Cámara en vivo
            </button>

            <button
              type="button"
              onClick={() => handleSourceChange("photo")}
              className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition ${
                inspectionSource === "photo"
                  ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/20"
                  : "text-zinc-400 hover:bg-zinc-800 hover:text-white"
              }`}
            >
              <Images size={16} />
              Subir fotografía
            </button>
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_390px]">
        <section className="overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/60 shadow-2xl">
          <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
            <div className="flex items-center gap-2">
              {inspectionSource === "camera" ? (
                <Camera size={17} className="text-emerald-400" />
              ) : (
                <FileImage size={17} className="text-emerald-400" />
              )}
              <span className="text-sm font-medium">
                {inspectionSource === "camera"
                  ? "Vista en vivo"
                  : "Fotografía seleccionada"}
              </span>
            </div>

            {inspectionSource === "camera" ? (
              <div className="flex items-center gap-4 text-xs text-zinc-400">
                <span className="flex items-center gap-1.5">
                  {stream.connected ? (
                    <Wifi size={14} className="text-emerald-400" />
                  ) : (
                    <WifiOff size={14} className="text-red-400" />
                  )}
                  {stream.connected ? "WebSocket conectado" : "Desconectado"}
                </span>
                <span>
                  {camera?.width ?? 1280} × {camera?.height ?? 720} ·{" "}
                  {camera?.fps ?? 30} FPS
                </span>
              </div>
            ) : (
              photo && (
                <div className="flex items-center gap-2 text-xs text-zinc-400">
                  <CheckCircle2 size={14} className="text-emerald-400" />
                  {(photo.size / 1024 / 1024).toFixed(2)} MB
                </div>
              )
            )}
          </div>

          <div className="relative aspect-video bg-black">
            {inspectionSource === "camera" ? (
              stream.imageUrl ? (
                <img
                  src={stream.imageUrl}
                  className="h-full w-full object-contain"
                  alt="Vista en vivo de la RealSense"
                />
              ) : (
                <div className="absolute inset-0 grid place-items-center">
                  <div className="text-center">
                    <Camera className="mx-auto mb-3 text-zinc-700" size={48} />
                    <p className="text-sm text-zinc-500">
                      {camera?.running
                        ? stream.message
                        : "Inicia la cámara para visualizar el panel"}
                    </p>
                  </div>
                </div>
              )
            ) : localPhotoUrl ? (
              <>
                <img
                  src={localPhotoUrl}
                  className="h-full w-full object-contain"
                  alt="Fotografía seleccionada para inspección"
                />
                <button
                  type="button"
                  onClick={removePhoto}
                  className="absolute right-4 top-4 grid h-9 w-9 place-items-center rounded-lg border border-zinc-700 bg-zinc-950/90 text-zinc-400 backdrop-blur transition hover:bg-red-500/15 hover:text-red-400"
                  aria-label="Quitar fotografía"
                >
                  <X size={18} />
                </button>
              </>
            ) : (
              <label className="absolute inset-0 flex cursor-pointer flex-col items-center justify-center transition hover:bg-zinc-900/40">
                <div className="grid h-14 w-14 place-items-center rounded-2xl bg-emerald-500/10">
                  <UploadCloud size={28} className="text-emerald-400" />
                </div>
                <p className="mt-4 font-medium text-zinc-200">
                  Seleccionar fotografía
                </p>
                <p className="mt-1 text-xs text-zinc-500">
                  JPG, PNG, WEBP o BMP, máximo 25 MB
                </p>
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/bmp"
                  className="hidden"
                  onChange={handlePhotoChange}
                />
              </label>
            )}

            {inspecting && (
              <div className="absolute inset-0 grid place-items-center bg-zinc-950/80 backdrop-blur-sm">
                <div className="flex flex-col items-center">
                  <LoaderCircle
                    className="animate-spin text-emerald-400"
                    size={42}
                  />
                  <p className="mt-3 font-medium">Procesando inspección</p>
                  <p className="mt-1 text-xs text-zinc-400">
                    Rectificando panel y comparando ROIs
                  </p>
                </div>
              </div>
            )}

            <div className="pointer-events-none absolute inset-6 rounded-xl border border-emerald-400/20">
              <span className="absolute left-1/2 top-0 -translate-x-1/2 -translate-y-1/2 whitespace-nowrap bg-black/80 px-2 text-[10px] uppercase tracking-[.25em] text-emerald-400">
                Área de inspección
              </span>
            </div>
          </div>

          <div className="flex items-center justify-between border-t border-zinc-800 px-4 py-3 text-xs text-zinc-500">
            <span>
              {inspectionSource === "camera"
                ? camera?.device_name ?? "Intel RealSense"
                : photo?.name ?? "Sin fotografía"}
            </span>
            <span>
              {inspectionSource === "camera"
                ? `Frame #${camera?.sequence ?? 0}`
                : photo?.type ?? "Archivo local"}
            </span>
          </div>
        </section>

        <aside className="space-y-4">
          <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
            <h2 className="font-semibold">Datos de inspección</h2>
            <div className="mt-4 space-y-4">
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-zinc-400">
                  Número de parte
                </span>
                <input
                  value={partNo}
                  onChange={(event) => setPartNo(event.target.value)}
                  placeholder="62P2554000A273"
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm outline-none placeholder:text-zinc-700 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/15"
                />
              </label>

              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-zinc-400">
                  Inspector
                </span>
                <input
                  value={inspector}
                  onChange={(event) => setInspector(event.target.value)}
                  placeholder="fn035575"
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm outline-none placeholder:text-zinc-700 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/15"
                />
              </label>

              {inspectionSource === "photo" && (
                <label className="flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-sm text-zinc-300 transition hover:border-emerald-500/40 hover:bg-zinc-800">
                  <UploadCloud size={17} />
                  {photo ? "Cambiar fotografía" : "Seleccionar fotografía"}
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp,image/bmp"
                    className="hidden"
                    onChange={handlePhotoChange}
                  />
                </label>
              )}

              <button
                type="button"
                onClick={handleInspect}
                disabled={!ready || inspecting}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-500 px-4 py-3 font-semibold text-zinc-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {inspecting ? (
                  <LoaderCircle size={18} className="animate-spin" />
                ) : (
                  <ScanLine size={18} />
                )}
                {inspecting
                  ? "Inspeccionando..."
                  : inspectionSource === "camera"
                    ? "Capturar e inspeccionar"
                    : "Enviar e inspeccionar"}
              </button>
            </div>
          </section>

          {inspectionSource === "camera" ? (
            <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold">Estado de cámara</h2>
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    camera?.running
                      ? "bg-emerald-400 shadow-[0_0_12px_#22c55e]"
                      : "bg-zinc-600"
                  }`}
                />
              </div>
              <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-lg bg-zinc-950 p-3">
                  <dt className="text-xs text-zinc-500">Pipeline</dt>
                  <dd className="mt-1 font-medium">
                    {camera?.running ? "Activo" : "Detenido"}
                  </dd>
                </div>
                <div className="rounded-lg bg-zinc-950 p-3">
                  <dt className="text-xs text-zinc-500">Frame</dt>
                  <dd className="mt-1 font-medium">
                    {stream.imageUrl ? "Disponible" : "Pendiente"}
                  </dd>
                </div>
                <div className="col-span-2 rounded-lg bg-zinc-950 p-3">
                  <dt className="text-xs text-zinc-500">Serial</dt>
                  <dd className="mt-1 truncate font-mono text-xs">
                    {camera?.serial_number ?? "No disponible"}
                  </dd>
                </div>
              </dl>
            </section>
          ) : (
            <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
              <h2 className="font-semibold">Archivo seleccionado</h2>
              {photo ? (
                <dl className="mt-4 space-y-3 text-sm">
                  <div className="rounded-lg bg-zinc-950 p-3">
                    <dt className="text-xs text-zinc-500">Nombre</dt>
                    <dd className="mt-1 truncate font-medium">{photo.name}</dd>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="rounded-lg bg-zinc-950 p-3">
                      <dt className="text-xs text-zinc-500">Tipo</dt>
                      <dd className="mt-1 truncate font-medium">
                        {photo.type}
                      </dd>
                    </div>
                    <div className="rounded-lg bg-zinc-950 p-3">
                      <dt className="text-xs text-zinc-500">Tamaño</dt>
                      <dd className="mt-1 font-medium">
                        {(photo.size / 1024 / 1024).toFixed(2)} MB
                      </dd>
                    </div>
                  </div>
                </dl>
              ) : (
                <div className="mt-4 rounded-lg border border-dashed border-zinc-700 bg-zinc-950 p-5 text-center text-xs text-zinc-500">
                  No se ha seleccionado ninguna fotografía.
                </div>
              )}
            </section>
          )}
        </aside>
      </div>

      {result && (
        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
            <div className="flex items-center gap-3">
              <StatusBadge status={result.status} />
              <div>
                <h2 className="font-semibold">
                  Resultado de inspección #{result.inspection_id}
                </h2>
                <p className="text-xs text-zinc-500">
                  Alineación {result.alignment_method} ·{" "}
                  {result.alignment_inliers} inliers ·{" "}
                  {inspectionSource === "camera" ? "RealSense" : "Fotografía"}
                </p>
              </div>
            </div>
            <div className="text-right">
              <div className="text-3xl font-bold text-emerald-400">
                {result.score.toFixed(1)}%
              </div>
              <div className="text-xs text-zinc-500">Score promedio</div>
            </div>
          </div>

          <div className="mt-5 grid gap-5 lg:grid-cols-[1fr_320px]">
            <div className="overflow-hidden rounded-xl border border-zinc-800 bg-black">
              {resultUrl && (
                <img
                  src={resultUrl}
                  alt="Resultado de inspección"
                  className="aspect-video h-full w-full object-contain"
                />
              )}
            </div>

            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-2">
                {(["OK", "REVISAR", "FALLO"] as const).map((key) => (
                  <div
                    key={key}
                    className="rounded-xl border border-zinc-800 bg-zinc-950 p-3 text-center"
                  >
                    <div
                      className={`text-2xl font-bold ${
                        key === "OK"
                          ? "text-emerald-400"
                          : key === "REVISAR"
                            ? "text-amber-400"
                            : "text-red-400"
                      }`}
                    >
                      {result.summary[key] ?? 0}
                    </div>
                    <div className="mt-1 text-[11px] text-zinc-500">
                      {key}
                    </div>
                  </div>
                ))}
              </div>

              <div className="space-y-2">
                {(
                  [
                    ["overlay-image", "Resultado"],
                    ["rectified-image", "Panel rectificado"],
                    ["original-image", "Imagen original"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    type="button"
                    key={value}
                    onClick={() => setActiveImage(value)}
                    className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2.5 text-sm ${
                      activeImage === value
                        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
                        : "border-zinc-800 bg-zinc-950 text-zinc-400 hover:bg-zinc-800"
                    }`}
                  >
                    <ImageIcon size={16} />
                    {label}
                  </button>
                ))}
              </div>

              <div className="rounded-xl bg-zinc-950 p-4 text-sm">
                <div className="flex justify-between">
                  <span className="text-zinc-500">ROIs analizados</span>
                  <span className="font-medium">{result.roi_count}</span>
                </div>
                <div className="mt-2 flex justify-between">
                  <span className="text-zinc-500">Drawing ID</span>
                  <span className="font-medium">{result.drawing_id}</span>
                </div>
                <div className="mt-2 flex justify-between">
                  <span className="text-zinc-500">Inspector</span>
                  <span className="font-medium">{result.inspector}</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
