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

try:  # el sondeo de indices genera avisos ruidosos de DirectShow
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:  # pragma: no cover
    pass


def _lock_settings() -> dict | None:
    """Parametros fijos configurados en el panel (None si no estan activos)."""
    from core.settings import settings_store

    camera = settings_store.get()["camera"]
    return camera if camera.get("lock_enabled") else None


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
        # Serializa todo acceso al hardware (sondeo, apertura, liberacion).
        # DirectShow/OpenCV corrompe el heap si dos hilos abren o liberan
        # dispositivos a la vez, y eso tumba el proceso sin traza.
        self._device_lock = threading.Lock()
        self._devices_cache: list[dict] | None = None
        # Captura que el hilo de lectura debe liberar al salir (ver stop()).
        self._orphan_capture: cv2.VideoCapture | None = None
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
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError(
                    "La cámara anterior todavía se está cerrando; intente de nuevo en unos segundos."
                )

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

            with self._device_lock:
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
        self._lock_realsense(profile)

    @staticmethod
    def _lock_realsense(profile: Any) -> None:
        lock = _lock_settings()
        if not lock:
            return
        try:
            sensor = profile.get_device().first_color_sensor()
            sensor.set_option(rs.option.enable_auto_exposure, 0)
            sensor.set_option(rs.option.exposure, max(1.0, 2.0 ** float(lock["exposure"]) * 1_000_000.0))
            sensor.set_option(rs.option.enable_auto_white_balance, 0)
            sensor.set_option(rs.option.white_balance, float(lock["wb_temperature"]))
        except Exception:
            pass  # no todos los modelos exponen estas opciones

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

        lock = _lock_settings()
        if lock:
            # DirectShow: 0.25 = exposicion manual, 0.75 = automatica.
            capture.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            capture.set(cv2.CAP_PROP_EXPOSURE, float(lock["exposure"]))
            capture.set(cv2.CAP_PROP_AUTO_WB, 0)
            capture.set(cv2.CAP_PROP_WB_TEMPERATURE, float(lock["wb_temperature"]))
            capture.set(cv2.CAP_PROP_AUTOFOCUS, 1 if lock["autofocus"] else 0)
            if not lock["autofocus"]:
                capture.set(cv2.CAP_PROP_FOCUS, float(lock["focus"]))
        else:
            capture.set(cv2.CAP_PROP_AUTOFOCUS, 1)

        ok, frame = capture.read()
        if not ok or frame is None:
            capture.release()
            raise RuntimeError("La Logitech BRIO se abrio pero no entrega imagen.")

        # La camara puede negociar otra resolucion; se informa la real.
        self._height, self._width = frame.shape[:2]
        self._capture = capture

    def list_devices(self, refresh: bool = False) -> dict:
        """Dispositivos disponibles (en cache; `refresh` vuelve a sondear).

        Nunca se sondea con la camara corriendo: abrir el dispositivo activo
        desde otro hilo es justo lo que corrompe DirectShow.
        """
        with self._state_lock:
            running = self._running
        if running:
            message = "Detenga la cámara para buscar otros dispositivos." if refresh else None
            return {"devices": list(self._devices_cache or []), "scanned": False, "message": message}
        if self._devices_cache is not None and not refresh:
            return {"devices": list(self._devices_cache), "scanned": False, "message": None}
        with self._device_lock:
            devices = self._probe_devices()
        self._devices_cache = devices
        return {"devices": list(devices), "scanned": True, "message": None}

    @staticmethod
    def _probe_devices(max_index: int = 8) -> list[dict]:
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

        misses = 0
        for index in range(max_index):
            capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            try:
                opened = capture.isOpened()
            finally:
                capture.release()
            if opened:
                misses = 0
                devices.append({
                    "provider": "brio",
                    "index": index,
                    "name": f"Cámara USB #{index}",
                    "serial": None,
                })
            else:
                # Los indices de DirectShow son consecutivos: dos huecos seguidos = fin.
                misses += 1
                if misses >= 2:
                    break
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
            thread.join(timeout=5.0)

        if capture is not None:
            if thread is not None and thread.is_alive():
                # El hilo sigue dentro de read(): liberar ahora corromperia el
                # heap. El propio hilo libera la captura al salir.
                with self._state_lock:
                    self._orphan_capture = capture
            else:
                with self._device_lock:
                    capture.release()

        with self._state_lock:
            self._capture = None
            self._pipeline = None
            self._config = None
            self._profile = None
            if thread is None or not thread.is_alive():
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
        with self._state_lock:
            own_capture = self._capture
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
            capture = None
            if own_capture is not None and self._orphan_capture is own_capture:
                capture, self._orphan_capture = own_capture, None
            self._frame_condition.notify_all()
        if capture is not None:
            with self._device_lock:
                capture.release()

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
