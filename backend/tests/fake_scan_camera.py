"""RealSense simulada que SE MUEVE sobre una placa grande (pruebas del escaner 3D).

Reutiliza la API simulada de fake_realsense, pero los cuadros salen de la escena sintetica
(tests/synthetic_scan.py): cada vez que se pide un cuadro alineado (color + profundidad) la camara
avanza un paso por una trayectoria de mano. Asi el ritmo no depende de la velocidad del equipo.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import fake_realsense as base  # noqa: E402
import numpy as np  # noqa: E402
import synthetic_scan as S  # noqa: E402

# Misma API que pyrealsense2 (lo que usa el servicio de camara).
stream, format, option = base.stream, base.format, base.option  # noqa: A001
rs400_visual_preset, camera_info = base.rs400_visual_preset, base.camera_info
config, context, WORLD = base.config, base.context, base.WORLD


class Scene:
    def __init__(self, step: float = 0.012):
        self.scene, self.mesh, self.truth = S.build_scene()
        self.trajectory = S.trajectory(step=step)
        self.index = 0
        self.poses_used: list[np.ndarray] = []
        self.preview, _ = S.render(self.scene, self.mesh, self.trajectory[0], seed=999)

    def next(self, units: float):
        pose = self.trajectory[min(self.index, len(self.trajectory) - 1)]
        color, depth = S.render(self.scene, self.mesh, pose, seed=self.index)
        self.poses_used.append(pose)
        self.index += 1
        raw = np.clip(np.where(depth > 0, depth / units, 0), 0, 65535).astype(np.uint16)
        return color, raw


SCENE: Scene | None = None


def reset(step: float = 0.012) -> Scene:
    global SCENE
    SCENE = Scene(step)
    return SCENE


class pipeline(base.pipeline):  # noqa: N801
    def start(self, cfg):
        if SCENE is None:
            reset()
        WORLD.calls.append("start")
        kinds = {item[0] for item in cfg.streams}
        if stream.depth in kinds and not WORLD.depth_supported:
            raise RuntimeError("Couldn't resolve requests (depth no soportada)")
        WORLD.intrinsics = {"fx": S.FX, "fy": S.FY, "ppx": S.CX, "ppy": S.CY, "width": S.WIDTH, "height": S.HEIGHT}
        self.cfg, self.running = cfg, True
        return base._Profile(WORLD)

    def wait_for_frames(self, timeout_ms=5000):
        if not self.running:
            raise RuntimeError("pipeline detenido")
        time.sleep(1 / 30)
        return base._Frameset(SCENE.preview.copy(), None)  # vista previa: cuadro fijo, barato


class align:  # noqa: N801
    def __init__(self, target):
        self.target = target

    def process(self, frames):
        WORLD.calls.append("align")
        color, raw = SCENE.next(WORLD.depth_units)
        return base._Frameset(color, raw)
