"""Flujo completo por la API con una RealSense simulada: escanear -> analizar -> guardar -> modelo.

Uso (desde backend/):  python tests/test_scan_api.py
Usa una carpeta de datos temporal: no toca la base de datos real.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
os.environ["OI_DATA_DIR"] = tempfile.mkdtemp(prefix="oi_scan_test_")
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import fake_realsense as fake  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules.camera import service as camera_module  # noqa: E402

camera_module.rs = fake


def main() -> None:
    import main as app_module

    with TestClient(app_module.app) as client:
        started = client.post("/api/camera/start", json={"provider": "realsense", "width": 1280, "height": 720})
        assert started.status_code == 200 and started.json()["depth_available"], started.text

        scan = client.post("/api/camera/scan", json={"frames": 10})
        assert scan.status_code == 200, scan.text
        scan_json = scan.json()
        scan_id = scan_json["scan_id"]
        print("escaneo:", scan_json)
        jpg = client.get(f"/api/camera/scan/{scan_id}/color.jpg")
        assert jpg.status_code == 200 and jpg.content[:3] == b"\xff\xd8\xff"
        assert client.get("/api/camera/scan/zzzz/color.jpg").status_code == 404

        # Analisis con profundidad: escala real sin dimension conocida.
        analysis = client.post(
            "/api/reference/analyze",
            files={"photo": ("scan.jpg", jpg.content, "image/jpeg")},
            data={"scan_id": scan_id},
        )
        assert analysis.status_code == 200, analysis.text
        result = analysis.json()
        relief = result["relief"]
        assert relief["available"], relief
        measures = relief["measures"]
        truth = fake.WORLD.truth
        print("medidas:", {k: measures[k] for k in ("scale_mm_per_px", "tilt_deg", "thickness_mm", "flatness_mm", "quality")})
        assert result["scale_source"] == "profundidad" and not result["relative"]
        assert result["dimensions"]["unit"] == "mm"
        assert abs(result["scale"] / truth["scale_mm_per_px"] - 1) < 0.01, (result["scale"], truth["scale_mm_per_px"])
        assert relief["grid"]["bounds"]["x1"] > relief["grid"]["bounds"]["x0"]
        heights = [f["height_mm"] for f in result["features"] if "height_mm" in f]
        assert heights, "los features deben traer su altura medida"
        length_mm = result["dimensions"]["length"]
        assert 300 < length_mm < 900, f"largo de la pieza en mm razonable: {length_mm}"

        # Escaneo caducado / inexistente.
        missing = client.post(
            "/api/reference/analyze",
            files={"photo": ("x.jpg", jpg.content, "image/jpeg")},
            data={"scan_id": "0123456789ab"},
        )
        assert missing.status_code == 404

        # Guardado: el cliente devuelve lo analizado (sin la vista previa ni la imagen).
        reference = {k: v for k, v in result.items() if k not in ("preview_png", "image_jpeg", "relief", "scan_id")}
        saved = client.post(
            "/api/reference/save",
            files={"photo": ("scan.jpg", jpg.content, "image/jpeg")},
            data={"part_no": "ESC3D", "revision": "A", "reference": json.dumps(reference), "scan_id": scan_id},
        )
        assert saved.status_code == 201, saved.text
        drawing_id = saved.json()["drawing_id"]

        model = client.get(f"/api/reference/{drawing_id}/model")
        assert model.status_code == 200, model.text
        data = model.json()
        assert data["relief"] and data["relief"]["available"], "el modelo debe incluir el relieve"
        grid = data["relief"]["grid"]
        cells = np.frombuffer(base64.b64decode(grid["data_b64"]), dtype="<i2")
        assert cells.size == grid["width"] * grid["height"], (cells.size, grid)
        assert data["texture"] and client.get(data["texture"]["url"]).status_code == 200
        assert data["unit"] == "mm"
        assert all("height_mm" in f for f in data["features"] if f["type"] != "BARRENO" and f.get("height_mm") is not None)
        print(f"modelo: malla {grid['width']}x{grid['height']}, {len(data['features'])} features, unidad {data['unit']}")

        client.post("/api/camera/stop")
    print("TODO OK")


if __name__ == "__main__":
    main()
