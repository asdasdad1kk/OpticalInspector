import {
  Camera,
  Database,
  FileUp,
  Maximize,
  Maximize2,
  Minimize,
  Minus,
  ScanLine,
  User,
  X,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

export default function Layout() {
   const link = ({ isActive }: { isActive: boolean }) => `flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition ${isActive ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/20" : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"}`;

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="sticky top-0 z-50 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-[1900px] items-center px-4 lg:px-8">
          {/* Logo */}
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-500 text-black shadow-lg shadow-emerald-500/20">
              <ScanLine size={20} />
            </div>

            <div>
              <h1 className="font-semibold text-white">
                Optical Inspector
              </h1>

              <p className="text-xs text-zinc-500">
                CAD Vision Station
              </p>
            </div>
          </div>

          {/* Navbar centrado */}
          <nav className="absolute left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900/70 p-1 backdrop-blur">
            <NavLink to="/upload" className={link}>
              <FileUp size={16} />
              Drawing
            </NavLink>

            <NavLink to="/inspect" className={link}>
              <Camera size={16} />
              Inspection
            </NavLink>

            <NavLink to="/database" className={link}>
              <Database size={16} />
              Database
            </NavLink>
          </nav>

          {/* Usuario */}
          <div className="ml-auto flex items-center gap-3">
            <div className="hidden text-right md:block">
              <p className="text-sm font-medium text-white">
                Daniel Paniagua
              </p>

              <p className="text-xs text-zinc-500">
                Manufacturing Engineer
              </p>
            </div>

            <button className="flex h-10 w-10 items-center justify-center rounded-full border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 transition hover:bg-emerald-500/20">
              <User size={18} />
            </button>
          </div>
          <div className="ml-6 flex items-center border-l border-zinc-800 pl-4">
  <button
    className="grid h-8 w-10 place-items-center text-zinc-400 transition hover:bg-zinc-800 hover:text-white"
    onClick={() => window.pywebview?.api?.minimize?.()}
  >
    <Minus size={16} />
  </button>

  <button
    className="grid h-8 w-10 place-items-center text-zinc-400 transition hover:bg-zinc-800 hover:text-white"
    onClick={() => window.pywebview?.api?.toggleMaximize?.()}
  >
    <Maximize2 size={14} />
  </button>

  <button
    className="grid h-8 w-10 place-items-center text-zinc-400 transition hover:bg-red-500 hover:text-white"
    onClick={() => window.pywebview?.api?.close?.()}
  >
    <X size={16} />
  </button>
</div>
        </div>
        {/* Ventana */}

      </header>

      <main className="mx-auto max-w-[1600px] p-4 lg:p-8">
        <Outlet />
      </main>
    </div>
  );
}