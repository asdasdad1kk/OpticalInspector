"""Prueba el servicio de camara (escaneo 3D) contra un simulador de la RealSense.

Uso (desde backend/):  python tests/test_camera_scan.py
Verifica la logica de captura (stream de profundidad, alineado, mediana temporal, fallback a
solo color); no sustituye una prueba con la camara real.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import fake_realsense as fake  # noqa: E402
from modules.camera import service as camera_module  # noqa: E402


def make_service():
    camera_module.rs = fake
    fake.WORLD.calls.clear()
    return camera_module.CameraService()


def test_scan_with_depth() -> None:
    fake.WORLD.depth_supported = True
    service = make_service()
    status = service.start(width=1280, height=720, fps=30, provider="realsense")
    try:
        assert status["depth_available"], "debe reportar profundidad disponible"
        assert fake.WORLD.depth_units == camera_module.DEPTH_UNITS_M, "debe pedir unidad de 0.1 mm"
        time.sleep(0.3)
        assert "align" not in fake.WORLD.calls, "el alineado solo corre durante un escaneo"

        started = time.time()
        scan = service.capture_depth_scan(frames=12)
        elapsed = time.time() - started
        assert "align" in fake.WORLD.calls
        assert scan["color"].shape == (720, 1280, 3) and scan["depth_mm"].shape == (720, 1280)
        assert scan["frames"] == 12
        assert scan["valid_fraction"] > 0.9, scan["valid_fraction"]
        assert 0.1 < scan["noise_mm"] < 0.5, scan["noise_mm"]
        assert scan["intrinsics"]["fx"] > 0

        clean = fake.WORLD.scene["depth_clean"]
        both = np.isfinite(scan["depth_mm"]) & np.isfinite(clean)
        error = np.median(np.abs(scan["depth_mm"][both] - clean[both]))
        assert error < 0.2, f"la mediana temporal debe acercarse a la verdad ({error:.3f} mm)"
        print(f"escaneo OK: {scan['frames']} cuadros en {elapsed:.1f}s, ruido {scan['noise_mm']:.2f} mm, "
              f"error mediano {error:.3f} mm")

        calls_before = fake.WORLD.calls.count("align")
        time.sleep(0.3)
        assert fake.WORLD.calls.count("align") <= calls_before + 1, "debe dejar de alinear al terminar"
    finally:
        service.stop()


def test_grab_rgbd() -> None:
    """Un cuadro color+profundidad del mismo frameset (escaner 3D en movimiento)."""
    fake.WORLD.depth_supported = True
    service = make_service()
    service.start(width=1280, height=720, fps=30, provider="realsense")
    try:
        time.sleep(0.2)
        before = fake.WORLD.calls.count("align")
        frame = service.grab_rgbd()
        assert frame["color"].shape == (720, 1280, 3) and frame["depth_m"].shape == (720, 1280)
        assert frame["depth_m"].dtype == np.float32
        valid = frame["depth_m"] > 0
        assert valid.mean() > 0.9, "la mayoria de pixeles tienen profundidad"
        assert 0.3 < float(np.median(frame["depth_m"][valid])) < 0.6, "profundidad en metros (la escena esta a ~0.45 m)"
        assert frame["intrinsics"]["fx"] > 0
        assert fake.WORLD.calls.count("align") >= before + 1
        time.sleep(0.3)
        settled = fake.WORLD.calls.count("align")
        time.sleep(0.3)
        assert fake.WORLD.calls.count("align") == settled, "sin peticion no se alinea (no gasta CPU)"
        # varias lecturas seguidas (el escaner pide un cuadro tras otro)
        for _ in range(5):
            assert service.grab_rgbd()["depth_m"].shape == (720, 1280)
        print("grab_rgbd: cuadro color+profundidad en metros, sin gasto cuando no se pide: OK")
    finally:
        service.stop()


def test_fallback_without_depth() -> None:
    fake.WORLD.depth_supported = False
    service = make_service()
    status = service.start(width=1280, height=720, fps=30, provider="realsense")
    try:
        assert status["running"] and not status["depth_available"]
        time.sleep(0.2)
        assert service.get_preview() is not None, "el preview a color debe seguir funcionando"
        try:
            service.capture_depth_scan(frames=5)
        except RuntimeError as error:
            assert "profundidad" in str(error).lower()
        else:
            raise AssertionError("debe negar el escaneo sin profundidad")
        print("fallback a solo color: OK")
    finally:
        service.stop()
        fake.WORLD.depth_supported = True


def test_scan_requires_camera() -> None:
    service = make_service()
    try:
        service.capture_depth_scan()
    except RuntimeError as error:
        assert "iniciada" in str(error)
    else:
        raise AssertionError("debe exigir la camara iniciada")
    print("escaneo sin camara iniciada: OK")


def test_hung_probe_does_not_freeze() -> None:
    """Regresion: una busqueda colgada en DirectShow congelaba status y start() (deadlock)."""
    import threading

    fake.WORLD.depth_supported = True
    service = make_service()
    camera_module.PROBE_TIMEOUT = 0.5
    camera_module.DEVICE_LOCK_TIMEOUT = 0.8
    release = threading.Event()
    service._probe_devices = lambda max_index=8: (release.wait(10), [])[1]  # llamada nativa que no vuelve
    try:
        started = time.time()
        found = service.list_devices(refresh=True)
        assert time.time() - started < 2.0, "la busqueda debe devolver a tiempo aunque el dispositivo se cuelgue"
        assert not found["scanned"] and "no responde" in found["message"], found

        again = time.time()
        assert service.list_devices(refresh=True)["message"], "otra busqueda mientras hay una colgada: aviso inmediato"
        assert time.time() - again < 0.3

        # status responde al instante aunque la busqueda siga colgada ...
        timer = time.time()
        assert "running" in service.status
        assert time.time() - timer < 0.3, "status no debe bloquearse"

        # ... y start() falla con un mensaje claro en vez de esperar para siempre.
        timer = time.time()
        try:
            service.start(width=1280, height=720, fps=30, provider="realsense")
        except RuntimeError as error:
            assert "no responde" in str(error), error
        else:
            raise AssertionError("start() debia fallar con la busqueda colgada")
        assert time.time() - timer < 3.0
        assert not service.status["running"] and not service._starting, "el intento fallido no deja la camara 'iniciando'"

        # Al volver la llamada nativa todo se recupera.
        release.set()
        time.sleep(0.4)
        status = service.start(width=1280, height=720, fps=30, provider="realsense")
        assert status["running"]
        print("busqueda colgada: status responde, start() falla claro y luego se recupera: OK")
    finally:
        release.set()
        service.stop()
        camera_module.PROBE_TIMEOUT = 8.0
        camera_module.DEVICE_LOCK_TIMEOUT = 12.0


if __name__ == "__main__":
    test_hung_probe_does_not_freeze()
    test_scan_with_depth()
    test_grab_rgbd()
    test_fallback_without_depth()
    test_scan_requires_camera()
    print("TODO OK")
