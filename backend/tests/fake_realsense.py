"""Simulador minimo de pyrealsense2 para probar el servicio de camara sin hardware.

Imita lo que usa modules/camera/service.py: pipeline/config/align, el sensor de profundidad
(escala, opciones, preset) y los intrinsecos del color. Entrega la foto de una pieza como color
y una profundidad sintetica con verdad conocida (tests/synthetic_scene.py), con ruido distinto
en cada cuadro, como el estereo real.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from synthetic_scene import make_depth  # noqa: E402


class _Enum:
    pass


class stream(_Enum):  # noqa: N801
    color = "color"
    depth = "depth"


class format(_Enum):  # noqa: N801,A001
    bgr8 = "bgr8"
    z16 = "z16"


class option(_Enum):  # noqa: N801
    visual_preset = "visual_preset"
    depth_units = "depth_units"
    emitter_enabled = "emitter_enabled"
    laser_power = "laser_power"
    enable_auto_exposure = "enable_auto_exposure"
    exposure = "exposure"
    enable_auto_white_balance = "enable_auto_white_balance"
    white_balance = "white_balance"


class rs400_visual_preset(_Enum):  # noqa: N801
    high_accuracy = 3


class camera_info(_Enum):  # noqa: N801
    name = "name"
    serial_number = "serial_number"


class _Intrinsics:
    def __init__(self, fx, fy, ppx, ppy, width, height):
        self.fx, self.fy, self.ppx, self.ppy, self.width, self.height = fx, fy, ppx, ppy, width, height


class _VideoProfile:
    def __init__(self, intrinsics):
        self._intrinsics = intrinsics

    def as_video_stream_profile(self):
        return self

    def get_intrinsics(self):
        return self._intrinsics


class _Range:
    def __init__(self, maximum):
        self.max = maximum


class _DepthSensor:
    def __init__(self, world):
        self.world = world
        self.options = {}

    def supports(self, code):
        return code in ("visual_preset", "depth_units", "emitter_enabled", "laser_power")

    def set_option(self, code, value):
        self.options[code] = value
        if code == "depth_units":
            self.world.depth_units = float(value)

    def get_option_range(self, code):
        return _Range(360.0)

    def get_depth_scale(self):
        return self.world.depth_units


class _Device:
    def __init__(self, world):
        self.world = world

    def first_depth_sensor(self):
        return self.world.depth_sensor

    def first_color_sensor(self):
        return _DepthSensor(self.world)

    def get_info(self, key):
        return "Intel RealSense D435 (simulada)" if key == "name" else "SIM000001"


class _Profile:
    def __init__(self, world):
        self.world = world

    def get_device(self):
        return _Device(self.world)

    def get_stream(self, kind):
        return _VideoProfile(_Intrinsics(**self.world.intrinsics))


class _Frame:
    def __init__(self, data):
        self._data = data

    def __bool__(self):
        return self._data is not None

    def get_data(self):
        return self._data


class _Frameset:
    def __init__(self, color, depth):
        self._color, self._depth = color, depth

    def get_color_frame(self):
        return _Frame(self._color)

    def get_depth_frame(self):
        return _Frame(self._depth)


class _World:
    """Estado compartido: la escena (color + profundidad) y los parametros del sensor."""

    def __init__(self):
        self.depth_supported = True
        self.depth_units = 0.001
        self.depth_sensor = _DepthSensor(self)
        self.scene = None
        self.intrinsics = {}
        self.truth = {}
        self.noise_mm = 0.25
        self.calls: list[str] = []

    def build(self, width: int, height: int):
        if self.scene is not None and self.scene["color"].shape[:2] == (height, width):
            return
        from modules.inspect.golden import analyze_reference
        from modules.reference.relief import contour_to_px, features_to_px

        source = cv2.imread(str(BACKEND / "tests" / "barrenos.png"))[130:812, 18:1232]
        color = cv2.resize(source, (width, height), interpolation=cv2.INTER_CUBIC)
        analysis = analyze_reference(color)
        depth, intrinsics, truth = make_depth(
            (height, width),
            contour_to_px(analysis["contour"], analysis["scale"]),
            features_to_px(analysis["features"], analysis["scale"]),
            noise_mm=0.0, dropout=0.0,
        )
        self.scene = {"color": color, "depth_clean": depth}
        self.intrinsics, self.truth = intrinsics, truth

    def depth_frame(self, rng) -> np.ndarray:
        clean = self.scene["depth_clean"]
        noisy = clean + rng.normal(0.0, self.noise_mm, clean.shape).astype(np.float32)
        noisy[rng.random(clean.shape) < 0.03] = np.nan
        raw = np.where(np.isfinite(noisy), noisy / (self.depth_units * 1000.0), 0)
        return np.clip(raw, 0, 65535).astype(np.uint16)


WORLD = _World()


class config:  # noqa: N801
    def __init__(self):
        self.streams: list[tuple] = []

    def enable_stream(self, kind, width, height, fmt, fps):
        self.streams.append((kind, width, height, fmt, fps))


class pipeline:  # noqa: N801
    def __init__(self):
        self.running = False
        self.rng = np.random.default_rng(3)
        self.cfg = None

    def start(self, cfg):
        WORLD.calls.append("start")
        kinds = {item[0] for item in cfg.streams}
        if stream.depth in kinds and not WORLD.depth_supported:
            raise RuntimeError("Couldn't resolve requests (depth no soportada)")
        color = next(item for item in cfg.streams if item[0] == stream.color)
        WORLD.build(color[1], color[2])
        self.cfg, self.running = cfg, True
        return _Profile(WORLD)

    def stop(self):
        self.running = False

    def wait_for_frames(self, timeout_ms=5000):
        if not self.running:
            raise RuntimeError("pipeline detenido")
        time.sleep(1 / 60)
        with_depth = any(item[0] == stream.depth for item in self.cfg.streams)
        depth = WORLD.depth_frame(self.rng) if with_depth else None
        return _Frameset(WORLD.scene["color"].copy(), depth)


class align:  # noqa: N801
    def __init__(self, target):
        self.target = target

    def process(self, frames):
        WORLD.calls.append("align")
        return frames


class _Context:
    def query_devices(self):
        return [_Device(WORLD)]


def context():
    return _Context()
