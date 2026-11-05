"""Escaner 3D completo por la API con una RealSense simulada que se mueve sobre una placa grande.

Uso (desde backend/):  python tests/test_scanner_api.py
Placa real simulada: 600 x 400 mm, 3 mm de espesor, sobre una mesa. Compara la malla reconstruida con
esas medidas. Usa una carpeta de datos temporal.
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
os.environ["OI_DATA_DIR"] = tempfile.mkdtemp(prefix="oi_scanner_test_")
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import fake_scan_camera as fake  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules.camera import service as camera_module  # noqa: E402

camera_module.rs = fake


def wait_for(client, predicate, timeout: float, label: str) -> dict:
    deadline = time.time() + timeout
    status = {}
    while time.time() < deadline:
        status = client.get("/api/scanner/status").json()
        if predicate(status):
            return status
        time.sleep(0.4)
    raise AssertionError(f"tiempo agotado esperando: {label}; ultimo estado: {status}")


def main() -> None:
    import main as app_module

    fake.reset(step=0.012)
    steps = len(fake.SCENE.trajectory)
    with TestClient(app_module.app) as client:
        # --- sin camara: mensaje claro --------------------------------------------------------
        refused = client.post("/api/scanner/start", json={})
        assert refused.status_code == 409 and "cámara" in refused.json()["detail"].lower(), refused.text
        idle = client.get("/api/scanner/status").json()
        assert idle["state"] == "idle" and idle["available"] and not idle["camera_running"]

        started = client.post("/api/camera/start", json={"provider": "realsense", "width": 640, "height": 360})
        assert started.status_code == 200 and started.json()["depth_available"], started.text

        # --- escaneo ----------------------------------------------------------------------------
        go = client.post("/api/scanner/start", json={"voxel_mm": 2.0})
        assert go.status_code == 200 and go.json()["state"] == "scanning", go.text
        assert client.post("/api/scanner/start", json={}).status_code == 409, "no permite dos escaneos a la vez"

        mid = wait_for(client, lambda s: s["frames"] >= steps // 2, 120, "mitad del recorrido")
        assert mid["tracking"] == "ok" and mid["keyframes"] > 5, mid
        assert mid["coverage_mm"] and max(mid["coverage_mm"]) > 200, mid["coverage_mm"]
        assert mid["camera"]["position"], "debe informar la posicion de la camara"
        raw = client.get("/api/scanner/preview.bin").content
        count = struct.unpack("<I", raw[:4])[0]
        assert count > 1000 and len(raw) == 4 + count * 12 + count * 3, (count, len(raw))
        print(f"a mitad del recorrido: {mid['frames']} cuadros, {mid['keyframes']} fotogramas clave, "
              f"cobertura {mid['coverage_mm']} mm, {count} puntos de vista previa")

        paused = client.post("/api/scanner/pause").json()
        assert paused["state"] == "paused"
        frozen = paused["frames"]
        time.sleep(1.5)
        assert client.get("/api/scanner/status").json()["frames"] <= frozen + 1, "en pausa no procesa cuadros"
        assert client.post("/api/scanner/resume").json()["state"] == "scanning"

        done = wait_for(client, lambda s: s["frames"] >= steps, 180, "fin del recorrido")
        print(f"recorrido completo: {done['frames']} cuadros, perdidos {done['lost_total']}, {done['fps']} fps")
        assert done["lost_total"] <= 2, "el rastreo no debe perderse en una trayectoria suave"

        # --- malla ------------------------------------------------------------------------------
        finishing = client.post("/api/scanner/finish", json={"isolate": True})
        assert finishing.status_code == 200 and finishing.json()["state"] == "processing"
        result = wait_for(client, lambda s: s["state"] in ("done", "error"), 90, "malla")
        assert result["state"] == "done", result["message"]
        assert result["isolated"], result["isolate_message"]
        analysis = result["analysis"]
        length, width, thickness = analysis["dimensions_mm"]
        print("pieza aislada:", analysis["dimensions_mm"], "mm; planitud", analysis["flatness"])
        assert abs(length - 600) <= 12 and abs(width - 400) <= 12, f"dimensiones {length} x {width} vs 600 x 400"
        assert thickness <= 9, f"alto {thickness} mm: la placa mide 3 mm (+ conos de 3 mm)"
        assert analysis["flatness"]["peak_to_valley_mm"] < 3 and analysis["flatness"]["tilt_deg"] < 1, analysis["flatness"]
        assert analysis["triangles"] > 50000

        blob = client.get("/api/scanner/mesh.bin", params={"max_triangles": 60000}).content
        vertices, faces = struct.unpack("<II", blob[:8])
        assert len(blob) == 8 + vertices * 12 + vertices * 3 + faces * 12 and 0 < faces <= 70000, (vertices, faces)
        positions = np.frombuffer(blob, np.float32, vertices * 3, 8).reshape(-1, 3)
        assert positions.min() >= -0.5, "la malla queda apoyada en el origen (mm)"

        # --- exportar ---------------------------------------------------------------------------
        stl = client.get("/api/scanner/export.stl")
        assert stl.status_code == 200 and "attachment" in stl.headers["content-disposition"]
        triangles = struct.unpack("<I", stl.content[80:84])[0]
        assert len(stl.content) == 84 + 50 * triangles and triangles == analysis["triangles"], "STL binario consistente"
        stl_vertices = np.frombuffer(stl.content[84:], np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), triangles)["v"]
        extent = (stl_vertices.reshape(-1, 3).max(0) - stl_vertices.reshape(-1, 3).min(0)).round(0).tolist()
        assert abs(extent[0] - length) < 1 and abs(extent[1] - width) < 1, "el STL esta en mm y con las mismas medidas"
        assert len(client.get("/api/scanner/export.ply").content) > 10000
        assert b"v " in client.get("/api/scanner/export.obj").content[:2000]
        assert client.get("/api/scanner/export.step").status_code == 404
        print(f"STL: {triangles} triangulos, {len(stl.content) / 1e6:.1f} MB, extension {extent} mm")

        # --- con y sin mesa, guardado -----------------------------------------------------------
        client.post("/api/scanner/reprocess", json={"isolate": False})
        full = wait_for(client, lambda s: s["state"] == "done", 90, "malla completa")
        assert not full["isolated"] and full["analysis"]["dimensions_mm"][0] > length + 20, "sin aislar incluye la mesa visible"

        saved = client.post("/api/scanner/save", json={"name": "Placa de prueba"})
        assert saved.status_code == 200, saved.text
        scan_id = saved.json()["id"]
        assert [s["id"] for s in client.get("/api/scanner/saved").json()] == [scan_id]
        client.post("/api/scanner/reset")
        assert client.get("/api/scanner/status").json()["state"] == "idle"
        opened = client.post(f"/api/scanner/saved/{scan_id}/open")
        assert opened.status_code == 200 and opened.json()["has_mesh"] and not opened.json()["can_reprocess"]
        assert client.get("/api/scanner/export.stl").status_code == 200, "un escaneo guardado tambien se exporta"
        assert client.delete(f"/api/scanner/saved/{scan_id}").status_code == 200
        assert client.get("/api/scanner/saved").json() == []
        assert client.post("/api/scanner/saved/000000000000/open").status_code == 404

        client.post("/api/scanner/reset")
        client.post("/api/camera/stop")
    print("TODO OK")


if __name__ == "__main__":
    main()
