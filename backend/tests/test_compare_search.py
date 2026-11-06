"""Busqueda del patron en la foto cuando la pieza es lisa (pocos puntos propios) y el fondo es rugoso.

Uso (desde backend/):  python tests/test_compare_search.py

Reproduce un caso real: un conector de brida lisa sobre grava; el patron es un recorte del conector y en la
inspeccion el conector esta en otro lugar (y girado). Los puntos SIFT se enganchaban en la grava; ahora se
verifica contra el patron y se busca por plantilla.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from modules.compare.engine import CompareInconclusive, compare  # noqa: E402

RNG = np.random.default_rng(2)


def connector(size: int = 490) -> np.ndarray:
    image = np.full((size, size, 3), (235, 235, 238), np.uint8)
    cv2.rectangle(image, (8, 8), (size - 8, size - 8), (215, 215, 220), 6)
    for cx, cy in ((50, 50), (size - 50, 50), (50, size - 50), (size - 50, size - 50)):
        cv2.ellipse(image, (cx, cy), (28, 16), 40, 0, 360, (120, 120, 125), -1)
    cv2.circle(image, (size // 2, size // 2), 190, (40, 110, 230), -1)
    cv2.circle(image, (size // 2, size // 2), 62, (30, 30, 30), -1)
    for k in range(8):
        a = k * np.pi / 4
        cv2.circle(image, (int(size / 2 + 125 * np.cos(a)), int(size / 2 + 125 * np.sin(a))), 40, (25, 25, 30), -1)
    return cv2.GaussianBlur(image, (0, 0), 1.0)


def scene(obj: np.ndarray, center: tuple[int, int], angle: float = 0.0, w: int = 1920, h: int = 1080) -> np.ndarray:
    ground = cv2.GaussianBlur(np.clip(RNG.normal(128, 38, (h, w)), 0, 255).astype(np.float32), (0, 0), 2.0) * 1.8 - 110
    image = np.dstack([np.clip(ground, 40, 200)] * 3).astype(np.uint8)
    big = int(max(obj.shape) * 1.5)
    canvas, mask = np.zeros((big, big, 3), np.uint8), np.zeros((big, big), np.uint8)
    off = (big - obj.shape[0]) // 2
    canvas[off:off + obj.shape[0], off:off + obj.shape[1]] = obj
    mask[off:off + obj.shape[0], off:off + obj.shape[1]] = 255
    matrix = cv2.getRotationMatrix2D((big / 2, big / 2), angle, 1)
    canvas, mask = cv2.warpAffine(canvas, matrix, (big, big)), cv2.warpAffine(mask, matrix, (big, big))
    x, y = center[0] - big // 2, center[1] - big // 2
    roi = image[y:y + big, x:x + big]
    roi[mask > 0] = canvas[mask > 0]
    return cv2.GaussianBlur(image, (0, 0), 0.8)


def test_smooth_part_moved() -> None:
    obj = connector()
    golden = scene(obj, (480, 400))[155:645, 235:725]  # el patron: recorte del conector
    for name, shot, center, rotation in (
        ("mismo lugar", scene(obj, (480, 400)), (480, 400), 0),
        ("movido a la derecha", scene(obj, (1300, 520)), (1300, 520), 0),
        ("movido y girado 35", scene(obj, (1250, 600), 35), (1250, 600), 35),
    ):
        report = compare(golden, shot, "estricto")["report"]
        found = np.array(report["located"]["polygon"]).mean(axis=0)
        assert np.hypot(*(found - center)) < 12, (name, found, center)
        assert abs(abs(report["alignment"]["rotation_deg"]) - rotation) < 4, (name, report["alignment"])
        assert report["metrics"]["similarity"] > 0.9, (name, report["metrics"]["similarity"])
        if rotation == 0:
            assert report["status"] == "OK", (name, report["summary"])
        print(f"{name}: ubicado en {found.round()} OK")
    # Un defecto real tras moverlo: se encuentra y se reporta.
    moved = scene(obj, (1300, 520))
    cv2.circle(moved, (1300, 430), 14, (20, 20, 20), -1)
    report = compare(golden, moved, "estricto")["report"]
    assert report["regions"], "no detecto el defecto en la pieza movida"


def test_not_in_photo() -> None:
    golden = scene(connector(), (480, 400))[155:645, 235:725]
    nothing = scene(connector(), (-3000, -3000)) if False else np.clip(RNG.normal(120, 40, (1080, 1920, 3)), 0, 255).astype(np.uint8)
    try:
        compare(golden, nothing, "estricto")
    except CompareInconclusive:
        print("foto sin la pieza: NO CONCLUYENTE")
        return
    raise AssertionError("una foto sin la pieza debe ser no concluyente")


if __name__ == "__main__":
    test_smooth_part_moved()
    test_not_in_photo()
    print("TODO OK")
