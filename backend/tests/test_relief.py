"""Valida la medicion de relieve contra escenas de profundidad sinteticas con verdad conocida.

Uso (desde backend/):  python tests/test_relief.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from modules.inspect.golden import analyze_reference  # noqa: E402
from modules.reference.relief import build_relief, contour_to_px, features_to_px  # noqa: E402
from synthetic_scene import make_depth  # noqa: E402

TESTS = BACKEND / "tests"


def load(name: str) -> np.ndarray:
    image = cv2.imread(str(TESTS / name))
    if name == "barrenos.png":
        return image[130:812, 18:1232]
    # panel1 es una foto chica (848 px): se amplia a ~1360 px, como una captura de 1280x720.
    return cv2.resize(image, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)


def scene(name: str, **kwargs):
    image = load(name)
    analysis = analyze_reference(image)  # escala relativa: unidades = pixeles
    features = features_to_px(analysis["features"], analysis["scale"])
    contour = contour_to_px(analysis["contour"], analysis["scale"])
    depth, intrinsics, truth = make_depth(image.shape[:2], contour, features, **kwargs)
    return depth, intrinsics, truth, contour, features


def check(name: str, label: str, **kwargs) -> dict:
    depth, intrinsics, truth, contour, features = scene(name, **kwargs)
    relief = build_relief(depth, intrinsics, contour, features, noise_mm=kwargs.get("noise_mm", 0.25))
    assert relief["available"], relief
    m = relief["measures"]
    print(f"[{label}] escala {m['scale_mm_per_px']:.4f} (real {truth['scale_mm_per_px']:.4f}) | "
          f"dist {m['distance_mm']} | incl {m['tilt_deg']}° (real {truth['tilt_deg']}) | "
          f"espesor {m['thickness_mm']} [{m['thickness_source']}] (real {truth['thickness_mm']}) | "
          f"planitud {m['flatness_mm']} | rms {m['rms_mm']} | validos {m['valid_fraction']} | {m['quality']}")
    assert abs(m["scale_mm_per_px"] / truth["scale_mm_per_px"] - 1) < 0.01, "escala fuera de 1 %"
    assert abs(m["distance_mm"] - truth["distance_mm"]) < 5, "distancia"
    assert abs(m["tilt_deg"] - truth["tilt_deg"]) < 0.4, "inclinacion"
    return {"relief": relief, "truth": truth, "features": features}


def test_scenes() -> None:
    # Pieza con hueco grande: el espesor sale del fondo visto por el hueco.
    result = check("barrenos.png", "barrenos")
    m = result["relief"]["measures"]
    assert m["thickness_mm"] is not None and abs(m["thickness_mm"] - result["truth"]["thickness_mm"]) < 0.6, m
    assert m["thickness_source"] in ("hueco", "hueco+mesa"), m["thickness_source"]
    assert 0.5 < m["flatness_mm"] < 2.0, "planitud: el abombamiento inyectado es de 0.8 mm"

    # Pieza con delrones: alturas de cabeza y espesor por la mesa que rodea la placa.
    result = check("panel1.png", "panel1")
    m = result["relief"]["measures"]
    assert m["thickness_mm"] is not None and abs(m["thickness_mm"] - result["truth"]["thickness_mm"]) < 0.6, m
    errors = []
    for fid, expected in result["truth"]["heights"].items():
        got = result["relief"]["feature_heights"].get(fid)
        if got is None:
            errors.append((fid, expected, None))
        elif abs(got["height_mm"] - expected) > (1.0 if expected > 2 else 0.6):
            errors.append((fid, expected, got["height_mm"]))
    total = len(result["truth"]["heights"])
    print(f"alturas de feature: {total - len(errors)}/{total} dentro de tolerancia (1.0 mm conos, 0.6 mm resto)")
    assert len(errors) <= 0.1 * total, f"alturas fuera de tolerancia: {errors}"


def test_robustness() -> None:
    # Ruido alto, mas perdidas y mas inclinacion: debe seguir midiendo y avisar de la inclinacion.
    result = check("barrenos.png", "ruidoso", noise_mm=0.6, dropout=0.15, tilt_deg=4.5, seed=5)
    assert any("inclinada" in w for w in result["relief"]["measures"]["warnings"]), "debe avisar la inclinacion"
    # Sin profundidad util: no inventa nada.
    depth, intrinsics, truth, contour, features = scene("barrenos.png")
    depth[:] = np.nan
    assert not build_relief(depth, intrinsics, contour, features)["available"]
    print("robustez: OK")


if __name__ == "__main__":
    test_scenes()
    test_robustness()
    print("TODO OK")
