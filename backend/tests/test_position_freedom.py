"""La pieza puede ir en cualquier posicion/giro de la mesa: nunca NO CONCLUYENTE por eso.

Uso (desde backend/):  python tests/test_position_freedom.py

Se da de alta cada foto y se inspecciona girada (hasta 180 grados) y desplazada con la pieza completa dentro
del encuadre: debe salir OK. Una pieza cortada por el borde de la foto si debe ser NO CONCLUYENTE.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from modules.inspect.golden import analyze_reference  # noqa: E402
from modules.inspect.pipeline import InconclusiveError, run_pipeline  # noqa: E402

TESTS = BACKEND / "tests"
BRIO = sorted((TESTS / "panel_curvo_completo").glob("*.jpg"))[0]


def shot(image: np.ndarray, angle: float, scale: float, dx: float, dy: float, border: int = cv2.BORDER_REPLICATE) -> np.ndarray:
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    matrix[0, 2] += dx
    matrix[1, 2] += dy
    return cv2.warpAffine(image, matrix, (w, h), borderMode=border, borderValue=(90, 90, 90))


def inspect(image: np.ndarray, moved: np.ndarray):
    reference = analyze_reference(image)
    items = [{k: f[k] for k in ("id", "type", "x", "y", "radius", "material", "radius_reliable")} for f in reference["features"]]
    golden = {k: reference[k] for k in ("contour", "label", "dimensions", "enabled")}
    golden["enabled"] = {**golden["enabled"], "contour": False}  # el borde replicado de la foto movida no es la silueta real
    b = reference["bounds"]
    return run_pipeline(moved, items, (b["xmin"], b["xmax"], b["ymin"], b["ymax"]), {"check_edges": False}, None, golden)


def test_rotations() -> None:
    image = cv2.imread(str(BRIO))
    for angle, scale, dx, dy in ((0, 0.85, 120, 100), (25, 0.8, 60, 0), (-30, 0.75, 0, 60), (90, 0.62, 0, 0), (180, 0.95, 0, 0)):
        run = inspect(image, shot(image, angle, scale, dx, dy))
        assert run["status"] == "OK", f"giro {angle}: {run['status']}"
        print(f"BRIO giro {angle:4} escala {scale}: OK")


def test_cut_piece_is_inconclusive() -> None:
    image = cv2.imread(str(BRIO))
    try:
        inspect(image, shot(image, 0, 1.0, 400, 0, cv2.BORDER_CONSTANT))
    except InconclusiveError as error:
        assert "encuadre" in str(error) or "alinear" in str(error), error
        print("pieza cortada por el encuadre: NO CONCLUYENTE")
        return
    raise AssertionError("una pieza cortada por el borde debe ser no concluyente")


if __name__ == "__main__":
    test_rotations()
    test_cut_piece_is_inconclusive()
    print("TODO OK")
