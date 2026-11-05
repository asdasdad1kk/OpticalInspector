from __future__ import annotations

import threading
import time
import warnings
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

try:  # RealSense es opcional: la BRIO funciona sin pyrealsense2.
    import pyrealsense2 as rs
except ImportError:  # pragma: no cover
    rs = None

PROVIDERS = ("realsense", "brio")

# Resoluciones de profundidad a intentar (la profundidad se alinea al color, no necesita coincidir).
DEPTH_MODES = ((848, 480), (640, 480), (1280, 720))
# Una llamada nativa a DirectShow/RealSense puede quedarse colgada (camara ocupada por otra
# aplicacion). Nada que corra en el hilo de una peticion debe esperar mas que esto por ella.
DEVICE_LOCK_TIMEOUT = 12.0
PROBE_TIMEOUT = 8.0
STATUS_LOCK_TIMEOUT = 1.0
# Unidad de profundidad de 0.1 mm: mas resolucion a distancia corta (alcance maximo ~6.5 m).
DEPTH_UNITS_M = 0.0001

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

        self._starting = False
        self._probe_thread: threading.Thread | None = None
        self._last_status: dict[str, Any] = {}

        # Profundidad (solo RealSense): se alinea al color unicamente mientras dura un escaneo.
        self._align: Any = None
        self._depth_available = False
        self._depth_scale = 0.0  # metros por unidad de profundidad
        self._color_intrinsics: dict[str, float] | None = None
        self._scan_frames: list[np.ndarray] | None = None
        self._scan_target = 0
        # Cuadro RGB-D suelto (escaner 3D en movimiento): color y profundidad del MISMO frameset.
        self._rgbd_wanted = False
        self._rgbd_slot: tuple[np.ndarray, np.ndarray] | None = None
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
            if self._starting:
                raise RuntimeError("La cámara ya se está iniciando; espere unos segundos.")
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError(
                    "La cámara anterior todavía se está cerrando; intente de nuevo en unos segundos."
                )
            self._starting = True

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

        # Abrir el dispositivo puede tardar o colgarse (camara ocupada): se hace fuera del
        # candado de estado para que /status y el preview sigan respondiendo, y con espera
        # acotada por el candado de dispositivo (p. ej. si una busqueda esta trabada).
        try:
            if not self._device_lock.acquire(timeout=DEVICE_LOCK_TIMEOUT):
                raise RuntimeError(
                    "Se está buscando u abriendo una cámara y no responde (otra aplicación puede estar "
                    "usándola). Ciérrela e intente de nuevo."
                )
            try:
                if self._provider == "brio":
                    self._open_brio()
                else:
                    self._open_realsense()
            finally:
                self._device_lock.release()

            with self._state_lock:
                self._running = True
                self._thread = threading.Thread(
                    target=self._capture_loop,
                    name=f"{self._provider}-capture",
                    daemon=True,
                )
                self._thread.start()
                return self.status
        finally:
            with self._state_lock:
                self._starting = False

    def _open_realsense(self) -> None:
        if rs is None:
            raise RuntimeError("pyrealsense2 no esta instalado en este equipo.")

        # Se intenta color + profundidad; si el equipo no lo soporta (USB 2, firmware) se
        # cae a solo color, que es lo que necesita la inspeccion.
        profile = pipeline = config = None
        depth_mode: tuple[int, int] | None = None
        last_error: Exception | None = None
        for mode in (*DEPTH_MODES, None):
            pipeline, config = rs.pipeline(), rs.config()
            config.enable_stream(rs.stream.color, self._width, self._height, rs.format.bgr8, self._fps)
            if mode is not None:
                config.enable_stream(rs.stream.depth, mode[0], mode[1], rs.format.z16, self._fps)
            try:
                profile = pipeline.start(config)
                depth_mode = mode
                break
            except Exception as error:
                last_error = error
                try:
                    pipeline.stop()
                except Exception:
                    pass
        if profile is None:
            raise RuntimeError(f"No fue posible iniciar la Intel RealSense: {last_error}") from last_error

        self._pipeline = pipeline
        self._config = config
        self._profile = profile
        self._lock_realsense(profile)
        self._depth_available = False
        if depth_mode is not None:
            self._setup_depth(profile)

    def _setup_depth(self, profile: Any) -> None:
        """Ajusta el sensor para medir con precision y lee escala e intrinsecos."""
        try:
            sensor = profile.get_device().first_depth_sensor()

            def option(name: str, value: float | None = None, use_max: bool = False) -> None:
                try:
                    code = getattr(rs.option, name)
                    if not sensor.supports(code):
                        return
                    if use_max:
                        value = sensor.get_option_range(code).max
                    sensor.set_option(code, float(value))
                except Exception:
                    pass  # no todos los modelos exponen todas las opciones

            # El preset primero: puede reiniciar otras opciones.
            try:
                option("visual_preset", float(rs.rs400_visual_preset.high_accuracy))
            except Exception:
                pass
            option("depth_units", DEPTH_UNITS_M)
            option("emitter_enabled", 1)
            option("laser_power", use_max=True)

            self._depth_scale = float(sensor.get_depth_scale())
            video = profile.get_stream(rs.stream.color).as_video_stream_profile()
            intrinsics = video.get_intrinsics()
            self._color_intrinsics = {
                "fx": float(intrinsics.fx), "fy": float(intrinsics.fy),
                "ppx": float(intrinsics.ppx), "ppy": float(intrinsics.ppy),
                "width": int(intrinsics.width), "height": int(intrinsics.height),
            }
            self._align = rs.align(rs.stream.color)
            self._depth_available = self._depth_scale > 0
        except Exception:
            self._depth_available = False
            self._align = None

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
        busy = (
            "La búsqueda de cámaras no responde: otra aplicación puede estar usando la cámara. "
            "Ciérrela e intente de nuevo."
        )
        previous = self._probe_thread
        if previous is not None and previous.is_alive():
            return {"devices": list(self._devices_cache or []), "scanned": False, "message": busy}

        outcome: dict[str, list[dict]] = {}

        def probe() -> None:
            with self._device_lock:
                outcome["devices"] = self._probe_devices()
                self._devices_cache = outcome["devices"]

        # Un hilo propio con tiempo limite: si DirectShow se cuelga, la peticion no se cuelga con el.
        thread = threading.Thread(target=probe, name="camera-probe", daemon=True)
        self._probe_thread = thread
        thread.start()
        thread.join(timeout=PROBE_TIMEOUT)
        if "devices" not in outcome:
            return {"devices": list(self._devices_cache or []), "scanned": False, "message": busy}
        return {"devices": list(outcome["devices"]), "scanned": True, "message": None}

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
            self._align = None
            self._depth_available = False
            self._scan_frames = None
            self._rgbd_wanted = False
            self._rgbd_slot = None
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

            depth_image = None
            try:
                if capture is not None:
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        raise RuntimeError("La BRIO no entrego un frame.")
                else:
                    frames = pipeline.wait_for_frames(timeout_ms=1000)
                    depth_image = None
                    align = self._align
                    if (self._scan_frames is not None or self._rgbd_wanted) and align is not None:
                        # Alinear cuesta CPU: solo se hace mientras hay un escaneo en curso.
                        frames = align.process(frames)
                        depth_frame = frames.get_depth_frame()
                        if depth_frame:
                            depth_image = np.asanyarray(depth_frame.get_data()).copy()
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
                    scan = self._scan_frames
                    if capture is None and depth_image is not None and scan is not None and len(scan) < self._scan_target:
                        scan.append(depth_image)
                    if capture is None and depth_image is not None and self._rgbd_wanted:
                        self._rgbd_slot = (self._latest_frame, depth_image)
                        self._rgbd_wanted = False
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

    def capture_depth_scan(self, frames: int = 16, timeout: float = 8.0) -> dict[str, Any]:
        """Escaneo 3D: color + mediana temporal de `frames` cuadros de profundidad alineados.

        La mediana por pixel elimina el ruido del estereo y los destellos del emisor;
        los pixeles sin medicion quedan en NaN. La profundidad queda en mm y en las
        mismas coordenadas que la imagen a color.
        """
        frames = max(3, min(int(frames), 60))
        with self._frame_condition:
            if not self._running:
                raise RuntimeError("La cámara no está iniciada.")
            if not self._depth_available:
                raise RuntimeError(
                    "Esta cámara no entrega profundidad. Use una Intel RealSense conectada por USB 3 "
                    "(con USB 2 solo hay color)."
                )
            if self._scan_frames is not None:
                raise RuntimeError("Ya hay un escaneo en curso.")
            self._scan_frames = []
            self._scan_target = frames
            deadline = time.monotonic() + timeout
            try:
                while len(self._scan_frames) < frames and self._running:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._frame_condition.wait(timeout=remaining)
            finally:
                stack, self._scan_frames = self._scan_frames, None
            color = self._latest_frame.copy() if self._latest_frame is not None else None
            scale_mm = self._depth_scale * 1000.0
            intrinsics = dict(self._color_intrinsics or {})

        if color is None or len(stack) < max(3, frames // 2):
            raise RuntimeError(
                f"Solo se recibieron {len(stack)} de {frames} cuadros de profundidad; revise la conexión USB 3."
            )
        depth_mm, noise_mm, valid_fraction = self._fuse_depth(stack, scale_mm)
        return {
            "color": color,
            "depth_mm": depth_mm,
            "intrinsics": intrinsics,
            "frames": len(stack),
            "noise_mm": noise_mm,
            "valid_fraction": valid_fraction,
        }

    def grab_rgbd(self, timeout: float = 2.0) -> dict[str, Any]:
        """Un solo cuadro de color + profundidad alineada, sin promediar.

        Para escanear en movimiento: no hay tiempo de promediar cuadros porque la camara se mueve;
        la fusion la hace despues el TSDF. Color y profundidad vienen del mismo frameset.
        Devuelve la profundidad en metros (float32, 0 = sin dato) y los intrinsecos del color.
        """
        with self._frame_condition:
            if not self._running:
                raise RuntimeError("La cámara no está iniciada.")
            if not self._depth_available:
                raise RuntimeError(
                    "Esta cámara no entrega profundidad. Use una Intel RealSense conectada por USB 3."
                )
            self._rgbd_slot = None
            self._rgbd_wanted = True
            deadline = time.monotonic() + timeout
            try:
                while self._rgbd_slot is None and self._running:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._frame_condition.wait(timeout=remaining)
            finally:
                slot, self._rgbd_slot = self._rgbd_slot, None
                self._rgbd_wanted = False
            scale = self._depth_scale
            intrinsics = dict(self._color_intrinsics or {})
        if slot is None:
            raise RuntimeError("La cámara no entregó un cuadro con profundidad a tiempo.")
        color, raw = slot
        depth_m = raw.astype(np.float32) * np.float32(scale)
        depth_m[raw == 0] = 0.0
        return {"color": color, "depth_m": depth_m, "intrinsics": intrinsics}

    @staticmethod
    def _fuse_depth(stack: list[np.ndarray], scale_mm: float) -> tuple[np.ndarray, float, float]:
        """Mediana temporal por pixel (por bloques de filas para acotar la memoria)."""
        cube = np.stack(stack)
        count, height, width = cube.shape
        fused = np.full((height, width), np.nan, np.float32)
        noise_samples: list[float] = []
        min_valid = max(2, int(np.ceil(0.4 * count)))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            for row in range(0, height, 48):
                block = cube[:, row:row + 48].astype(np.float32) * scale_mm
                block[block <= 0] = np.nan
                valid = np.isfinite(block).sum(axis=0)
                median = np.nanmedian(block, axis=0)
                median[valid < min_valid] = np.nan
                fused[row:row + 48] = median
                spread = np.nanstd(block, axis=0)
                spread = spread[(valid >= min_valid) & np.isfinite(spread)]
                if spread.size:
                    noise_samples.append(float(np.median(spread)))
        noise = float(np.median(noise_samples)) if noise_samples else float("nan")
        return fused, noise, float(np.isfinite(fused).mean())

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
        # Nunca debe bloquearse: si el candado esta tomado se devuelve el ultimo estado conocido.
        if not self._state_lock.acquire(timeout=STATUS_LOCK_TIMEOUT):
            return {**self._last_status, "busy": True}
        try:
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

            snapshot = {
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
                "depth_available": self._depth_available,
            }
            self._last_status = snapshot
            return snapshot
        finally:
            self._state_lock.release()


camera_service = CameraService()
