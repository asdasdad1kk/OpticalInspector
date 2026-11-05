"""Servicio del escaner 3D: hilo de captura, estados, resultado, exportacion y guardado.

Es independiente del resto de la app: solo usa de la camara el metodo `grab_rgbd`, y Open3D se
carga al iniciar un escaneo (si falta, solo este panel avisa).
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import struct
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from core import config
from modules.camera.service import camera_service

from .engine import ScanEngine, ScannerUnavailable, ScanOptions, analyze_mesh, load_open3d

TARGET_FPS = 8.0
EXPORT_FORMATS = {
    "stl": "model/stl",
    "ply": "application/octet-stream",
    "obj": "text/plain",
}
_ID = re.compile(r"^[0-9a-f]{12}$")


class ScannerBusy(RuntimeError):
    pass


class ScannerService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._engine_lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._reset_state()

    def _reset_state(self) -> None:
        self.state = "idle"  # idle | scanning | paused | processing | done | error
        self.message = ""
        self.engine: ScanEngine | None = None
        self.options = ScanOptions()
        self.mesh: Any = None
        self.analysis: dict[str, Any] | None = None
        self.mesh_info: dict[str, Any] = {}
        self.frames = 0
        self.tracked_frames = 0
        self.fps = 0.0
        self.started_at = 0.0
        self.elapsed = 0.0
        self.errors = 0
        self.saved_id: str | None = None
        self._recent: list[float] = []

    # ------------------------------------------------------------------ estado
    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("open3d") is not None

    def status(self) -> dict[str, Any]:
        camera = camera_service.status
        with self._lock:
            engine = self.engine
            elapsed = self.elapsed + (time.monotonic() - self.started_at if self.state == "scanning" else 0.0)
            data: dict[str, Any] = {
                "available": self.available(),
                "install_hint": None if self.available() else "Instale la librería con: pip install open3d",
                "camera_running": bool(camera["running"]),
                "depth_available": bool(camera.get("depth_available")),
                "state": self.state,
                "message": self.message,
                "frames": self.frames,
                "tracked_frames": self.tracked_frames,
                "fps": round(self.fps, 1),
                "elapsed_s": round(elapsed, 1),
                "voxel_mm": self.options.voxel_mm,
                "has_mesh": self.mesh is not None,
                "analysis": self.analysis,
                "isolated": bool(self.mesh_info.get("isolated")),
                "isolate_message": self.mesh_info.get("isolate_message", ""),
                "can_reprocess": engine is not None and self.mesh is not None,
                "saved_id": self.saved_id,
            }
            if engine is not None:
                data.update({
                    "tracking": engine.tracking,
                    "keyframes": engine.keyframes,
                    "lost_total": engine.lost_total,
                    "lost_streak": engine.lost_streak,
                    "coverage_mm": engine.coverage_mm(),
                    "camera": engine.camera_mm(),
                    "tracking_message": engine.message,
                    "speed_mm_s": round(engine.speed_mm_per_frame * self.fps, 0),
                    "alignment_quality": round(engine.alignment_quality),
                })
            else:
                data.update({"tracking": "inicializando", "keyframes": 0, "lost_total": 0, "lost_streak": 0,
                             "coverage_mm": None, "camera": None, "tracking_message": "", "speed_mm_s": 0, "alignment_quality": 100})
        return data

    # ------------------------------------------------------------------ control
    def start(self, voxel_mm: float = 2.0, max_depth_mm: float = 1000.0) -> dict[str, Any]:
        load_open3d()  # falla aqui, con un mensaje claro, si falta la libreria
        camera = camera_service.status
        if not camera["running"]:
            raise RuntimeError("Inicie la cámara RealSense antes de escanear.")
        if not camera.get("depth_available"):
            raise RuntimeError("Esta cámara no entrega profundidad: el escáner 3D necesita una Intel RealSense por USB 3.")
        with self._lock:
            if self.state in ("scanning", "paused", "processing"):
                raise ScannerBusy("Ya hay un escaneo en curso. Termínelo o reinícielo primero.")
            self._reset_state()
            self.options = ScanOptions(
                voxel_mm=float(min(max(voxel_mm, 0.5), 10.0)),
                max_depth_mm=float(min(max(max_depth_mm, 300.0), 4000.0)),
            )
            self.state = "scanning"
            self.started_at = time.monotonic()
            self._stop.clear()
            self._paused.clear()
            self._thread = threading.Thread(target=self._capture_loop, name="scanner3d-capture", daemon=True)
            self._thread.start()
        return self.status()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "scanning":
                self.elapsed += time.monotonic() - self.started_at
                self.state = "paused"
                self._paused.set()
        return self.status()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "paused":
                self.started_at = time.monotonic()
                self.state = "scanning"
                self._paused.clear()
        return self.status()

    def reset(self) -> dict[str, Any]:
        self._stop.set()
        self._paused.clear()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        with self._lock:
            self._reset_state()
        return self.status()

    def finish(self, isolate: bool = False) -> dict[str, Any]:
        """Detiene la captura y genera la malla en segundo plano."""
        with self._lock:
            if self.state not in ("scanning", "paused"):
                raise RuntimeError("No hay un escaneo en curso.")
            if self.state == "scanning":
                self.elapsed += time.monotonic() - self.started_at
            self.state = "processing"
            self.message = "Generando la malla..."
        self._stop.set()
        self._paused.clear()
        threading.Thread(target=self._finalize, args=(isolate,), name="scanner3d-finalize", daemon=True).start()
        return self.status()

    def reprocess(self, isolate: bool) -> dict[str, Any]:
        """Regenera la malla del mismo escaneo, con o sin la mesa."""
        with self._lock:
            if self.engine is None or self.state not in ("done", "error"):
                raise RuntimeError("No hay un escaneo que reprocesar.")
            self.state = "processing"
            self.message = "Generando la malla..."
        threading.Thread(target=self._finalize, args=(isolate,), name="scanner3d-finalize", daemon=True).start()
        return self.status()

    # ------------------------------------------------------------------ hilos
    def _capture_loop(self) -> None:
        interval = 1.0 / TARGET_FPS
        failures = 0
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.05)
                continue
            began = time.monotonic()
            try:
                frame = camera_service.grab_rgbd(timeout=1.5)
            except RuntimeError as error:
                failures += 1
                with self._lock:
                    self.message = str(error)
                if failures >= 20 or not camera_service.status["running"]:
                    with self._lock:
                        self.state = "error"
                        self.message = "Se perdió la señal de la cámara. " + str(error)
                    return
                time.sleep(0.2)
                continue
            failures = 0
            try:
                with self._engine_lock:
                    if self.engine is None:
                        self.engine = ScanEngine(self.options, frame["intrinsics"])
                    result = self.engine.process(frame["color"], frame["depth_m"])
            except ScannerUnavailable as error:
                with self._lock:
                    self.state, self.message = "error", str(error)
                return
            except Exception as error:  # un cuadro malo no debe tumbar el escaneo
                self.errors += 1
                with self._lock:
                    self.message = f"Cuadro descartado: {error}"
                continue
            with self._lock:
                self.frames += 1
                self.tracked_frames += int(result["tracked"])
                now = time.monotonic()
                self._recent = [t for t in self._recent if now - t < 3.0] + [now]
                self.fps = len(self._recent) / 3.0 if now - self.started_at > 3 else float(len(self._recent)) / max(now - self.started_at, 0.5)
            delay = interval - (time.monotonic() - began)
            if delay > 0:
                time.sleep(delay)

    def _finalize(self, isolate: bool) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10.0)
        try:
            with self._engine_lock:
                if self.engine is None:
                    raise RuntimeError("No se recibió ningún cuadro con profundidad.")
                mesh, info = self.engine.build_mesh(isolate=isolate)
            analysis = analyze_mesh(mesh)
            with self._lock:
                self.mesh, self.analysis, self.mesh_info = mesh, analysis, info
                self.saved_id = None
                self.state, self.message = "done", info.get("isolate_message", "")
        except Exception as error:
            with self._lock:
                self.state, self.message = "error", str(error)

    # ------------------------------------------------------------------ datos para el visor
    def preview_bytes(self, max_points: int = 60000) -> bytes:
        """[uint32 N][float32 N*3 (mm)][uint8 N*3]"""
        engine = self.engine
        if engine is None:
            return struct.pack("<I", 0)
        with self._engine_lock:
            points, colors = engine.preview_arrays(max_points)
        return struct.pack("<I", len(points)) + points.tobytes() + colors.tobytes()

    def mesh_bytes(self, max_triangles: int = 300000) -> bytes:
        """[uint32 V][uint32 F][float32 V*3 (mm)][uint8 V*3][uint32 F*3]"""
        with self._lock:
            mesh = self.mesh
        if mesh is None:
            raise RuntimeError("Todavía no hay una malla generada.")
        if len(mesh.triangles) > max_triangles:
            mesh = mesh.simplify_quadric_decimation(target_number_of_triangles=max_triangles)
        vertices = np.asarray(mesh.vertices).astype(np.float32)
        triangles = np.asarray(mesh.triangles).astype(np.uint32)
        colors = (np.asarray(mesh.vertex_colors) * 255).clip(0, 255).astype(np.uint8) if mesh.has_vertex_colors() else \
            np.full((len(vertices), 3), 190, np.uint8)
        return struct.pack("<II", len(vertices), len(triangles)) + vertices.tobytes() + colors.tobytes() + triangles.tobytes()

    # ------------------------------------------------------------------ exportar
    def export(self, fmt: str) -> tuple[bytes, str, str]:
        if fmt not in EXPORT_FORMATS:
            raise ValueError(f"Formato no soportado: {fmt}. Use {', '.join(EXPORT_FORMATS)}.")
        with self._lock:
            mesh = self.mesh
        if mesh is None:
            raise RuntimeError("Todavía no hay una malla generada.")
        o3d = load_open3d()
        mesh.compute_vertex_normals()
        mesh.compute_triangle_normals()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"escaneo.{fmt}"
            ok = o3d.io.write_triangle_mesh(
                str(path), mesh, write_ascii=False, write_vertex_normals=True, write_vertex_colors=fmt != "stl",
            )
            if not ok or not path.exists():
                raise RuntimeError(f"No fue posible escribir el archivo {fmt.upper()}.")
            data = path.read_bytes()
        name = f"escaneo_3d_{datetime.now():%Y%m%d_%H%M%S}.{fmt}"
        return data, EXPORT_FORMATS[fmt], name

    # ------------------------------------------------------------------ guardados
    @staticmethod
    def _folder(scan_id: str) -> Path:
        if not _ID.match(scan_id or ""):
            raise LookupError("Escaneo inválido.")
        folder = config.SCANS3D_DIR / scan_id
        if not folder.is_dir():
            raise LookupError("El escaneo guardado no existe.")
        return folder

    def save(self, name: str) -> dict[str, Any]:
        with self._lock:
            mesh, analysis, info, options = self.mesh, self.analysis, dict(self.mesh_info), self.options
            frames, elapsed = self.frames, self.elapsed
        if mesh is None:
            raise RuntimeError("Todavía no hay una malla que guardar.")
        o3d = load_open3d()
        scan_id = uuid.uuid4().hex[:12]
        folder = config.SCANS3D_DIR / scan_id
        folder.mkdir(parents=True, exist_ok=True)
        o3d.io.write_triangle_mesh(str(folder / "mesh.ply"), mesh, write_ascii=False, write_vertex_colors=True)
        meta = {
            "id": scan_id, "name": (name or "").strip() or f"Escaneo {datetime.now():%d/%m/%Y %H:%M}",
            "created_at": datetime.now().isoformat(timespec="seconds"), "analysis": analysis, "isolated": info.get("isolated", False),
            "voxel_mm": options.voxel_mm, "frames": frames, "duration_s": round(elapsed, 1),
        }
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        with self._lock:
            self.saved_id = scan_id
        return meta

    def list_saved(self) -> list[dict[str, Any]]:
        items = []
        if config.SCANS3D_DIR.is_dir():
            for folder in config.SCANS3D_DIR.iterdir():
                try:
                    items.append(json.loads((folder / "meta.json").read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)

    def load_saved(self, scan_id: str) -> dict[str, Any]:
        folder = self._folder(scan_id)
        with self._lock:
            if self.state in ("scanning", "paused", "processing"):
                raise ScannerBusy("Termine o reinicie el escaneo actual antes de abrir otro.")
        o3d = load_open3d()
        mesh = o3d.io.read_triangle_mesh(str(folder / "mesh.ply"))
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        mesh.compute_vertex_normals()
        with self._lock:
            self._reset_state()
            self.mesh, self.analysis = mesh, meta.get("analysis")
            self.mesh_info = {"isolated": meta.get("isolated", False), "isolate_message": ""}
            self.options = ScanOptions(voxel_mm=meta.get("voxel_mm", 2.0))
            self.frames, self.elapsed, self.saved_id = meta.get("frames", 0), meta.get("duration_s", 0.0), scan_id
            self.state = "done"
        return self.status()

    def delete_saved(self, scan_id: str) -> None:
        shutil.rmtree(self._folder(scan_id), ignore_errors=True)
        with self._lock:
            if self.saved_id == scan_id:
                self.saved_id = None


scanner_service = ScannerService()
