"""Comparador optico general: alta de patron, inspeccion, explicacion de discrepancias y BD separada.

Uso (desde backend/):  python tests/test_compare.py

Usa una BD y una carpeta temporales (no toca los datos reales) y fotos de tests/ como piezas de ejemplo
(el comparador no sabe que son paneles: es una comparacion visual cualquiera).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

TMP = Path(tempfile.mkdtemp(prefix="compare_test_"))
os.environ["OI_COMPARE_DB_PATH"] = str(TMP / "compare.db")
os.environ["OI_COMPARE_DIR"] = str(TMP / "files")
os.environ["OI_DB_PATH"] = str(TMP / "main.db")
os.environ["OI_DATA_DIR"] = str(TMP)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules.compare.router import router  # noqa: E402

app = FastAPI()
app.include_router(router, prefix="/api")
client = TestClient(app)

BRIO = sorted((BACKEND / "tests" / "panel_curvo_completo").glob("*.jpg"))[0]
PIECE = (170, 330, 1670, 230)  # x, y, w, h del recorte de la pieza dentro de la foto


def jpeg(image: np.ndarray) -> bytes:
    return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


def crop_form(rect=PIECE) -> str:
    return json.dumps({"x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3]})


def test_flow() -> None:
    photo = cv2.imread(str(BRIO))
    # 1) Dar de alta el patron: foto completa + recorte.
    created = client.post("/api/compare/parts", data={"name": "Tira A", "crop": crop_form()}, files={"photo": ("a.jpg", jpeg(photo), "image/jpeg")})
    assert created.status_code == 200, created.text
    part = created.json()
    assert part["name"] == "Tira A" and part["revision"] == 1 and part["width"] == 1670
    assert [p["name"] for p in client.get("/api/compare/parts").json()] == ["Tira A"]

    # 2) La misma pieza, movida y con otro recorte: debe salir OK sin que el usuario alinee nada.
    h, w = photo.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 3, 0.92)
    matrix[0, 2] += 25
    matrix[1, 2] += 15
    moved = cv2.warpAffine(photo, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    answer = client.post("/api/compare/inspect", data={"part_name": "tira a", "crop": crop_form((120, 280, 1760, 340))}, files={"photo": ("b.jpg", jpeg(moved), "image/jpeg")})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "OK", answer.json()["summary"]

    # 3) Defectos sembrados: cada uno debe dar una zona con explicacion.
    defective = photo.copy()
    cv2.circle(defective, (170 + 700, 330 + 100), 22, (30, 30, 30), -1)               # mancha oscura al centro
    cv2.rectangle(defective, (170 + 1500, 330 + 60), (170 + 1590, 330 + 150), (40, 60, 200), -1)  # parche rojo
    cv2.line(defective, (170 + 300, 330 + 40), (170 + 420, 330 + 170), (20, 20, 20), 3)           # rayon
    answer = client.post("/api/compare/inspect", data={"part_id": part["id"], "crop": crop_form()}, files={"photo": ("c.jpg", jpeg(defective), "image/jpeg")})
    assert answer.status_code == 200, answer.text
    report = answer.json()
    assert report["status"] == "FALLO", report["summary"]
    assert len(report["regions"]) >= 3, [r["explanation"] for r in report["regions"]]
    kinds = {r["kind"] for r in report["regions"]}
    assert "color" in kinds, kinds
    for region in report["regions"]:
        assert region["explanation"] and region["probable_cause"] and region["zone"]
        assert client.get(f"/api{report['images'][f'region-{region['id']}']}").status_code == 200
    for kind in ("annotated", "heatmap", "test", "golden", "aligned"):
        assert client.get(f"/api{report['images'][kind]}").status_code == 200, kind
    assert client.get(f"/api/compare/inspections/{report['id']}").json()["status"] == "FALLO"
    assert len(client.get("/api/compare/inspections", params={"part": "Tira A"}).json()) == 2

    # 4) Mismo nombre = nueva revision; la anterior se conserva.
    again = client.post("/api/compare/parts", data={"name": "TIRA A", "crop": crop_form()}, files={"photo": ("a.jpg", jpeg(photo), "image/jpeg")})
    assert again.json()["revision"] == 2
    assert len(client.get("/api/compare/parts").json()) == 1
    print("flujo alta / inspeccion / explicacion: OK")


def test_golden_is_a_crop_of_the_photo() -> None:
    """El patron es un recorte; la inspeccion usa la foto COMPLETA (sin recortar): el recorte se busca solo."""
    photo = cv2.imread(str(BRIO))
    crop = (1480, 320, 360, 240)  # extremo derecho de la pieza
    created = client.post("/api/compare/parts", data={"name": "Extremo", "crop": crop_form(crop)}, files={"photo": ("a.jpg", jpeg(photo), "image/jpeg")})
    assert created.status_code == 200, created.text
    h, w = photo.shape[:2]
    for angle, scale, dx, dy in ((0, 1.0, 0, 0), (25, 0.85, 120, -60), (-40, 0.9, -150, 120)):
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
        matrix[0, 2] += dx
        matrix[1, 2] += dy
        shot = cv2.warpAffine(photo, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
        answer = client.post("/api/compare/inspect", data={"part_name": "Extremo"}, files={"photo": ("b.jpg", jpeg(shot), "image/jpeg")})
        assert answer.status_code == 200, (angle, answer.text)
        report = answer.json()
        assert report["status"] == "OK", (angle, report["summary"])
        assert abs(report["alignment"]["rotation_deg"] + angle) < 3, report["alignment"]
        assert report["located"]["area_fraction"] < 0.2 and len(report["located"]["polygon"]) == 4
        assert client.get(f"/api{report['images']['located']}").status_code == 200
    # Defecto en la zona del patron, con la foto completa.
    defective = photo.copy()
    cv2.circle(defective, (1600, 420), 10, (30, 30, 30), -1)
    answer = client.post("/api/compare/inspect", data={"part_name": "Extremo"}, files={"photo": ("c.jpg", jpeg(defective), "image/jpeg")})
    assert answer.status_code == 200 and answer.json()["regions"], answer.text
    # Una foto donde NO esta el patron: no concluyente, no un veredicto inventado.
    other = cv2.imread(str(BACKEND / "tests" / "panel1.png"))
    answer = client.post("/api/compare/inspect", data={"part_name": "Extremo"}, files={"photo": ("o.jpg", jpeg(other), "image/jpeg")})
    assert answer.status_code == 422 and "NO CONCLUYENTE" in answer.json()["detail"], answer.text
    print("patron recortado, inspeccion con foto completa (cualquier posicion/giro): OK")


def _code(fmt: str, text: str, module_px: int) -> np.ndarray:
    import zxingcpp

    image = np.array(zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(text, getattr(zxingcpp.BarcodeFormat, fmt)), scale=module_px))
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image


def _labelled(photo: np.ndarray, qr: str, serial: str) -> np.ndarray:
    """La foto de la tira con una etiqueta: un QR y un Code128 pegados sobre la pieza."""
    out = photo.copy()
    q = _code("QRCode", qr, 4)
    b = _code("Code128", serial, 2)
    out[350:350 + q.shape[0], 1080:1080 + q.shape[1]] = q
    out[455:455 + b.shape[0], 1300:1300 + b.shape[1]] = b
    return out


def test_reading_zones() -> None:
    """Zonas de lectura del patron: se leen en la foto aunque la pieza este movida, y se validan."""
    photo = cv2.imread(str(BRIO))
    good = _labelled(photo, "OI-SERIE-0042", "PN-7731-B")
    zones = json.dumps([
        {"name": "QR", "kind": "code", "rule": "equals", "expected": "OI-SERIE-0042", "x": 1070, "y": 340, "w": 150, "h": 150},
        {"name": "Serie", "kind": "code", "rule": "regex", "pattern": r"PN-\d{4}-[A-Z]", "x": 1290, "y": 445, "w": 300, "h": 100},
    ])
    created = client.post("/api/compare/parts", data={"name": "Etiquetada", "crop": crop_form(), "readings": zones},
                          files={"photo": ("a.jpg", jpeg(good), "image/jpeg")})
    assert created.status_code == 200, created.text
    assert len(created.json()["readings"]) == 2 and created.json()["readings"][0]["x"] == 1070 - PIECE[0]

    h, w = good.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 6, 0.9)
    matrix[0, 2] += 30
    moved = cv2.warpAffine(good, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    report = client.post("/api/compare/inspect", data={"part_name": "Etiquetada"}, files={"photo": ("b.jpg", jpeg(moved), "image/jpeg")}).json()
    readings = {r["name"]: r for r in report["readings"]}
    assert readings["QR"]["status"] == "OK" and readings["QR"]["value"] == "OI-SERIE-0042", readings
    assert readings["Serie"]["status"] == "OK" and readings["Serie"]["value"] == "PN-7731-B", readings
    assert client.get(f"/api{report['images']['reading-' + readings['QR']['id']]}").status_code == 200

    wrong = cv2.warpAffine(_labelled(photo, "OI-SERIE-0099", "XX-7731-B"), matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    report = client.post("/api/compare/inspect", data={"part_name": "Etiquetada"}, files={"photo": ("c.jpg", jpeg(wrong), "image/jpeg")}).json()
    readings = {r["name"]: r for r in report["readings"]}
    assert readings["QR"]["status"] == "FALLO" and "OI-SERIE-0099" in readings["QR"]["reason"], readings
    assert readings["Serie"]["status"] == "FALLO", readings
    assert report["status"] == "FALLO" and "Lecturas" in report["summary"]

    # Zona fuera del area del patron: se rechaza al guardar.
    outside = json.dumps([{"name": "X", "kind": "code", "rule": "present", "x": 10, "y": 10, "w": 50, "h": 50}])
    bad = client.post("/api/compare/parts", data={"name": "Mala", "crop": crop_form(), "readings": outside}, files={"photo": ("a.jpg", jpeg(good), "image/jpeg")})
    assert bad.status_code == 400 and "fuera" in bad.json()["detail"], bad.text
    print("zonas de lectura en el comparador (pieza movida, valor correcto y distinto): OK")


def test_errors() -> None:
    photo = cv2.imread(str(BRIO))
    assert client.post("/api/compare/parts", data={"name": "  "}, files={"photo": ("a.jpg", jpeg(photo), "image/jpeg")}).status_code == 400
    assert client.post("/api/compare/inspect", data={"part_name": "no existe"}, files={"photo": ("a.jpg", jpeg(photo), "image/jpeg")}).status_code == 404
    assert client.post("/api/compare/parts", data={"name": "X"}, files={"photo": ("a.jpg", b"basura", "image/jpeg")}).status_code == 400
    assert client.post("/api/compare/parts", data={"name": "X", "crop": crop_form((0, 0, 20, 20))}, files={"photo": ("a.jpg", jpeg(photo), "image/jpeg")}).status_code == 400
    # Una pieza totalmente distinta no es alineable: NO CONCLUYENTE (422), no un veredicto inventado.
    other = cv2.imread(str(BACKEND / "tests" / "panel1.png"))
    answer = client.post("/api/compare/inspect", data={"part_name": "Tira A"}, files={"photo": ("o.jpg", jpeg(other), "image/jpeg")})
    assert answer.status_code == 422 and "NO CONCLUYENTE" in answer.json()["detail"], answer.text
    dark = np.full((400, 600, 3), 10, np.uint8)
    answer = client.post("/api/compare/inspect", data={"part_name": "Tira A"}, files={"photo": ("d.jpg", jpeg(dark), "image/jpeg")})
    assert answer.status_code == 422, answer.text
    print("errores y NO CONCLUYENTE: OK")


def test_thin_pen_mark() -> None:
    """Una raya fina de pluma sobre una hoja se detecta; las marcas por debajo del umbral se avisan, no se ocultan."""
    from modules.compare.engine import compare

    rng = np.random.default_rng(3)
    h, w = 1700, 2400
    yy, xx = np.mgrid[0:h, 0:w]
    sheet = np.clip(np.full((h, w, 3), 215, np.float32) + (10 * np.sin(xx / 700) + 6 * np.cos(yy / 500))[..., None], 0, 255).astype(np.uint8)
    cv2.putText(sheet, "FORMATO 12-B", (300, 400), cv2.FONT_HERSHEY_SIMPLEX, 3, (40, 40, 40), 6, cv2.LINE_AA)
    cv2.rectangle(sheet, (300, 700), (2000, 1500), (60, 60, 60), 3)

    def shot(pen=None) -> np.ndarray:
        image = sheet.copy()
        if pen:
            cv2.line(image, (900, 900), (1500, 1200), pen, 2, cv2.LINE_AA)
        noisy = image.astype(np.float32) + rng.normal(0, 3.5, image.shape[:2])[..., None]
        return cv2.GaussianBlur(np.clip(noisy, 0, 255).astype(np.uint8), (0, 0), 0.9)

    golden = shot()
    clean = compare(golden, shot(), "estricto")["report"]
    assert clean["status"] == "OK", clean["summary"]
    for pen in ((190, 120, 70), (70, 70, 70)):
        report = compare(golden, shot(pen), "estricto")["report"]
        assert report["regions"], f"no detecto la raya {pen}"
    faint = compare(golden, shot((205, 170, 150)), "maximo")["report"]
    assert faint["regions"], "la sensibilidad maxima debe ver una raya tenue"
    assert "peak_delta_e" in faint["metrics"] and "near_misses" in faint
    print("raya fina de pluma: OK")


def test_separate_database() -> None:
    """Los datos del comparador viven en su archivo; el de paneles no se toca ni se crea."""
    tables = {r[0] for r in sqlite3.connect(TMP / "compare.db").execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"part", "inspection"} <= tables, tables
    assert not (TMP / "main.db").exists(), "el comparador no debe crear ni usar la BD de paneles"
    print("BD separada de paneles: OK")


if __name__ == "__main__":
    test_flow()
    test_golden_is_a_crop_of_the_photo()
    test_reading_zones()
    test_errors()
    test_thin_pen_mark()
    test_separate_database()
    print("TODO OK")
