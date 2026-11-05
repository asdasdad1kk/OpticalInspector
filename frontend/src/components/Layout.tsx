import {
  Camera,
  Database,
  FileUp,
  Orbit,
  ScanSearch,
  Settings,
  ScanLine,
  User,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

export default function Layout() {
  const link = ({ isActive }: { isActive: boolean }) =>
    [
      "group relative flex h-16 items-center gap-2 px-4",
      "text-sm font-medium transition-colors duration-200",
      isActive
        ? "text-white"
        : "text-zinc-500 hover:text-zinc-200",
    ].join(" ");

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* HEADER */}
      <header className="border-b border-zinc-800/80 bg-zinc-950">
        <div className="mx-auto flex h-16 max-w-[1900px] items-center px-5 lg:px-8">
          
          {/* BRAND */}
          <div className="flex min-w-[260px] items-center gap-3">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-emerald-500/20 bg-emerald-500/10 text-emerald-400">
              <ScanLine size={19} strokeWidth={1.8} />
            </div>

            <div className="leading-tight">
              <div className="flex items-center gap-2">
                <h1 className="text-sm font-semibold tracking-tight text-zinc-100">
                  Optical Inspector
                </h1>

                <span className="rounded border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-zinc-500">
                  Vision
                </span>
              </div>

              <p className="mt-0.5 text-[11px] font-medium tracking-wide text-zinc-600">
                CAD INSPECTION SYSTEM
              </p>
            </div>
          </div>

          {/* NAVIGATION */}
          <nav
            aria-label="Main navigation"
            className="ml-8 flex h-full items-center"
          >
            <NavLink to="/upload" className={link}>
              {({ isActive }) => (
                <>
                  <FileUp
                    size={16}
                    strokeWidth={1.8}
                    className={
                      isActive
                        ? "text-emerald-400"
                        : "text-zinc-500 transition-colors group-hover:text-zinc-300"
                    }
                  />

                  <span>Drawing</span>

                  {isActive && (
                    <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-t bg-emerald-400" />
                  )}
                </>
              )}
            </NavLink>

            <NavLink to="/reference" className={link}>
              {({ isActive }) => (
                <>
                  <ScanSearch
                    size={16}
                    strokeWidth={1.8}
                    className={
                      isActive
                        ? "text-emerald-400"
                        : "text-zinc-500 transition-colors group-hover:text-zinc-300"
                    }
                  />

                  <span>Pieza real</span>

                  {isActive && (
                    <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-t bg-emerald-400" />
                  )}
                </>
              )}
            </NavLink>

            <NavLink to="/inspect" className={link}>
              {({ isActive }) => (
                <>
                  <Camera
                    size={16}
                    strokeWidth={1.8}
                    className={
                      isActive
                        ? "text-emerald-400"
                        : "text-zinc-500 transition-colors group-hover:text-zinc-300"
                    }
                  />

                  <span>Inspection</span>

                  {isActive && (
                    <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-t bg-emerald-400" />
                  )}
                </>
              )}
            </NavLink>

            <NavLink to="/database" className={link}>
              {({ isActive }) => (
                <>
                  <Database
                    size={16}
                    strokeWidth={1.8}
                    className={
                      isActive
                        ? "text-emerald-400"
                        : "text-zinc-500 transition-colors group-hover:text-zinc-300"
                    }
                  />

                  <span>Database</span>

                  {isActive && (
                    <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-t bg-emerald-400" />
                  )}
                </>
              )}
            </NavLink>

            <NavLink to="/scanner" className={link}>
              {({ isActive }) => (
                <>
                  <Orbit
                    size={16}
                    strokeWidth={1.8}
                    className={
                      isActive
                        ? "text-emerald-400"
                        : "text-zinc-500 transition-colors group-hover:text-zinc-300"
                    }
                  />

                  <span>Escáner 3D</span>

                  {isActive && (
                    <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-t bg-emerald-400" />
                  )}
                </>
              )}
            </NavLink>
            <NavLink to="/settings" className={link}>
              {({ isActive }) => (
                <>
                  <Settings
                    size={16}
                    strokeWidth={1.8}
                    className={
                      isActive
                        ? "text-emerald-400"
                        : "text-zinc-500 transition-colors group-hover:text-zinc-300"
                    }
                  />

                  <span>Configuración</span>

                  {isActive && (
                    <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-t bg-emerald-400" />
                  )}
                </>
              )}
            </NavLink>
          </nav>

          {/* RIGHT SIDE */}
          <div className="ml-auto flex h-full items-center">
            {/* STATUS */}
            <div className="mr-5 hidden items-center gap-2 border-r border-zinc-800 pr-5 lg:flex">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-30" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
              </span>

              <span className="text-xs font-medium text-zinc-500">
                System online
              </span>
            </div>

            {/* USER */}
            <button
              type="button"
              className="group flex items-center gap-3 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-zinc-900"
              aria-label="Open user menu"
            >
              <div className="hidden text-right md:block">
                <p className="text-xs font-medium text-zinc-200">
                  Daniel Paniagua
                </p>

                <p className="mt-0.5 text-[10px] text-zinc-600">
                  Manufacturing Engineer
                </p>
              </div>

              <div className="grid h-8 w-8 place-items-center rounded-lg border border-zinc-800 bg-zinc-900 text-zinc-400 transition-colors group-hover:border-zinc-700 group-hover:text-zinc-200">
                <User size={15} strokeWidth={1.8} />
              </div>
            </button>
          </div>
        </div>
      </header>

    {/* CONTENT */}
<main className="relative min-h-[calc(100vh-4rem)] overflow-hidden bg-zinc-950">
  {/* Grid técnico animado */}
  <div
    aria-hidden="true"
   className="technical-grid pointer-events-none absolute inset-0 opacity-[0.14]"
    style={{
      backgroundImage: `
        linear-gradient(to right, #3f3f46 1px, transparent 1px),
        linear-gradient(to bottom, #3f3f46 1px, transparent 1px)
      `,
      backgroundSize: "32px 32px",
    }}
  />

  {/* Glow ambiental superior */}
  <div
    aria-hidden="true"
    className="ambient-glow pointer-events-none absolute -left-[10%] -top-40 h-[500px] w-[120%]"
    style={{
      background:
        "radial-gradient(ellipse at center, rgba(16,185,129,0.09), transparent 60%)",
      filter: "blur(35px)",
    }}
  />

  {/* Segundo glow estático */}
  <div
    aria-hidden="true"
    className="pointer-events-none absolute -right-64 top-[35%] h-[500px] w-[500px] rounded-full bg-emerald-500/[0.025] blur-3xl"
  />

  {/* Oscurecimiento para profundidad */}
  <div
    aria-hidden="true"
    className="pointer-events-none absolute inset-0"
    style={{
      background: `
        radial-gradient(
          ellipse at center,
          transparent 0%,
          transparent 45%,
          rgba(9, 9, 11, 0.35) 100%
        )
      `,
    }}
  />

  {/* Degradado vertical */}
  <div
    aria-hidden="true"
    className="pointer-events-none absolute inset-0"
    style={{
      background:
        "linear-gradient(to bottom, rgba(9,9,11,0.05), transparent 180px, rgba(9,9,11,0.20) 100%)",
    }}
  />

  {/* CONTENIDO */}
  <div className="relative z-10 mx-auto max-w-[1600px] p-4 md:p-6 lg:p-8 xl:p-10">
    <Outlet />
  </div>
</main>
    </div>
  );
}