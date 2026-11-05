"""Inspeccion parcial: la pieza patron se da de alta con un recorte y se inspecciona la foto completa.

Uso (desde backend/):  python tests/test_partial_inspection.py
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

# Zona a inspeccionar (x, y, ancho, alto) sobre el cuadro de camara de barrenos.png (1214x682).
CROP = (120, 40, 700, 620)  # mitad izquierda: 2 insertos + 4 barrenos + el hueco grande
CROP_SPARSE = (120, 40, 900, 300)  # franja superior: solo 3 insertos (+ nada alineable mas)


def frame() -> np.ndarray:
    return cv2.imread(str(BACKEND / "tests" / "barrenos.png"))[130:812, 18:1232]


def golden_from_crop():
    x, y, w, h = CROP
    analysis = analyze_reference(frame()[y:y + h, x:x + w])
    analysis["enabled"] = {**analysis["enabled"], "contour": False, "label": False}  # como hace /analyze
    items = [
        {"id": f["id"], "type": f["type"], "x": f["x"], "y": f["y"], "radius": f["radius"], "material": f["material"],
         "radius_reliable": f["radius_reliable"]}
        for f in analysis["features"]
    ]
    golden = {k: analysis[k] for k in ("contour", "label", "dimensions", "enabled")}
    b = analysis["bounds"]
    return items, (b["xmin"], b["xmax"], b["ymin"], b["ymax"]), golden


def inspect(image, items, bounds, golden):
    return run_pipeline(image, items, bounds, {"check_edges": False}, None, golden)


def test_partial() -> None:
    items, bounds, golden = golden_from_crop()
    assert 5 <= len(items) < 9, f"el recorte debe quedarse con una parte de los features ({len(items)})"
    assert any(i["type"] == "BARRENO" and i["radius"] > 50 for i in items), "el hueco grande entra en la zona"
    image = frame()

    run = inspect(image, items, bounds, golden)
    kinds = [r for r in run["results"] if r.get("kind") is None]
    assert len(kinds) == len(items)
    assert run["status"] == "OK", [(r["id"], r["status"], r.get("reasons")) for r in run["results"] if r["status"] != "OK"]
    assert not any(r.get("kind") in ("contour", "label") for r in run["results"]), "contorno/etiqueta apagados"
    print(f"foto completa vs patron recortado ({len(items)} features): {run['status']}, alineacion {run['alignment']['method']}")

    # La pieza colocada distinta (girada 5 grados, mas lejos y movida): debe registrar y seguir en OK.
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 5, 0.93)
    matrix[0, 2] += 22
    matrix[1, 2] -= 14
    moved = cv2.warpAffine(image, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    run = inspect(moved, items, bounds, golden)
    assert run["status"] == "OK", [(r["id"], r["status"], r.get("reasons")) for r in run["results"] if r["status"] != "OK"]
    print(f"pieza girada/movida: {run['status']}")

    # Una falla dentro de la zona (barreno convertido en delron) se detecta ...
    broken = image.copy()
    target = next(i for i in items if i["type"] == "BARRENO" and i["radius"] < 50)  # un barreno chico, no el hueco grande
    px = int(target["x"]) + CROP[0]
    py = int(-target["y"]) + CROP[1]
    cv2.circle(broken, (px, py), 17, (150, 175, 195), -1)
    cv2.circle(broken, (px, py), 6, (30, 25, 25), -1)
    run = inspect(broken, items, bounds, golden)
    failed = [r["id"] for r in run["results"] if r["status"] == "FALLO"]
    assert target["id"] in failed, f"debe fallar el feature {target['id']}: {failed}"
    print(f"falla dentro de la zona detectada en el feature {target['id']}")

    # ... y una fuera de la zona no importa: no esta en la pieza patron.
    outside = image.copy()
    cv2.circle(outside, (1150, 640), 20, (30, 25, 25), -1)
    assert inspect(outside, items, bounds, golden)["status"] == "OK"
    print("lo que hay fuera de la zona recortada se ignora")


def test_sparse_crop_is_rejected() -> None:
    """Con menos de 4 features pequenos no hay forma de alinear: se avisa al dar de alta, no al inspeccionar."""
    x, y, w, h = CROP_SPARSE
    try:
        analyze_reference(frame()[y:y + h, x:x + w])
    except ValueError as error:
        assert "4 features" in str(error) or "pequeños" in str(error), error
    else:
        raise AssertionError("un recorte con menos de 4 features alineables debe rechazarse")
    print("recorte insuficiente: rechazado al analizar con mensaje claro")


def test_other_piece_is_rejected() -> None:
    """Un patron parcial no debe aceptar cualquier pieza: otra pieza distinta es no concluyente o FALLO."""
    items, bounds, golden = golden_from_crop()
    other = cv2.imread(str(BACKEND / "tests" / "panel1.png"))
    try:
        run = inspect(other, items, bounds, golden)
        assert run["status"] != "OK"
    except InconclusiveError:
        pass
    print("otra pieza distinta: rechazada")


if __name__ == "__main__":
    test_partial()
    test_sparse_crop_is_rejected()
    test_other_piece_is_rejected()
    print("TODO OK")
