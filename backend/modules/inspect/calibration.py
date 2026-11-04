"""Calibracion de camara con tablero ChArUco.

- Distorsion del lente (matriz intrinseca + coeficientes), aplicada a cada
  imagen antes de inspeccionar. Importante en la BRIO (gran angular).
- Escala mm/px en el plano del tablero (para dar de alta piezas reales sin
  teclear una dimension), medida con el tablero sobre el plano de la pieza.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any

import cv2
import numpy as np

from core import config
from core.settings import settings_store

BOARD_SQUARES = (7, 5)
INNER_COLS = BOARD_SQUARES[0] - 1
MIN_FRAMES = 8
MIN_CORNERS = 12
CALIBRATION_FILE = "calibration.json"


def _board(square_mm: float):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_250)
    return cv2.aruco.CharucoBoard(BOARD_SQUARES, float(square_mm), float(square_mm) * 0.73, dictionary)


def board_png(square_mm: float, pixels_per_mm: float = 10.0) -> bytes:
    """Tablero para imprimir. A `pixels_per_mm` = 10 equivale a 254 DPI."""
    margin = int(round(5 * pixels_per_mm))
    width = int(round(BOARD_SQUARES[0] * square_mm * pixels_per_mm)) + 2 * margin
    height = int(round(BOARD_SQUARES[1] * square_mm * pixels_per_mm)) + 2 * margin
    image = _board(square_mm).generateImage((width, height), marginSize=margin, borderBits=1)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("No fue posible generar el tablero.")
    return encoded.tobytes()


def _detect(image: np.ndarray, square_mm: float):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    detector = cv2.aruco.CharucoDetector(_board(square_mm))
    corners, ids, _, _ = detector.detectBoard(gray)
    if corners is None or ids is None:
        return None, None
    return corners, ids


def _mm_per_px(corners: np.ndarray, ids: np.ndarray, square_mm: float) -> float | None:
    by_id = {int(i): c.reshape(2) for i, c in zip(ids.ravel(), corners)}
    distances = [
        float(np.linalg.norm(point - by_id[i + 1]))
        for i, point in by_id.items()
        if (i + 1) in by_id and (i + 1) % INNER_COLS != 0
    ]
    return square_mm / float(np.median(distances)) if distances else None


class CalibrationService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._frames: list[tuple[np.ndarray, np.ndarray]] = []
        self._size: tuple[int, int] | None = None
        self._cache_key: tuple | None = None
        self._maps: tuple[np.ndarray, np.ndarray] | None = None

    # ---------- captura de fotogramas ----------
    def add_frame(self, image: np.ndarray) -> dict[str, Any]:
        square_mm = float(settings_store.get()["calibration"]["square_mm"])
        corners, ids = _detect(image, square_mm)
        count = 0 if ids is None else len(ids)
        with self._lock:
            size = (image.shape[1], image.shape[0])
            if self._size is not None and self._size != size:
                self._frames.clear()  # cambio de resolucion: se reinicia la sesion
            self._size = size
            accepted = count >= MIN_CORNERS
            if accepted:
                self._frames.append((corners, ids))
            return {
                "accepted": accepted,
                "corners": count,
                "frames": len(self._frames),
                "required": MIN_FRAMES,
                "message": (
                    "Fotograma aceptado." if accepted
                    else f"Tablero no detectado completo ({count} esquinas; se requieren {MIN_CORNERS})."
                ),
            }

    def reset_session(self) -> None:
        with self._lock:
            self._frames.clear()

    def session_info(self) -> dict[str, Any]:
        with self._lock:
            return {"frames": len(self._frames), "required": MIN_FRAMES}

    # ---------- calculo ----------
    def compute(self) -> dict[str, Any]:
        with self._lock:
            frames = list(self._frames)
            size = self._size
        if size is None or len(frames) < MIN_FRAMES:
            raise ValueError(f"Se necesitan al menos {MIN_FRAMES} fotogramas validos (hay {len(frames)}).")

        square_mm = float(settings_store.get()["calibration"]["square_mm"])
        board = _board(square_mm)
        object_points, image_points = [], []
        for corners, ids in frames:
            obj, img = board.matchImagePoints(corners, ids)
            if obj is not None and len(obj) >= 6:
                object_points.append(obj.reshape(-1, 1, 3).astype(np.float32))
                image_points.append(img.reshape(-1, 1, 2).astype(np.float32))

        rms, matrix, dist, rvecs, tvecs = cv2.calibrateCamera(
            object_points, image_points, size, None, None
        )
        per_view = []
        for obj, img, rvec, tvec in zip(object_points, image_points, rvecs, tvecs):
            projected, _ = cv2.projectPoints(obj, rvec, tvec, matrix, dist)
            per_view.append(float(np.sqrt(np.mean(np.sum((projected - img) ** 2, axis=2)))))

        result = {
            "camera_matrix": matrix.tolist(),
            "dist_coeffs": dist.ravel().tolist(),
            "image_size": list(size),
            "rms": float(rms),
            "per_view_error": per_view,
            "frames_used": len(object_points),
            "square_mm": square_mm,
            "calibrated_at": datetime.now().isoformat(timespec="seconds"),
            "plane_mm_per_px": None,
            "quality": "BUENA" if rms < 0.6 else "ACEPTABLE" if rms < 1.2 else "POBRE",
        }
        previous = self.load()
        if previous and previous.get("image_size") == list(size):
            result["plane_mm_per_px"] = previous.get("plane_mm_per_px")
        self._write(result)
        return result

    # ---------- escala en el plano de la pieza ----------
    def measure_plane_scale(self, image: np.ndarray) -> dict[str, Any]:
        calibration = self.load()
        if not calibration:
            raise ValueError("Calibre primero el lente.")
        square_mm = float(settings_store.get()["calibration"]["square_mm"])
        undistorted = self.undistort(image, force=True)
        corners, ids = _detect(undistorted, square_mm)
        if ids is None or len(ids) < MIN_CORNERS:
            raise ValueError("No se detecta el tablero completo en el plano de la pieza.")
        scale = _mm_per_px(corners, ids, square_mm)
        if scale is None:
            raise ValueError("No fue posible medir la escala.")
        calibration["plane_mm_per_px"] = scale
        calibration["plane_image_size"] = [image.shape[1], image.shape[0]]
        self._write(calibration)
        return {"mm_per_px": scale, "px_per_mm": 1.0 / scale}

    # ---------- persistencia y aplicacion ----------
    def _write(self, data: dict[str, Any]) -> None:
        config.CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
        (config.CALIBRATION_DIR / CALIBRATION_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._cache_key = None

    def load(self) -> dict[str, Any] | None:
        try:
            return json.loads((config.CALIBRATION_DIR / CALIBRATION_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def delete(self) -> None:
        (config.CALIBRATION_DIR / CALIBRATION_FILE).unlink(missing_ok=True)
        self._cache_key = None

    def plane_scale_for(self, size: tuple[int, int]) -> float | None:
        data = self.load()
        if data and data.get("plane_mm_per_px") and data.get("plane_image_size") == list(size):
            return float(data["plane_mm_per_px"])
        return None

    def undistort(self, image: np.ndarray, force: bool = False) -> np.ndarray:
        """Corrige la distorsion si hay calibracion para esta resolucion."""
        if not force and not settings_store.get()["calibration"]["apply"]:
            return image
        data = self.load()
        size = (image.shape[1], image.shape[0])
        if not data or data.get("image_size") != list(size):
            return image
        key = (size, data.get("calibrated_at"))
        with self._lock:
            if self._cache_key != key:
                matrix = np.array(data["camera_matrix"], dtype=np.float64)
                dist = np.array(data["dist_coeffs"], dtype=np.float64)
                new_matrix, _ = cv2.getOptimalNewCameraMatrix(matrix, dist, size, 0.0)
                self._maps = cv2.initUndistortRectifyMap(matrix, dist, None, new_matrix, size, cv2.CV_16SC2)
                self._cache_key = key
            maps = self._maps
        return cv2.remap(image, maps[0], maps[1], cv2.INTER_LINEAR)


calibration_service = CalibrationService()
