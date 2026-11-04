"""Escena de profundidad sintetica con verdad conocida (sin camara).

Dada una foto de una pieza analizada (contorno y features en pixeles) construye el mapa de
profundidad que veria una RealSense: placa inclinada a una distancia, abombamiento suave,
mesa detras (espesor), cabezas de delron con altura, huecos pasantes, ruido del estereo,
pixeles perdidos y bordes ruidosos.
"""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

HEAD_HEIGHT_MM = {"INSERTO": 0.1, "DELRON_CONICO": 3.0, "DELRON_PLANO": 1.2}


def make_depth(
    shape: tuple[int, int],
    contour_px: np.ndarray,
    features: list[dict[str, Any]],
    *,
    distance_mm: float = 450.0,
    tilt_deg: float = 2.0,
    thickness_mm: float = 2.4,
    noise_mm: float = 0.25,
    warp_mm: float = 0.8,
    dropout: float = 0.03,
    seed: int = 1,
) -> tuple[np.ndarray, dict[str, float], dict[str, Any]]:
    """Devuelve (profundidad mm con NaN, intrinsecos, verdad)."""
    height, width = shape
    rng = np.random.default_rng(seed)
    fx = fy = 0.72 * width
    cx, cy = width / 2.0, height / 2.0
    intrinsics = {"fx": fx, "fy": fy, "ppx": cx, "ppy": cy, "width": width, "height": height}

    phi = math.radians(35.0)
    tilt = math.radians(tilt_deg)
    normal = np.array([math.sin(tilt) * math.cos(phi), math.sin(tilt) * math.sin(phi), math.cos(tilt)])
    d_panel = normal[2] * distance_mm

    u, v = np.meshgrid(np.arange(width, dtype=np.float64), np.arange(height, dtype=np.float64))
    ray_u, ray_v = (u - cx) / fx, (v - cy) / fy
    denominator = normal[0] * ray_u + normal[1] * ray_v + normal[2]

    # Altura (hacia la camara) de la superficie visible sobre el plano de la placa.
    rise = np.zeros((height, width))
    panel = np.zeros((height, width), np.uint8)
    cv2.fillPoly(panel, [np.round(contour_px).astype(np.int32)], 1)
    ys, xs = np.nonzero(panel)
    centre_u, centre_v = xs.mean(), ys.mean()
    sigma = 0.25 * max(xs.max() - xs.min(), ys.max() - ys.min())
    rise += warp_mm * np.exp(-(((u - centre_u) ** 2 + (v - centre_v) ** 2) / (2 * sigma**2)))

    on_table = panel == 0  # fuera de la placa se ve la mesa
    truth_heights: dict[str, float] = {}
    for feature in features:
        radius, bore = float(feature["radius_px"]), float(feature.get("bore_px", 0.3 * feature["radius_px"]))
        r = np.hypot(u - feature["x_px"], v - feature["y_px"])
        if feature["type"] == "BARRENO":
            on_table |= r <= radius  # hueco pasante
            continue
        on_table |= r <= bore  # barreno central
        head = HEAD_HEIGHT_MM[feature["type"]]
        annulus = (r > bore) & (r <= radius)
        if feature["type"] == "DELRON_CONICO":
            profile = head * (1.0 - 0.65 * np.clip((r - bore) / max(radius - bore, 1e-6), 0, 1))
        else:
            profile = np.full_like(r, head)
        rise = np.where(annulus, rise + profile, rise)
        truth_heights[str(feature["id"])] = head

    surface = np.where(on_table, -thickness_mm, rise)  # mm sobre el plano de la placa (+ = hacia la camara)
    depth = (d_panel - surface) / denominator
    depth += rng.normal(0.0, noise_mm, depth.shape)

    # Perdidas aleatorias y bordes (pixeles volantes) alrededor de huecos y del contorno.
    invalid = rng.random(depth.shape) < dropout
    edges = cv2.Canny((on_table * 255).astype(np.uint8), 50, 150)
    edge_band = cv2.dilate(edges, np.ones((5, 5), np.uint8)) > 0
    invalid |= edge_band & (rng.random(depth.shape) < 0.6)
    depth[invalid] = np.nan

    truth = {
        "thickness_mm": thickness_mm,
        "tilt_deg": tilt_deg,
        "distance_mm": distance_mm,
        "scale_mm_per_px": distance_mm / fx,
        "heights": truth_heights,
        "warp_mm": warp_mm,
    }
    return depth.astype(np.float32), intrinsics, truth
