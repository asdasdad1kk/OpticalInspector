"""Lectura de codigos y texto (zonas de lectura).

Uso (desde backend/):  python tests/test_reading.py

Los codigos se generan en el momento (zxing-cpp / OpenCV), se pegan sobre una "etiqueta" y se leen girados,
chicos y con ruido. Las pruebas de texto (Tesseract) se omiten con aviso si no esta instalado.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("OI_SETTINGS_FILE", str(Path(tempfile.mkdtemp()) / "settings.json"))

from modules.reading import codes, ocr, service  # noqa: E402

RNG = np.random.default_rng(7)


def make_code(fmt: str, text: str, size: int) -> np.ndarray:
    import zxingcpp

    value = getattr(zxingcpp.BarcodeFormat, fmt)
    if hasattr(zxingcpp, "create_barcode"):
        image = zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(text, value), scale=4)
    else:  # pragma: no cover - versiones viejas
        image = zxingcpp.write_barcode(value, text, width=size, height=size // 3)
    image = np.array(image)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    # Factor ENTERO: un reescalado fraccionario con vecino mas cercano deja barras de ancho desigual
    # (un codigo mal impreso), que no es lo que se quiere probar aqui.
    base = image[::4, ::4] if hasattr(zxingcpp, "create_barcode") else image
    factor = max(1, round(size / max(base.shape[:2])))
    return cv2.resize(base, None, fx=factor, fy=factor, interpolation=cv2.INTER_NEAREST)


def label(code: np.ndarray, angle: float = 0.0, noise: float = 4.0) -> np.ndarray:
    """Etiqueta clara sobre metal gris, con el codigo en el centro, girada y con ruido."""
    h, w = 600, 800
    canvas = np.full((h, w, 3), 150, np.uint8)
    cv2.rectangle(canvas, (100, 80), (700, 520), (235, 235, 230), -1)
    y, x = (h - code.shape[0]) // 2, (w - code.shape[1]) // 2
    canvas[y:y + code.shape[0], x:x + code.shape[1]] = code
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    canvas = cv2.warpAffine(canvas, matrix, (w, h), borderValue=(150, 150, 150))
    noisy = canvas.astype(np.float32) + RNG.normal(0, noise, canvas.shape[:2])[..., None]
    return cv2.GaussianBlur(np.clip(noisy, 0, 255).astype(np.uint8), (0, 0), 0.7)


CASES = [
    ("QRCode", "SN:OI-2026-000123", 220),
    ("DataMatrix", "PN7731-REV-B", 140),
    ("Code128", "PN-12345-A", 360),
    ("Code39", "LOT2026A", 360),
    ("EAN13", "7501031311309", 300),
    ("PDF417", "ORDEN 4471 / LINEA 3", 560),
]


def test_codes() -> None:
    assert codes.engine_info()["engine"] == "zxing-cpp", "zxing-cpp no esta instalado"
    for fmt, text, size in CASES:
        for angle in (0, 17, 90, 180):
            found = codes.read_codes(label(make_code(fmt, text, size), angle))
            texts = [item["text"] for item in found]
            assert text in texts, (fmt, angle, found)
        if fmt in ("QRCode", "DataMatrix"):  # 2D chicos (~3 px por modulo); los lineales necesitan >= 2 px por barra
            small = codes.read_codes(label(make_code(fmt, text, max(70, size // 2)), 5))
            assert text in [item["text"] for item in small], (fmt, "chico", small)
        print(f"{fmt:10} {text!r}: OK (0/17/90/180 grados)")


def test_zone_rules() -> None:
    image = label(make_code("Code128", "PN-12345-A", 360))
    zone = {"x": 180, "y": 220, "w": 440, "h": 160}  # rodea el codigo
    ok = service.read_zone(image, service.normalize_zone({**zone, "kind": "code", "rule": "equals", "expected": "PN-12345-A"}, 0))
    assert ok["status"] == "OK" and ok["value"] == "PN-12345-A", ok
    bad = service.read_zone(image, service.normalize_zone({**zone, "kind": "code", "rule": "equals", "expected": "PN-99999-A"}, 0))
    assert bad["status"] == "FALLO" and "se esperaba" in bad["reason"], bad
    regex = service.read_zone(image, service.normalize_zone({**zone, "kind": "code", "rule": "regex", "pattern": r"PN-\d{5}-[A-Z]"}, 0))
    assert regex["status"] == "OK", regex
    empty = service.read_zone(image, service.normalize_zone({"x": 0, "y": 0, "w": 90, "h": 90, "kind": "code", "rule": "present"}, 0))
    assert empty["status"] == "REVISAR" and "No se pudo leer" in empty["reason"], empty
    record = service.read_zone(image, service.normalize_zone({"x": 0, "y": 0, "w": 90, "h": 90, "kind": "code", "rule": "record"}, 0))
    assert record["status"] == "OK", record
    # Escala: la zona en coordenadas de una imagen el doble de grande.
    big = cv2.resize(image, None, fx=2, fy=2)
    scaled = service.read_zone(big, service.normalize_zone({**zone, "kind": "code", "rule": "present"}, 0), scale=2.0)
    assert scaled["value"] == "PN-12345-A", scaled
    for wrong in ({"kind": "x", **zone}, {**zone, "kind": "code", "rule": "regex", "pattern": "("}, {"x": 1, "y": 1, "w": 2, "h": 2}):
        try:
            service.normalize_zone(wrong, 0)
        except ValueError:
            continue
        raise AssertionError(f"zona invalida aceptada: {wrong}")
    print("reglas de zona (igual, regex, presente, registrar, escala, validacion): OK")


def test_text() -> None:
    if not ocr.locate()["available"]:
        print("OCR: Tesseract no esta instalado; pruebas de texto OMITIDAS")
        return
    image = np.full((140, 640, 3), 235, np.uint8)
    cv2.putText(image, "PN 7731-B LOTE 42", (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 3, cv2.LINE_AA)
    zone = service.normalize_zone({"x": 0, "y": 0, "w": 640, "h": 140, "kind": "text", "rule": "regex", "pattern": r".*7731.*"}, 0)
    result = service.read_zone(image, zone)
    assert result["status"] == "OK", result
    print(f"OCR: {result['value']!r} (confianza {result['confidence']}): OK")


if __name__ == "__main__":
    test_codes()
    test_zone_rules()
    test_text()
    print("TODO OK")
