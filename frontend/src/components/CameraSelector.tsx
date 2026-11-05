import { useEffect, useState } from "react";
import { LoaderCircle, RefreshCw } from "lucide-react";
import { listCameraDevices, type CameraStartOptions } from "../lib/api";
import type { CameraDevice, CameraProvider } from "../types/api";

const STORAGE_KEY = "optical-inspector.camera";

const PROVIDER_LABEL: Record<CameraProvider, string> = {
  realsense: "Intel RealSense",
  brio: "Logitech BRIO",
};

const RESOLUTIONS = [
  { label: "1280 × 720", width: 1280, height: 720 },
  { label: "1920 × 1080", width: 1920, height: 1080 },
  { label: "3840 × 2160 (4K)", width: 3840, height: 2160 },
];

export function loadCameraOptions(): CameraStartOptions {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return JSON.parse(saved);
  } catch {
    /* sin almacenamiento disponible */
  }
  return { provider: "realsense", width: 1280, height: 720 };
}

/** Olvida la camara/resolucion guardadas en este equipo (vuelve a RealSense 1280x720). */
export function resetCameraOptions() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* sin almacenamiento disponible */
  }
}

interface Props {
  value: CameraStartOptions;
  onChange: (value: CameraStartOptions) => void;
  disabled?: boolean;
}

export default function CameraSelector({ value, onChange, disabled }: Props) {
  const [devices, setDevices] = useState<CameraDevice[]>([]);
  const [searching, setSearching] = useState(false);
  const [notice, setNotice] = useState<string>();

  // Al montar solo se lee la lista en cache del backend; el sondeo de
  // dispositivos (lento) se hace unicamente con el boton "Buscar cámaras".
  useEffect(() => {
    listCameraDevices()
      .then((response) => setDevices(response.devices))
      .catch(() => undefined);
  }, []);

  async function searchDevices() {
    setSearching(true);
    setNotice(undefined);
    try {
      const response = await listCameraDevices(true);
      setDevices(response.devices);
      const found = response.devices.filter((device) => device.provider === provider);
      if (response.message) setNotice(response.message);
      else if (!found.length) setNotice("No se encontraron cámaras.");
      if (found.length && !found.some((device) => device.index === (value.deviceIndex ?? 0))) {
        update({ ...value, deviceIndex: found[0].index });
      }
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : "No fue posible buscar cámaras.");
    } finally {
      setSearching(false);
    }
  }

  function update(next: CameraStartOptions) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* ignorar */
    }
    onChange(next);
  }

  const provider = value.provider ?? "realsense";
  const available = devices.filter((device) => device.provider === provider);
  const resolutionKey = `${value.width ?? 1280}x${value.height ?? 720}`;
  const selectClass =
    "rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 outline-none transition focus:border-emerald-500/60 disabled:opacity-40";

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        aria-label="Proveedor de cámara"
        className={selectClass}
        disabled={disabled}
        value={provider}
        onChange={(event) =>
          update({
            ...value,
            provider: event.target.value as CameraProvider,
            deviceIndex: 0,
          })
        }
      >
        {(Object.keys(PROVIDER_LABEL) as CameraProvider[]).map((key) => (
          <option key={key} value={key}>
            {PROVIDER_LABEL[key]}
          </option>
        ))}
      </select>

      {provider === "brio" && (
        <>
          <select
            aria-label="Dispositivo"
            className={selectClass}
            disabled={disabled || searching}
            value={value.deviceIndex ?? 0}
            onChange={(event) => update({ ...value, deviceIndex: Number(event.target.value) })}
          >
            {!available.some((device) => device.index === (value.deviceIndex ?? 0)) && (
              <option value={value.deviceIndex ?? 0}>
                {available.length ? `Cámara USB #${value.deviceIndex ?? 0}` : "Sin buscar — use Buscar cámaras"}
              </option>
            )}
            {available.map((device) => (
              <option key={device.index} value={device.index}>
                {device.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={searchDevices}
            disabled={disabled || searching}
            title={disabled ? "Detenga la cámara para buscar dispositivos" : "Buscar cámaras conectadas"}
            className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {searching ? <LoaderCircle size={15} className="animate-spin" /> : <RefreshCw size={15} />}
            {searching ? "Buscando..." : "Buscar cámaras"}
          </button>
          {notice && <span className="text-xs text-amber-400">{notice}</span>}
        </>
      )}

      <select
        aria-label="Resolución"
        className={selectClass}
        disabled={disabled}
        value={resolutionKey}
        onChange={(event) => {
          const [width, height] = event.target.value.split("x").map(Number);
          update({ ...value, width, height });
        }}
      >
        {RESOLUTIONS.map((resolution) => (
          <option key={resolution.label} value={`${resolution.width}x${resolution.height}`}>
            {resolution.label}
          </option>
        ))}
      </select>
    </div>
  );
}
