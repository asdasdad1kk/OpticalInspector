import { useEffect, useState } from "react";
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

interface Props {
  value: CameraStartOptions;
  onChange: (value: CameraStartOptions) => void;
  disabled?: boolean;
}

export default function CameraSelector({ value, onChange, disabled }: Props) {
  const [devices, setDevices] = useState<CameraDevice[]>([]);

  useEffect(() => {
    listCameraDevices()
      .then((response) => setDevices(response.devices))
      .catch(() => setDevices([]));
  }, [disabled]);

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

      {provider === "brio" && available.length > 1 && (
        <select
          aria-label="Dispositivo"
          className={selectClass}
          disabled={disabled}
          value={value.deviceIndex ?? 0}
          onChange={(event) => update({ ...value, deviceIndex: Number(event.target.value) })}
        >
          {available.map((device) => (
            <option key={device.index} value={device.index}>
              {device.name}
            </option>
          ))}
        </select>
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
