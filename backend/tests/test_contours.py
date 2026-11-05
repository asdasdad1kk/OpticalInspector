"""Contorno de la pieza: debe seguir al panel de cada foto de prueba.

Uso (desde backend/):  python tests/test_contours.py

Regresion de dos fallas reales: la segmentacion cortaba la esquina superior derecha de
"delrons planos" (perdiendo 3 delrones) y dejaba picos en el borde inferior.
Los rectangulos esperados son los del panel visto en cada foto (x, y, ancho, alto).
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from modules.inspect.golden import analyze_reference  # noqa: E402

TESTS = BACKEND / "tests"


def load(name: str) -> np.ndarray:
    image = cv2.imread(str(TESTS / name))
    assert image is not None, name
    return image[130:812, 18:1232] if name == "barrenos.png" else image


# nombre: (rectangulo esperado del panel, features esperados, puntos que DEBEN quedar dentro)
CASES = {
    "panel1.png": ((130, 23, 624, 604), 34, []),
    "delrons planos.png": (
        (38, 0, 505, 528), 26,
        # la esquina superior derecha (3 delrones) y el borde inferior, que antes se perdian
        [(430, 30), (488, 29), (62, 35), (120, 497), (506, 489)],
    ),
    "barrenos.png": ((157, 157, 827, 484), 9, [(683, 395)]),
    "panel_curvo.png": ((302, 17, 115, 481), 4, [(346, 62), (390, 65), (336, 293), (373, 428)]),
}


def check(name: str) -> None:
    image = load(name)
    height, width = image.shape[:2]
    (ex, ey, ew, eh), expected_features, must_contain = CASES[name]
    result = analyze_reference(image)
    contour = np.array([[x, -y] for x, y in result["contour"]], np.float32)
    x, y, w, h = cv2.boundingRect(contour.astype(np.int32))

    tolerance_x, tolerance_y = 0.05 * width, 0.05 * height
    assert abs(x - ex) <= tolerance_x and abs(y - ey) <= tolerance_y, f"{name}: esquina {x, y} vs {ex, ey}"
    assert abs(w - ew) <= 2 * tolerance_x and abs(h - eh) <= 2 * tolerance_y, f"{name}: tamano {w, h} vs {ew, eh}"
    assert len(result["features"]) == expected_features, f"{name}: {len(result['features'])} features vs {expected_features}"
    for px, py in must_contain:
        assert cv2.pointPolygonTest(contour, (float(px), float(py)), False) >= 0, f"{name}: ({px}, {py}) quedo fuera del contorno"

    # Todo feature detectado esta dentro de la silueta (o pegado a su borde).
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [contour.astype(np.int32)], 255)
    outside = cv2.distanceTransform(255 - mask, cv2.DIST_L2, 3)
    for feature in result["features"]:
        fx, fy = int(feature["x"]), int(-feature["y"])
        assert mask[fy, fx] > 0 or outside[fy, fx] <= 1.5 * feature["radius"], f"{name}: feature {feature['id']} fuera"
    print(f"{name}: contorno {x, y, w, h}, {len(result['features'])} features: OK")


if __name__ == "__main__":
    for case in CASES:
        check(case)
    print("TODO OK")
