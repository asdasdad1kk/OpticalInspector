"""Cada panel de prueba, dado de alta y luego inspeccionado, debe salir OK (tambien movido).

Uso (desde backend/):  python tests/test_self_inspection.py

Cubre el flujo completo: analisis de la pieza patron (features, tipos, contorno, etiqueta),
alineamiento y verificacion de cada feature, incluida la metrica de distancias.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from modules.inspect.golden import analyze_reference  # noqa: E402
from modules.inspect.pipeline import run_pipeline  # noqa: E402

TESTS = BACKEND / "tests"


def load(name: str) -> np.ndarray:
    image = cv2.imread(str(TESTS / name))
    assert image is not None, name
    return image[130:812, 18:1232] if name == "barrenos.png" else image


# nombre: (grados, escala, dx, dy) de la variante "movida" (la pieza colocada algo distinto).
# delrons planos ya llena la foto: solo se achica un poco para que siga cabiendo.
MOVED = {
    "panel1.png": (4, 0.95, 15, -10),
    "delrons planos.png": (1.5, 0.93, 12, 10),
    "barrenos.png": (4, 0.93, 15, -10),
    "panel_curvo.png": (4, 0.93, 15, -10),
}


def inspect(name: str, variant: str):
    image = load(name)
    reference = analyze_reference(image)
    items = [
        {"id": f["id"], "type": f["type"], "x": f["x"], "y": f["y"], "radius": f["radius"],
         "material": f["material"], "radius_reliable": f["radius_reliable"]}
        for f in reference["features"]
    ]
    golden = {k: reference[k] for k in ("contour", "label", "dimensions", "enabled")}
    if name == "delrons planos.png" and variant == "movida":
        # La foto achicada rellena sus bordes replicando pixeles: la silueta medida no es confiable.
        golden["enabled"] = {**golden["enabled"], "contour": False}
    b = reference["bounds"]
    shot = image
    if variant == "movida":
        angle, scale, dx, dy = MOVED[name]
        h, w = image.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
        matrix[0, 2] += dx
        matrix[1, 2] += dy
        shot = cv2.warpAffine(image, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    run = run_pipeline(shot, items, (b["xmin"], b["xmax"], b["ymin"], b["ymax"]), {"check_edges": False}, None, golden)
    return run, items


def test_all() -> None:
    for name in MOVED:
        for variant in ("misma", "movida"):
            run, items = inspect(name, variant)
            problems = [(r["id"], r.get("observed_type"), r["status"], r.get("reasons")) for r in run["results"] if r["status"] != "OK"]
            assert run["status"] == "OK", f"{name} {variant}: {problems[:3]}"
            features = [r for r in run["results"] if r.get("kind") is None]
            assert len(features) == len(items)
            wrong = [r["id"] for r in features if r.get("observed_type") and r["observed_type"] != r["type"]]
            assert not wrong, f"{name} {variant}: tipo observado distinto en {wrong}"
            canvas = run["canvas"]
            assert max(canvas["width"], canvas["height"]) <= 1800, "el lienzo no debe pasar del lado mayor fijado"
            print(f"{name:20} {variant:7} OK  {len(items):2} features, alineacion {run['alignment']['method']}, "
                  f"lienzo {canvas['width']}x{canvas['height']}")


if __name__ == "__main__":
    test_all()
    print("TODO OK")
