import type { Status } from "../types/api";
export default function StatusBadge({ status }: { status: Status }) {
  const colors = status === "OK" ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-400" : status === "REVISAR" ? "border-amber-500/30 bg-amber-500/15 text-amber-400" : status === "FALLO" ? "border-red-500/30 bg-red-500/15 text-red-400" : "border-zinc-700 bg-zinc-800 text-zinc-400";
  return <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${colors}`}>{status}</span>;
}
