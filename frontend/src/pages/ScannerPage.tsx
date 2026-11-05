import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CircleStop,
  Download,
  FolderOpen,
  Gauge,
  LoaderCircle,
  Maximize2,
  Orbit,
  Pause,
  Play,
  RotateCcw,
  Save,
  Square,
  Trash2,
} from "lucide-react";

import { getCameraStatus, startCamera, stopCamera } from "../lib/api";
import {
  scannerApi,
  type MeshData,
  type PointCloudData,
  type SavedScan,
  type ScannerStatus,
} from "../lib/scannerApi";
import type { CameraStatus } from "../types/api";

// three.js pesa bastante: solo se descarga al abrir este panel.
const ScanViewer = lazy(() => import("../components/ScanViewer"));

const card = "rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5";
const primary =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40";
const secondary =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm font-semibold transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40";
const selectClass =
  "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500 disabled:opacity-40";

const RESOLUTIONS = [
  { label: "848 × 480 (recomendada)", width: 848, height: 480 },
  { label: "1280 × 720", width: 1280, height: 720 },
  { label: "640 × 360 (más rápida)", width: 640, height: 360 },
];

const minutes = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

function Chip({ tone, children }: { tone: "ok" | "warn" | "bad" | "info"; children: React.ReactNode }) {
  const tones = {
    ok: "border-emerald-500/30 bg-emerald-500/15 text-emerald-300",
    warn: "border-amber-500/30 bg-amber-500/15 text-amber-300",
    bad: "border-red-500/30 bg-red-500/15 text-red-300",
    info: "border-zinc-600 bg-zinc-900/80 text-zinc-300",
  };
  return <span className={`rounded-md border px-2 py-1 text-xs font-medium backdrop-blur ${tones[tone]}`}>{children}</span>;
}

export default function ScannerPage() {
  const [status, setStatus] = useState<ScannerStatus>();
  const [camera, setCamera] = useState<CameraStatus>();
  const [resolution, setResolution] = useState("848x480");
  const [voxel, setVoxel] = useState(2);
  const [maxDepth, setMaxDepth] = useState(1000);
  const [isolate, setIsolate] = useState(false);
  const [cloud, setCloud] = useState<PointCloudData | null>(null);
  const [mesh, setMesh] = useState<MeshData | null>(null);
  const [fitToken, setFitToken] = useState(0);
  const [showColor, setShowColor] = useState(true);
  const [wireframe, setWireframe] = useState(false);
  const [saved, setSaved] = useState<SavedScan[]>([]);
  const [saveName, setSaveName] = useState("");
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  const meshKey = useRef("");
  const cloudBusy = useRef(false);
  const state = status?.state ?? "idle";
  const scanning = state === "scanning" || state === "paused";

  const fail = (caught: unknown, fallback: string) =>
    setError(caught instanceof Error ? caught.message : fallback);

  const refreshSaved = useCallback(() => {
    scannerApi.saved().then(setSaved).catch(() => undefined);
  }, []);

  // Estado del escáner y de la cámara.
  useEffect(() => {
    let alive = true;
    let ticks = 0;
    const tick = async () => {
      try {
        const next = await scannerApi.status();
        if (alive) setStatus(next);
        if (ticks++ % 3 === 0) {
          const cam = await getCameraStatus().catch(() => undefined);
          if (alive && cam) setCamera(cam);
        }
      } catch {
        /* backend no disponible un instante */
      }
    };
    tick();
    refreshSaved();
    const timer = window.setInterval(tick, 700);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [refreshSaved]);

  // Vista previa en vivo mientras se escanea.
  useEffect(() => {
    if (!scanning) return;
    let alive = true;
    const pull = async () => {
      if (cloudBusy.current) return;
      cloudBusy.current = true;
      try {
        const next = await scannerApi.preview(60000);
        if (alive && next.count > 0) {
          setCloud((previous) => {
            if (!previous || previous.count === 0) setFitToken((token) => token + 1);
            return next;
          });
        }
      } catch {
        /* se reintenta en el siguiente ciclo */
      } finally {
        cloudBusy.current = false;
      }
    };
    pull();
    const timer = window.setInterval(pull, 1300);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [scanning]);

  // Malla final: se pide cuando hay una nueva (o cambia con/sin mesa).
  useEffect(() => {
    if (state === "idle") {
      setCloud(null);
      setMesh(null);
      meshKey.current = "";
      return;
    }
    if (state !== "done" || !status?.analysis) return;
    const key = `${status.analysis.triangles}-${status.isolated}-${status.saved_id ?? ""}`;
    if (key === meshKey.current) return;
    meshKey.current = key;
    scannerApi
      .mesh(300000)
      .then((data) => {
        setMesh(data);
        setCloud(null);
        setFitToken((token) => token + 1);
      })
      .catch((caught) => fail(caught, "No fue posible cargar la malla."));
  }, [state, status?.analysis, status?.isolated, status?.saved_id]);

  async function run<T>(label: string, action: () => Promise<T>, done?: string) {
    try {
      setBusy(label);
      setError(undefined);
      const result = await action();
      if (done) {
        setNotice(done);
        window.setTimeout(() => setNotice(undefined), 5000);
      }
      return result;
    } catch (caught) {
      fail(caught, "La operación no se pudo completar.");
      return undefined;
    } finally {
      setBusy(undefined);
    }
  }

  const [width, height] = resolution.split("x").map(Number);
  const cameraReady = Boolean(camera?.running && camera.depth_available);

  const toggleCamera = () =>
    run("camera", async () => {
      if (camera?.running) setCamera(await stopCamera());
      else setCamera(await startCamera({ provider: "realsense", width, height }));
    });

  const startScan = () =>
    run("start", async () => {
      setMesh(null);
      setCloud(null);
      meshKey.current = "";
      setStatus(await scannerApi.start(voxel, maxDepth));
    });

  const finishScan = () => run("finish", async () => setStatus(await scannerApi.finish(isolate)));
  const toggleIsolate = (value: boolean) => {
    setIsolate(value);
    if (state === "done" && status?.can_reprocess) {
      run("reprocess", async () => setStatus(await scannerApi.reprocess(value)));
    }
  };
  const resetScan = () => run("reset", async () => setStatus(await scannerApi.reset()));

  const saveScan = () =>
    run("save", async () => {
      await scannerApi.save(saveName);
      setSaveName("");
      refreshSaved();
      setStatus(await scannerApi.status());
    }, "Escaneo guardado.");

  const openSaved = (id: string) =>
    run("open", async () => {
      meshKey.current = "";
      setMesh(null);
      setStatus(await scannerApi.openSaved(id));
    });

  const removeSaved = (id: string) =>
    run("delete", async () => {
      await scannerApi.deleteSaved(id);
      refreshSaved();
    });

  const analysis = status?.analysis;
  const tone = status?.tracking === "ok" ? "ok" : status?.tracking === "perdido" ? "bad" : "info";
  const fast = (status?.speed_mm_s ?? 0) > 120;

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm font-medium text-emerald-400">Reconstrucción 3D</p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">Escáner 3D de piezas grandes</h1>
        <p className="mt-2 max-w-3xl text-sm text-zinc-500">
          Para piezas que no caben en el campo de visión: mueva la cámara RealSense cerca de la pieza, recorriéndola completa. Cada
          cuadro se alinea con lo ya escaneado y se funde en un solo modelo, que se puede medir y exportar a STL.
        </p>
      </div>

      {status && !status.available && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          <AlertTriangle size={18} className="mt-0.5 shrink-0" />
          <div>
            <b>Falta la librería del escáner (Open3D).</b> {status.install_hint} Después reinicie la aplicación. El resto de los paneles
            funciona con normalidad.
          </div>
        </div>
      )}
      {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}
      {notice && <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">{notice}</div>}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        {/* VISOR */}
        <section className="space-y-3">
          <div className="relative">
            <Suspense fallback={<div className="grid h-[70vh] place-items-center rounded-xl border border-zinc-800 bg-black"><LoaderCircle className="animate-spin text-emerald-400" /></div>}>
              <ScanViewer
                cloud={cloud}
                mesh={mesh}
                camera={scanning ? status?.camera : null}
                showColor={showColor}
                wireframe={wireframe}
                fitToken={fitToken}
                className="h-[70vh]"
              />
            </Suspense>

            {/* estado en vivo */}
            <div className="pointer-events-none absolute left-3 top-3 flex max-w-[70%] flex-wrap gap-1.5">
              {scanning && (
                <>
                  <Chip tone={tone}>
                    {status?.tracking === "ok" ? "Rastreo OK" : status?.tracking === "perdido" ? "Rastreo perdido" : "Inicializando"}
                  </Chip>
                  {state === "paused" && <Chip tone="warn">En pausa</Chip>}
                  <Chip tone="info">{status?.keyframes ?? 0} vistas · {status?.fps ?? 0} fps</Chip>
                  {status?.coverage_mm && (
                    <Chip tone="info">
                      Cubierto {status.coverage_mm.map((v) => Math.round(v)).join(" × ")} mm
                    </Chip>
                  )}
                  <Chip tone={fast ? "warn" : "info"}>
                    <Gauge size={11} className="mr-1 inline" />
                    {Math.round((status?.speed_mm_s ?? 0) / 10)} cm/s
                  </Chip>
                  <Chip tone={(status?.alignment_quality ?? 100) < 50 ? "warn" : "info"}>
                    Alineación {status?.alignment_quality ?? 100} %
                  </Chip>
                </>
              )}
              {state === "done" && analysis && (
                <Chip tone="ok">
                  Modelo {analysis.dimensions_mm.map((v) => Math.round(v)).join(" × ")} mm
                </Chip>
              )}
            </div>
            {scanning && (status?.tracking_message || fast) && (
              <div className="pointer-events-none absolute inset-x-3 bottom-14 rounded-lg border border-amber-500/30 bg-amber-500/15 px-3 py-2 text-sm text-amber-200 backdrop-blur">
                {status?.tracking_message || "Va muy rápido: mueva la cámara más despacio para no perder el rastreo."}
              </div>
            )}
            {(state === "processing" || busy === "reprocess") && (
              <div className="absolute inset-0 grid place-items-center rounded-xl bg-black/70 backdrop-blur-sm">
                <div className="text-center">
                  <LoaderCircle size={36} className="mx-auto animate-spin text-emerald-400" />
                  <p className="mt-3 font-medium">Generando la malla…</p>
                  <p className="mt-1 text-xs text-zinc-400">Fusionando el modelo y limpiando el resultado.</p>
                </div>
              </div>
            )}
            {state === "idle" && !mesh && !cloud && (
              <div className="pointer-events-none absolute inset-0 grid place-items-center">
                <div className="text-center text-zinc-500">
                  <Orbit size={44} className="mx-auto mb-3 text-zinc-700" />
                  <p className="text-sm">
                    {cameraReady ? "Pulse «Iniciar escaneo» y recorra la pieza." : "Inicie la cámara RealSense para comenzar."}
                  </p>
                </div>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={secondary} onClick={() => setFitToken((value) => value + 1)}>
              <Maximize2 size={15} /> Ajustar vista
            </button>
            <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-300">
              <input type="checkbox" className="accent-emerald-500" checked={showColor} onChange={(event) => setShowColor(event.target.checked)} />
              Color
            </label>
            <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-300">
              <input type="checkbox" className="accent-emerald-500" checked={wireframe} onChange={(event) => setWireframe(event.target.checked)} disabled={!mesh} />
              Malla
            </label>
            <span className="ml-auto text-xs text-zinc-500">Arrastrar: girar · Rueda: zoom · Clic derecho: mover</span>
          </div>
        </section>

        {/* PANEL */}
        <aside className="space-y-4">
          <section className={card}>
            <h2 className="font-semibold">1. Cámara</h2>
            <div className="mt-3 space-y-3">
              <select className={selectClass} value={resolution} onChange={(event) => setResolution(event.target.value)} disabled={Boolean(camera?.running)}>
                {RESOLUTIONS.map((item) => (
                  <option key={item.label} value={`${item.width}x${item.height}`}>{item.label}</option>
                ))}
              </select>
              <button type="button" className={camera?.running ? secondary : primary} onClick={toggleCamera} disabled={busy === "camera" || scanning || state === "processing"}>
                {busy === "camera" ? <LoaderCircle size={16} className="animate-spin" /> : camera?.running ? <CircleStop size={16} /> : <Play size={16} />}
                {camera?.running ? "Detener cámara" : "Iniciar RealSense"}
              </button>
              {camera?.running && !camera.depth_available && (
                <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  La cámara no entrega profundidad. Se necesita una Intel RealSense por USB 3 (con USB 2 solo hay color).
                </p>
              )}
              {cameraReady && <p className="text-xs text-emerald-400">RealSense con profundidad lista.</p>}
            </div>
          </section>

          <section className={card}>
            <h2 className="font-semibold">2. Escaneo</h2>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <label className="text-xs text-zinc-400">
                Resolución del modelo
                <select className={`${selectClass} mt-1`} value={voxel} onChange={(event) => setVoxel(Number(event.target.value))} disabled={scanning || state === "processing"}>
                  <option value={1}>1 mm (fino, lento)</option>
                  <option value={2}>2 mm (recomendado)</option>
                  <option value={3}>3 mm</option>
                  <option value={5}>5 mm (rápido)</option>
                </select>
              </label>
              <label className="text-xs text-zinc-400">
                Distancia máxima
                <select className={`${selectClass} mt-1`} value={maxDepth} onChange={(event) => setMaxDepth(Number(event.target.value))} disabled={scanning || state === "processing"}>
                  <option value={600}>60 cm</option>
                  <option value={1000}>1 m</option>
                  <option value={1500}>1.5 m</option>
                </select>
              </label>
            </div>
            <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm text-zinc-300">
              <input type="checkbox" className="mt-0.5 accent-emerald-500" checked={isolate} onChange={(event) => toggleIsolate(event.target.checked)} disabled={state === "processing"} />
              <span>
                Aislar la pieza (quitar la mesa)
                <span className="block text-xs text-zinc-500">Funciona si se ve la mesa alrededor o por los huecos de la pieza.</span>
              </span>
            </label>

            <div className="mt-4 grid grid-cols-2 gap-2">
              {state === "idle" || state === "done" || state === "error" ? (
                <button type="button" className={`${primary} col-span-2`} onClick={startScan} disabled={!cameraReady || !status?.available || busy === "start"}>
                  {busy === "start" ? <LoaderCircle size={16} className="animate-spin" /> : <Play size={16} />}
                  {state === "done" ? "Nuevo escaneo" : "Iniciar escaneo"}
                </button>
              ) : (
                <>
                  {state === "paused" ? (
                    <button type="button" className={primary} onClick={() => run("resume", async () => setStatus(await scannerApi.resume()))}>
                      <Play size={16} /> Reanudar
                    </button>
                  ) : (
                    <button type="button" className={secondary} onClick={() => run("pause", async () => setStatus(await scannerApi.pause()))} disabled={state === "processing"}>
                      <Pause size={16} /> Pausar
                    </button>
                  )}
                  <button type="button" className={primary} onClick={finishScan} disabled={busy === "finish" || (status?.keyframes ?? 0) < 3 || state === "processing"}>
                    <Square size={16} /> Terminar
                  </button>
                </>
              )}
              {(scanning || state === "done" || state === "error") && (
                <button type="button" className={`${secondary} col-span-2`} onClick={resetScan} disabled={busy === "reset"}>
                  <RotateCcw size={16} /> Descartar y reiniciar
                </button>
              )}
            </div>
            {scanning && (
              <dl className="mt-4 grid grid-cols-3 gap-2 text-center text-xs">
                <div className="rounded-lg bg-zinc-950 p-2"><dt className="text-zinc-500">Tiempo</dt><dd className="mt-0.5 font-medium">{minutes(status?.elapsed_s ?? 0)}</dd></div>
                <div className="rounded-lg bg-zinc-950 p-2"><dt className="text-zinc-500">Cuadros</dt><dd className="mt-0.5 font-medium">{status?.frames ?? 0}</dd></div>
                <div className="rounded-lg bg-zinc-950 p-2"><dt className="text-zinc-500">Perdidos</dt><dd className={`mt-0.5 font-medium ${(status?.lost_total ?? 0) > 0 ? "text-amber-300" : ""}`}>{status?.lost_total ?? 0}</dd></div>
              </dl>
            )}
            {state === "error" && status?.message && <p className="mt-3 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">{status.message}</p>}
            {!scanning && state !== "done" && (
              <ul className="mt-4 list-disc space-y-1 pl-4 text-xs text-zinc-500">
                <li>Mantenga la cámara a 20–60 cm y muévala despacio (menos de 10 cm/s).</li>
                <li>Recorra la pieza en pasadas que se traslapen, sin saltos.</li>
                <li>Necesita textura: apoye la pieza en una mesa con veta o papel impreso.</li>
                <li>Si el rastreo se pierde, vuelva a una zona ya escaneada.</li>
              </ul>
            )}
          </section>

          {state === "done" && analysis && (
            <section className={card}>
              <h2 className="font-semibold">3. Resultado</h2>
              <dl className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                {["Largo", "Ancho", "Alto"].map((label, index) => (
                  <div key={label} className="rounded-lg bg-zinc-950 p-2">
                    <dt className="text-zinc-500">{label}</dt>
                    <dd className="mt-0.5 text-sm font-semibold">{analysis.dimensions_mm[index].toFixed(1)} mm</dd>
                  </div>
                ))}
              </dl>
              <dl className="mt-3 space-y-1.5 text-xs">
                <div className="flex justify-between"><dt className="text-zinc-500">Superficie</dt><dd>{(analysis.area_mm2 / 100).toFixed(0)} cm²</dd></div>
                <div className="flex justify-between"><dt className="text-zinc-500">Triángulos</dt><dd>{analysis.triangles.toLocaleString()}</dd></div>
                {analysis.flatness && (
                  <>
                    <div className="flex justify-between"><dt className="text-zinc-500">Planitud (P-V)</dt><dd>{analysis.flatness.peak_to_valley_mm.toFixed(2)} mm</dd></div>
                    <div className="flex justify-between"><dt className="text-zinc-500">Rugosidad (RMS)</dt><dd>{analysis.flatness.rms_mm.toFixed(2)} mm</dd></div>
                    <div className="flex justify-between"><dt className="text-zinc-500">Cara plana</dt><dd>{Math.round(analysis.flatness.planar_fraction * 100)} %</dd></div>
                  </>
                )}
                <div className="flex justify-between"><dt className="text-zinc-500">Mesa</dt><dd>{status?.isolated ? "quitada" : "incluida"}</dd></div>
              </dl>
              {isolate && !status?.isolated && status?.isolate_message && (
                <p className="mt-3 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{status.isolate_message}</p>
              )}
              <p className="mt-3 text-[11px] leading-snug text-zinc-500">
                Precisión aproximada del 1–2 % del recorrido (el rastreo acumula deriva). Para medidas críticas, verifique con el escaneo más
                lento y con pasadas traslapadas.
              </p>
              <div className="mt-4 grid grid-cols-3 gap-2">
                {(["stl", "ply", "obj"] as const).map((format) => (
                  <a key={format} href={scannerApi.exportUrl(format)} download className={`${format === "stl" ? primary : secondary} !px-2`}>
                    <Download size={15} /> {format.toUpperCase()}
                  </a>
                ))}
              </div>
              <div className="mt-3 flex gap-2">
                <input
                  value={saveName}
                  onChange={(event) => setSaveName(event.target.value)}
                  placeholder="Nombre del escaneo"
                  className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-emerald-500"
                />
                <button type="button" className={secondary} onClick={saveScan} disabled={busy === "save" || Boolean(status?.saved_id)}>
                  <Save size={15} /> {status?.saved_id ? "Guardado" : "Guardar"}
                </button>
              </div>
            </section>
          )}

          {saved.length > 0 && (
            <section className={card}>
              <h2 className="font-semibold">Escaneos guardados</h2>
              <ul className="mt-3 max-h-60 space-y-2 overflow-y-auto pr-1">
                {saved.map((item) => (
                  <li key={item.id} className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-xs">
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-zinc-200">{item.name}</p>
                      <p className="text-zinc-500">
                        {item.analysis?.dimensions_mm.map((v) => Math.round(v)).join(" × ")} mm · {new Date(item.created_at).toLocaleDateString()}
                      </p>
                    </div>
                    <button type="button" title="Abrir" className="text-zinc-400 hover:text-emerald-400 disabled:opacity-40" onClick={() => openSaved(item.id)} disabled={scanning || state === "processing"}>
                      <FolderOpen size={16} />
                    </button>
                    <button type="button" title="Eliminar" className="text-zinc-500 hover:text-red-400" onClick={() => removeSaved(item.id)}>
                      <Trash2 size={15} />
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
