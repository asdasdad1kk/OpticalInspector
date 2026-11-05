"""Motor del escaner 3D: precision de la odometria y casos de falla.

Uso (desde backend/):  python tests/test_scanner_engine.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
os.environ.setdefault("OI_DATA_DIR", tempfile.mkdtemp(prefix="oi_engine_test_"))
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import synthetic_scan as S  # noqa: E402

from modules.scanner3d import engine as E  # noqa: E402

INTRINSICS = dict(fx=S.FX, fy=S.FY, ppx=S.CX, ppy=S.CY, width=S.WIDTH, height=S.HEIGHT)
SCENE, MESH, TRUTH = S.build_scene()


def run(poses, options=None):
    engine = E.ScanEngine(options or E.ScanOptions(voxel_mm=2.0), INTRINSICS)
    first_inverse = np.linalg.inv(poses[0])
    errors, results = [], []
    for index, pose in enumerate(poses):
        color, depth = S.render(SCENE, MESH, pose, seed=index)
        results.append(engine.process(color, depth))
        errors.append(float(np.linalg.norm(engine.pose[:3, 3] - (first_inverse @ pose)[:3, 3])) * 1000)
    return engine, errors, results


def test_tracking_accuracy() -> None:
    engine, errors, _ = run(S.trajectory(step=0.010))
    assert engine.lost_total == 0 and engine.tracking == "ok"
    assert max(errors) < 15.0, f"deriva maxima {max(errors):.1f} mm en 0.6 m de recorrido (cota: 2.4 %)"
    assert engine.keyframes >= 30
    assert 5.0 < engine.speed_mm_per_frame < 20.0, engine.speed_mm_per_frame
    print(f"rastreo: deriva maxima {max(errors):.1f} mm en 0.6 m, {engine.keyframes} fotogramas clave, "
          f"{engine.speed_mm_per_frame:.1f} mm/cuadro")


def test_lost_and_recover() -> None:
    poses = S.trajectory(step=0.010)
    engine = E.ScanEngine(E.ScanOptions(voxel_mm=2.0), INTRINSICS)
    for index in range(10):
        engine.process(*S.render(SCENE, MESH, poses[index], seed=index))
    good_pose = engine.pose.copy()
    keyframes = engine.keyframes

    # Cuadro vacio (tapado / fuera de rango): no se integra, se avisa y se recupera con el siguiente.
    blank = np.zeros((S.HEIGHT, S.WIDTH, 3), np.uint8)
    result = engine.process(blank, np.zeros((S.HEIGHT, S.WIDTH), np.float32))
    assert not result["tracked"] and "profundidad" in result["reason"].lower()
    assert engine.tracking == "perdido" and engine.lost_streak == 1 and engine.keyframes == keyframes
    assert np.allclose(engine.pose, good_pose), "al perder el rastreo la pose no salta"
    result = engine.process(*S.render(SCENE, MESH, poses[11], seed=11))
    assert result["tracked"] and engine.tracking == "ok" and engine.lost_streak == 0

    # Salto brusco (la camara se mueve 30 cm de golpe): no debe corromper la reconstruccion.
    jumped = S.pose_looking_down(0.25, 0.15, 0.35)
    result = engine.process(*S.render(SCENE, MESH, jumped, seed=99))
    assert not result["tracked"], result
    assert np.linalg.norm(engine.pose[:3, 3] - good_pose[:3, 3]) < 0.05, "la pose no sigue al salto"
    result = engine.process(*S.render(SCENE, MESH, poses[12], seed=12))
    assert result["tracked"], "al volver a la zona conocida recupera el rastreo"
    print("rastreo perdido (cuadro vacio y salto brusco) se avisa, no corrompe y se recupera: OK")


def test_isolate_without_table() -> None:
    """Si solo se ve la placa (sin mesa) no se inventa nada: se conserva todo y se avisa."""
    # Zona sin huecos ni bordes: a 18 cm la camara ve ~25 x 14 cm, todo dentro de la placa.
    poses = [S.pose_looking_down(x, 0.0, 0.18) for x in np.arange(-0.16, -0.09, 0.008)]
    engine, _, _ = run(poses)
    mesh, info = engine.build_mesh(isolate=True)
    assert not info["isolated"] and info["isolate_message"], info
    analysis = E.analyze_mesh(mesh)
    assert analysis["triangles"] > 5000
    assert analysis["flatness"]["peak_to_valley_mm"] < 3.0
    print(f"sin mesa visible: se conserva la pieza y se avisa ('{info['isolate_message'][:50]}...'): OK")


def test_not_enough_geometry() -> None:
    engine = E.ScanEngine(E.ScanOptions(voxel_mm=2.0), INTRINSICS)
    try:
        engine.build_mesh()
    except RuntimeError as error:
        assert "geometría" in str(error)
    else:
        raise AssertionError("sin cuadros no hay malla")
    print("sin geometria: error claro: OK")


def test_open3d_missing_is_isolated() -> None:
    """Sin Open3D solo falla el escaner (501); el resto de la app sigue sirviendo."""
    from fastapi.testclient import TestClient

    from modules.scanner3d import service as service_module

    original = service_module.load_open3d

    def missing():
        raise E.ScannerUnavailable("El escáner 3D necesita la librería Open3D. Instálela con: pip install open3d")

    service_module.load_open3d = missing
    try:
        import main as app_module

        with TestClient(app_module.app) as client:
            response = client.post("/api/scanner/start", json={})
            assert response.status_code == 501 and "open3d" in response.json()["detail"].lower(), response.text
            assert client.get("/api/camera/status").status_code == 200, "las demas rutas siguen funcionando"
            assert client.get("/api/database/parts").status_code == 200
    finally:
        service_module.load_open3d = original
    print("sin Open3D: el escaner responde 501 con instrucciones y el resto de la app sigue OK")


if __name__ == "__main__":
    test_tracking_accuracy()
    test_lost_and_recover()
    test_isolate_without_table()
    test_not_enough_geometry()
    test_open3d_missing_is_isolated()
    print("TODO OK")
