from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

try:  # RealSense es opcional: la BRIO funciona sin pyrealsense2.
    import pyrealsense2 as rs
except ImportError:  # pragma: no cover
    rs = None

PROVIDERS = ("realsense", "brio")


@dataclass(frozen=True)
class CameraFrame:
    sequence: int
    jpeg: bytes


class CameraService:
    """Control centralizado de la camara (Intel RealSense o Logitech BRIO).

    Un único hilo captura imágenes. El WebSocket consume una versión JPEG
    ligera, mientras que la inspección obtiene una copia del frame BGR
    original y la codifica en PNG sin reutilizar el JPEG del preview.
    """

    def __init__(self) -> None:
        self._provider = "realsense"
        self._device_index = 0
        self._capture: cv2.VideoCapture | None = None
        self._pipeline: Any = None
        self._config: Any = None
        self._profile: Any = None
        self._thread: threading.Thread | None = None

        self._state_lock = threading.RLock()
        self._frame_condition = threading.Condition(self._state_lock)
        self._stop_event = threading.Event()

        self._running = False
        self._sequence = 0
        self._latest_frame: np.ndarray | None = None
        self._latest_preview: bytes | None = None
        self._last_error: str | None = None

        self._width = 1280
        self._height = 720
        self._fps = 30
        self._preview_quality = 75

    def start(
        self,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        preview_quality: int = 75,
        provider: str | None = None,
        device_index: int | None = None,
    ) -> dict:
        if width <= 0 or height <= 0 or fps <= 0:
            raise ValueError("width, height y fps deben ser mayores que cero.")

        if provider is not None:
            provider = provider.lower()
            if provider not in PROVIDERS:
                raise ValueError(f"Proveedor desconocido: {provider}. Use {PROVIDERS}.")

        preview_quality = max(30, min(int(preview_quality), 95))

        with self._state_lock:
            if self._running:
                return self.status

            if provider is not None:
                self._provider = provider
            if device_index is not None:
                self._device_index = max(0, int(device_index))
            self._width = int(width)
            self._height = int(height)
            self._fps = int(fps)
            self._preview_quality = preview_quality
            self._last_error = None
            self._latest_frame = None
            self._latest_preview = None
            self._sequence = 0
            self._stop_event.clear()

            if self._provider == "brio":
                self._open_brio()
            else:
                self._open_realsense()

            self._running = True

            self._thread = threading.Thread(
                target=self._capture_loop,
                name=f"{self._provider}-capture",
                daemon=True,
            )
            self._thread.start()

            return self.status

    def _open_realsense(self) -> None:
        if rs is None:
            raise RuntimeError("pyrealsense2 no esta instalado en este equipo.")

        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(
            rs.stream.color,
            self._width,
            self._height,
            rs.format.bgr8,
            self._fps,
        )

        try:
            profile = pipeline.start(config)
        except Exception as error:
            try:
                pipeline.stop()
            except Exception:
                pass
            raise RuntimeError(
                f"No fue posible iniciar la Intel RealSense: {error}"
            ) from error

        self._pipeline = pipeline
        self._config = config
        self._profile = profile

    def _open_brio(self) -> None:
        # DirectShow + MJPG: la BRIO solo entrega 1080p/4K a 30 fps en MJPG.
        capture = cv2.VideoCapture(self._device_index, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(
                f"No fue posible abrir la Logitech BRIO (indice {self._device_index})."
            )

        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        capture.set(cv2.CAP_PROP_FPS, self._fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        capture.set(cv2.CAP_PROP_AUTOFOCUS, 1)

        ok, frame = capture.read()
        if not ok or frame is None:
            capture.release()
            raise RuntimeError("La Logitech BRIO se abrio pero no entrega imagen.")

        # La camara puede negociar otra resolucion; se informa la real.
        self._height, self._width = frame.shape[:2]
        self._capture = capture

    @staticmethod
    def list_devices(max_index: int = 6) -> list[dict]:
        """Dispositivos disponibles. Los indices OpenCV se prueban rapidamente."""
        devices: list[dict] = []

        if rs is not None:
            try:
                for device in rs.context().query_devices():
                    devices.append({
                        "provider": "realsense",
                        "index": len(devices),
                        "name": device.get_info(rs.camera_info.name),
                        "serial": device.get_info(rs.camera_info.serial_number),
                    })
            except Exception:
                pass

        for index in range(max_index):
            capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            try:
                if capture.isOpened():
                    devices.append({
                        "provider": "brio",
                        "index": index,
                        "name": f"Camara USB (UVC) #{index}",
                        "serial": None,
                    })
            finally:
                capture.release()
        return devices

    def stop(self) -> dict:
        with self._state_lock:
            if not self._running and self._pipeline is None and self._capture is None:
                return self.status

            self._running = False
            self._stop_event.set()
            self._frame_condition.notify_all()
            thread = self._thread
            pipeline = self._pipeline
            capture = self._capture

        # El pipeline se detiene fuera del lock para desbloquear wait_for_frames.
        if pipeline is not None:
            try:
                pipeline.stop()
            except Exception:
                pass

        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

        if capture is not None:
            capture.release()

        with self._state_lock:
            self._capture = None
            self._pipeline = None
            self._config = None
            self._profile = None
            self._thread = None
            self._latest_frame = None
            self._latest_preview = None
            self._frame_condition.notify_all()
            return self.status

    def restart(
        self,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        preview_quality: int = 75,
        provider: str | None = None,
        device_index: int | None = None,
    ) -> dict:
        self.stop()
        return self.start(width, height, fps, preview_quality, provider, device_index)

    def _capture_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._state_lock:
                pipeline = self._pipeline
                capture = self._capture

            if pipeline is None and capture is None:
                break

            try:
                if capture is not None:
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        raise RuntimeError("La BRIO no entrego un frame.")
                else:
                    frames = pipeline.wait_for_frames(timeout_ms=1000)
                    color_frame = frames.get_color_frame()

                    if not color_frame:
                        continue

                    frame = np.asanyarray(color_frame.get_data())

                if frame is None or frame.size == 0:
                    continue

                success, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, self._preview_quality],
                )
                if not success:
                    continue

                with self._frame_condition:
                    self._latest_frame = frame.copy()
                    self._latest_preview = encoded.tobytes()
                    self._sequence += 1
                    self._last_error = None
                    self._frame_condition.notify_all()

            except RuntimeError as error:
                if self._stop_event.is_set():
                    break

                with self._frame_condition:
                    self._last_error = str(error)
                    self._frame_condition.notify_all()
                time.sleep(0.05)

            except Exception as error:
                if self._stop_event.is_set():
                    break

                with self._frame_condition:
                    self._last_error = str(error)
                    self._frame_condition.notify_all()
                time.sleep(0.10)

        with self._frame_condition:
            self._running = False
            self._frame_condition.notify_all()

    def wait_for_preview(
        self,
        after_sequence: int = -1,
        timeout: float = 1.0,
    ) -> CameraFrame | None:
        """Espera un frame posterior a `after_sequence` sin hacer busy-wait."""

        deadline = time.monotonic() + max(timeout, 0.0)

        with self._frame_condition:
            while True:
                if (
                    self._latest_preview is not None
                    and self._sequence > after_sequence
                ):
                    return CameraFrame(
                        sequence=self._sequence,
                        jpeg=bytes(self._latest_preview),
                    )

                if not self._running:
                    return None

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None

                self._frame_condition.wait(timeout=remaining)

    def get_preview(self) -> CameraFrame | None:
        with self._state_lock:
            if self._latest_preview is None:
                return None

            return CameraFrame(
                sequence=self._sequence,
                jpeg=bytes(self._latest_preview),
            )

    def capture_bgr(self) -> np.ndarray:
        with self._state_lock:
            if self._latest_frame is None:
                raise RuntimeError("La cámara todavía no tiene un frame disponible.")
            return self._latest_frame.copy()

    def capture_png(self) -> bytes:
        frame = self.capture_bgr()
        success, encoded = cv2.imencode(".png", frame)

        if not success:
            raise RuntimeError("No fue posible codificar el frame como PNG.")

        return encoded.tobytes()

    def capture_jpeg(self, quality: int = 95) -> bytes:
        frame = self.capture_bgr()
        quality = max(30, min(int(quality), 100))
        success, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, quality],
        )

        if not success:
            raise RuntimeError("No fue posible codificar el frame como JPEG.")

        return encoded.tobytes()

    @property
    def status(self) -> dict:
        with self._state_lock:
            device_name: str | None = None
            serial_number: str | None = None

            if self._capture is not None:
                device_name = "Logitech BRIO"
            elif self._profile is not None:
                try:
                    device = self._profile.get_device()
                    device_name = device.get_info(rs.camera_info.name)
                    serial_number = device.get_info(rs.camera_info.serial_number)
                except Exception:
                    pass

            return {
                "provider": self._provider,
                "device_index": self._device_index,
                "running": self._running,
                "frame_available": self._latest_frame is not None,
                "sequence": self._sequence,
                "width": self._width,
                "height": self._height,
                "fps": self._fps,
                "preview_quality": self._preview_quality,
                "device_name": device_name,
                "serial_number": serial_number,
                "last_error": self._last_error,
            }


camera_service = CameraService()
