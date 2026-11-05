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

        # ---- Recorte (trim): solo una parte de la pieza --------------------------------------
        crop = [120, 40, 900, 660]
        cropped = client.post(
            "/api/reference/analyze",
            files={"photo": ("scan.jpg", jpg.content, "image/jpeg")},
            data={"scan_id": scan_id, "crop": json.dumps(crop)},
        )
        assert cropped.status_code == 200, cropped.text
        part = cropped.json()
        assert part["crop"] == crop
        import cv2

        shown = cv2.imdecode(np.frombuffer(base64.b64decode(part["image_jpeg"]), np.uint8), cv2.IMREAD_COLOR)
        assert shown.shape[:2] == (crop[3], crop[2]), shown.shape  # la imagen mostrada ES el recorte
        assert part["relief"]["available"], part["relief"]
        assert not part["enabled"]["contour"] and not part["enabled"]["label"], "contorno/etiqueta apagados con recorte"
        assert 4 <= len(part["features"]) < len(result["features"]) + 2
        limit_x, limit_y = crop[2] * part["scale"], crop[3] * part["scale"]
        assert all(0 <= f["x"] <= limit_x and 0 <= -f["y"] <= limit_y for f in part["features"]), "features dentro del recorte"
        # El recorte mueve el punto principal: la escala y la inclinacion no deben cambiar.
        assert abs(part["relief"]["measures"]["tilt_deg"] - measures["tilt_deg"]) < 0.5
        assert abs(part["scale"] / result["scale"] - 1) < 0.02
        print(f"recorte: {len(part['features'])} features en {crop[2]}x{crop[3]} px, inclinacion {part['relief']['measures']['tilt_deg']}")

        for bad in ([0, 0, 40, 30], "no-json"):
            response = client.post(
                "/api/reference/analyze",
                files={"photo": ("scan.jpg", jpg.content, "image/jpeg")},
                data={"scan_id": scan_id, "crop": json.dumps(bad) if not isinstance(bad, str) else bad},
            )
            assert response.status_code == 400, (bad, response.status_code)
        with_length = client.post(
            "/api/reference/analyze",
            files={"photo": ("scan.jpg", jpg.content, "image/jpeg")},
            data={"scan_id": scan_id, "crop": json.dumps(crop), "known_length": "500"},
        )
        assert with_length.status_code == 400 and "recorte" in with_length.json()["detail"].lower()

        # Zona con muy pocos features: se rechaza al analizar, pidiendo ampliar el recorte.
        sparse = client.post(
            "/api/reference/analyze",
            files={"photo": ("scan.jpg", jpg.content, "image/jpeg")},
            data={"scan_id": scan_id, "crop": json.dumps([120, 40, 900, 300])},
        )
        assert sparse.status_code == 400 and "recortada" in sparse.json()["detail"].lower(), sparse.text

        # Foto sin escaneo (BRIO / archivo): el recorte tambien aplica.
        plain = client.post(
            "/api/reference/analyze",
            files={"photo": ("scan.jpg", jpg.content, "image/jpeg")},
            data={"crop": json.dumps(crop)},
        )
        assert plain.status_code == 200 and plain.json()["crop"] == crop and plain.json()["relative"]

        part_reference = {k: v for k, v in part.items() if k not in ("preview_png", "image_jpeg", "relief", "scan_id")}
        saved_crop = client.post(
            "/api/reference/save",
            files={"photo": ("scan.jpg", jpg.content, "image/jpeg")},
            data={"part_no": "ESC3D-RECORTE", "revision": "A", "reference": json.dumps(part_reference),
                  "scan_id": scan_id, "crop": json.dumps(crop)},
        )
        assert saved_crop.status_code == 201, saved_crop.text
        stored = client.get(f"/api/reference/{saved_crop.json()['drawing_id']}/model").json()
        assert stored["texture"]["width"] == crop[2] and stored["texture"]["height"] == crop[3], stored["texture"]
        assert stored["relief"]["available"]
        print("recorte guardado: textura", stored["texture"]["width"], "x", stored["texture"]["height"])

        # ---- Restablecer configuracion ------------------------------------------------------
        from modules.inspect.calibration import calibration_service

        login = client.post("/api/config/login", json={"password": "admin"})
        assert login.status_code == 200, login.text
        headers = {"X-Config-Token": login.json()["token"]}
        assert client.put("/api/config/settings/inspection",
                          json={"position_ok_radii": 0.8, "position_review_radii": 1.5, "min_presence": 0.6},
                          headers=headers).status_code == 200
        assert client.put("/api/config/settings/calibration", json={"apply": False, "square_mm": 45.0}, headers=headers).status_code == 200
        assert client.put("/api/config/parts/ESC3D/overrides", json={"min_presence": 0.5}, headers=headers).status_code == 200
        calibration_service._write({"calibrated_at": "prueba", "image_size": [1280, 720]})
        assert calibration_service.load() is not None

        changed = client.get("/api/config/settings", headers=headers).json()
        assert changed["inspection"]["min_presence"] == 0.6 and changed["parts"], changed["parts"]

        reset = client.post("/api/config/reset", json={}, headers=headers)
        assert reset.status_code == 200, reset.text
        outcome = reset.json()
        defaults = outcome["settings"]
        assert defaults["inspection"]["min_presence"] == 0.30 and defaults["inspection"]["position_ok_radii"] == 0.45
        assert defaults["calibration"] == {"apply": True, "square_mm": 30.0}
        assert defaults["parts"] == {}, "los ajustes por numero de parte tambien se restablecen"
        assert calibration_service.load() is None, "la calibracion se borra"
        assert outcome["camera_restarted"] is True, outcome
        assert client.post("/api/config/login", json={"password": "admin"}).status_code == 200, "la clave no se toca"

        # Restablecimiento parcial: solo la calibracion, los ajustes se conservan.
        client.put("/api/config/settings/inspection", json={"min_presence": 0.7}, headers=headers)
        partial = client.post("/api/config/reset", json={"general": False, "parts": False, "calibration": True}, headers=headers).json()
        assert partial["settings"]["inspection"]["min_presence"] == 0.7 and partial["camera_restarted"] is None
        print("restablecer: ajustes, partes y calibracion vuelven a fabrica; la clave se conserva")

        client.post("/api/camera/stop")
    print("TODO OK")


if __name__ == "__main__":
    main()
