"""Regresion de la clasificacion de features sobre las imagenes de tests/.

Uso (desde backend/):  python tests/test_feature_types.py

Las coordenadas son las posiciones (x, y) de cada feature en la imagen y su
tipo real, anotados a mano. Se clasifica a 1x, 2x y 3x (el canvas de inspeccion
es una version ampliada de la foto) con radios iniciales de +-25 %.
"""
from __future__ import annotations

import collections
import math
import sys
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from modules.inspect.golden import analyze_reference, detect_features  # noqa: E402
from modules.inspect.pipeline import classify_feature_type, classify_large_hole  # noqa: E402

I, C, P, B = "INSERTO", "DELRON_CONICO", "DELRON_PLANO", "BARRENO"
TESTS = BACKEND / "tests"

# barrenos.png es una captura de pantalla de la app: el cuadro de camara esta en [130:812, 18:1232].
BARRENOS = [
    (611, 318, I), (806, 329, I), (961, 336, I),
    (200, 708, B), (386, 717, B), (593, 727, B), (753, 736, B), (936, 745, B),
]
PANEL1 = [
    (190, 97, C), (619, 98, C), (666, 83, C), (239, 109, C), (367, 106, C), (493, 102, C),
    (595, 137, I), (697, 133, I), (163, 150, I), (267, 147, I), (194, 212, C), (671, 197, C),
    (329, 227, I), (415, 224, I), (453, 223, I), (539, 220, I), (198, 328, C), (675, 312, C),
    (200, 444, C), (681, 427, C), (425, 500, I), (463, 499, I), (550, 496, I), (338, 503, I),
    (509, 537, C), (638, 533, C), (202, 563, C), (251, 547, C), (381, 541, C), (613, 570, I),
    (686, 544, C), (717, 566, I), (174, 588, I), (279, 583, I),
]
PLANOS = [
    (172, 33, P), (298, 33, C), (430, 30, P), (488, 29, P), (62, 35, C), (62, 98, C),
    (63, 208, C), (132, 215, C), (461, 200, C), (176, 254, P), (240, 262, C), (340, 242, C),
    (435, 250, P), (493, 248, P), (118, 273, P), (302, 270, P), (378, 269, P), (162, 309, C),
    (63, 320, C), (464, 297, C), (64, 434, C), (120, 497, P), (213, 494, C), (306, 492, P),
    (383, 491, P), (506, 489, C),
]
CASES = {"barrenos.png": BARRENOS, "panel1.png": PANEL1, "delrons planos.png": PLANOS}


def load(name: str) -> np.ndarray:
    image = cv2.imread(str(TESTS / name))
    assert image is not None, f"No se pudo leer tests/{name}"
    return image[130:812, 18:1232] if name == "barrenos.png" else image


def test_classifier() -> None:
    errors: list[str] = []
    total = 0
    for name, truth in CASES.items():
        image = cv2.imread(str(TESTS / name))
        detected = detect_features(image)
        for scale in (1.0, 2.0, 3.0):
            scaled = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else image
            gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(scaled, cv2.COLOR_BGR2HSV)
            for feature in detected:
                x, y, expected = min(truth, key=lambda t: math.hypot(t[0] - feature["x_px"], t[1] - feature["y_px"]))
                if math.hypot(x - feature["x_px"], y - feature["y_px"]) > 7:
                    errors.append(f"{name}: deteccion sin anotar en ({feature['x_px']:.0f}, {feature['y_px']:.0f})")
                    continue
                for factor in (0.8, 1.0, 1.25):
                    total += 1
                    result = classify_feature_type(
                        gray, hsv, feature["x_px"] * scale, feature["y_px"] * scale, feature["radius_px"] * scale * factor
                    )
                    observed = result and result["observed_type"]
                    if observed != expected:
                        errors.append(f"{name} x{scale} r*{factor} ({x},{y}): esperado {expected}, observado {observed}")
    assert not errors, f"{len(errors)} errores de {total}:\n" + "\n".join(errors[:15])
    print(f"clasificador: {total} comprobaciones OK")


def test_reference_counts() -> None:
    expected = {
        "barrenos.png": {I: 3, B: 6},          # 3 insertos + 5 barrenos + hueco grande
        "panel1.png": {C: 18, I: 16},
        "delrons planos.png": {P: 10, C: 11},
    }
    for name, counts in expected.items():
        features = analyze_reference(load(name))["features"]
        assert dict(collections.Counter(f["type"] for f in features)) == counts, name
    print("pieza patron: conteos por tipo OK")


def test_large_hole() -> None:
    frame = load("barrenos.png")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    assert classify_large_hole(gray, hsv, 683, 395, 85) is not None, "no reconoce el hueco grande"
    assert classify_large_hole(gray, hsv, 400, 500, 85) is None, "acepto metal liso como hueco"
    features = analyze_reference(frame)["features"]
    big = [f for f in features if f["type"] == B and f["radius"] > 50]
    assert len(big) == 1 and abs(big[0]["radius"] - 87) < 5, "el hueco grande no se detecta en la pieza patron"
    print("hueco grande: OK")


if __name__ == "__main__":
    test_classifier()
    test_reference_counts()
    test_large_hole()
    print("TODO OK")
