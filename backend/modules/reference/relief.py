"""Relieve 3D medido con la profundidad de la RealSense.

Entrada: mapa de profundidad en mm alineado al color (NaN = sin medicion), los
intrinsecos del color y lo que ya extrajo el analisis de la foto (contorno y
features, en pixeles). Salida: escala metrica real, inclinacion, planitud, espesor,
altura de cada feature y una malla de alturas respecto al plano de la placa.

Todo se mide en el espacio metrico de la camara: cada pixel (u, v) con profundidad
Z es el punto (X, Y, Z) = ((u - cx) Z / fx, (v - cy) Z / fy, Z). La placa es el plano
que mejor ajusta los pixeles de su cara (sin features ni huecos); la altura de un
punto es su distancia a ese plano, positiva hacia la camara.
"""
from __future__ import annotations

import base64
import io
import math
import warnings
from typing import Any

import cv2
import numpy as np

QUANT_MM = 0.01  # resolucion de la malla de alturas (int16)
GRID_MAX = 220  # celdas del lado mayor de la malla

TILT_WARN_DEG = 3.0
VALID_WARN = 0.6


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def _fit_plane(points: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    """Plano n.p = d (n unitario, n_z > 0) por minimos cuadrados con rechazo de atipicos."""
    keep = np.ones(len(points), dtype=bool)
    normal, offset = np.array([0.0, 0.0, 1.0]), float(np.median(points[:, 2]))
    for _ in range(6):
        sample = points[keep]
        if len(sample) < 50:
            break
        centroid = sample.mean(axis=0)
        _, _, vt = np.linalg.svd(sample - centroid, full_matrices=False)
        normal = vt[2]
        if normal[2] < 0:
            normal = -normal
        offset = float(normal @ centroid)
        residual = points @ normal - offset
        sigma = 1.4826 * float(np.median(np.abs(residual[keep])))
        keep = np.abs(residual) < max(3.0 * sigma, 0.2)
    return normal, offset, keep


def _robust_median(values: np.ndarray, minimum: int) -> float | None:
    values = values[np.isfinite(values)]
    return float(np.median(values)) if values.size >= minimum else None


def _fill_holes(grid: np.ndarray) -> np.ndarray:
    """Rellena celdas sin dato con el promedio de sus vecinas, avanzando desde los bordes."""
    valid = np.isfinite(grid)
    filled = np.where(valid, grid, 0.0).astype(np.float32)
    weight = valid.astype(np.float32)
    for _ in range(80):
        if valid.all():
            break
        neighbors = cv2.boxFilter(weight, -1, (3, 3), normalize=False)
        total = cv2.boxFilter(filled * weight, -1, (3, 3), normalize=False)
        grow = (~valid) & (neighbors > 0)
        if not grow.any():
            break
        filled[grow] = total[grow] / neighbors[grow]
        weight[grow] = 1.0
        valid = valid | grow
    filled[~valid] = 0.0
    return filled


def build_relief(
    depth_mm: np.ndarray,
    intrinsics: dict[str, float],
    contour_px: np.ndarray,
    features: list[dict[str, Any]],
    noise_mm: float | None = None,
) -> dict[str, Any]:
    """Mide el relieve. `features`: [{id, type, x_px, y_px, radius_px, bore_px}]."""
    height, width = depth_mm.shape
    fx, fy = float(intrinsics["fx"]), float(intrinsics["fy"])
    cx, cy = float(intrinsics["ppx"]), float(intrinsics["ppy"])
    if len(contour_px) < 3:
        return _unavailable("No hay contorno de la pieza.")

    polygon = np.round(contour_px).astype(np.int32)
    panel = np.zeros((height, width), np.uint8)
    cv2.fillPoly(panel, [polygon], 1)
    margin = max(3, int(0.006 * max(width, height)))
    core = cv2.erode(panel, np.ones((2 * margin + 1, 2 * margin + 1), np.uint8)) > 0

    occupied = np.zeros((height, width), np.uint8)  # features y huecos: no son "cara de la placa"
    for feature in features:
        cv2.circle(
            occupied, (int(round(feature["x_px"])), int(round(feature["y_px"]))),
            int(feature["radius_px"] * 1.5) + 2, 1, -1,
        )
    finite = np.isfinite(depth_mm)
    face = core & (occupied == 0) & finite
    if face.sum() < 1500:
        return _unavailable("Muy pocos puntos de profundidad sobre la placa (¿reflejos o fuera de rango?).")

    v_idx, u_idx = np.nonzero(face)
    if len(v_idx) > 60000:
        pick = np.random.default_rng(7).choice(len(v_idx), 60000, replace=False)
        v_idx, u_idx = v_idx[pick], u_idx[pick]
    z = depth_mm[v_idx, u_idx].astype(np.float64)
    points = np.column_stack(((u_idx - cx) * z / fx, (v_idx - cy) * z / fy, z))
    normal, offset, inliers = _fit_plane(points)
    if inliers.sum() < 1000:
        return _unavailable("No fue posible ajustar el plano de la placa.")

    # Altura (mm, hacia la camara) de cada pixel: h = d - n.P = d - Z (n.(u', v', 1)).
    uu = (np.arange(width, dtype=np.float32) - cx) / fx
    vv = (np.arange(height, dtype=np.float32) - cy) / fy
    factor = (normal[0] * uu[None, :] + normal[1] * vv[:, None] + normal[2]).astype(np.float32)
    heights = (offset - factor * depth_mm).astype(np.float32)  # NaN donde no hay profundidad

    distance = float(np.median(z[inliers]))
    scale_mm_per_px = distance / (0.5 * (fx + fy))
    tilt = math.degrees(math.acos(float(np.clip(normal[2], -1.0, 1.0))))
    plane_residual = (points[inliers] @ normal) - offset
    rms = float(np.std(plane_residual))
    valid_fraction = float(np.isfinite(depth_mm[core]).mean())

    # --- malla de alturas -------------------------------------------------
    u0, v0, bw, bh = cv2.boundingRect(polygon)
    u0, v0 = max(u0, 0), max(v0, 0)
    u1, v1 = min(u0 + bw, width), min(v0 + bh, height)
    step = max(1, math.ceil(max(u1 - u0, v1 - v0) / GRID_MAX))
    gw, gh = math.ceil((u1 - u0) / step), math.ceil((v1 - v0) / step)
    eligible = np.where(face, heights, np.nan)[v0:v1, u0:u1]
    padded = np.full((gh * step, gw * step), np.nan, np.float32)
    padded[: eligible.shape[0], : eligible.shape[1]] = eligible
    blocks = padded.reshape(gh, step, gw, step).swapaxes(1, 2).reshape(gh, gw, step * step)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        grid = np.nanmedian(blocks, axis=2)
    coverage = np.isfinite(blocks).mean(axis=2)
    grid[coverage < 0.3] = np.nan

    # Celdas realmente sobre la cara (para medir planitud) frente a las rellenadas.
    inside = np.zeros((gh * step, gw * step), np.uint8)
    shifted = (polygon - np.array([u0, v0])).astype(np.int32)
    cv2.fillPoly(inside, [shifted], 1)
    inside_cells = inside.reshape(gh, step, gw, step).mean(axis=(1, 3)) > 0.9
    measured = np.isfinite(grid) & inside_cells
    flat_values = grid[measured]
    if flat_values.size >= 50:
        flatness = float(np.percentile(flat_values, 98) - np.percentile(flat_values, 2))
        flat_rms = float(np.std(flat_values))
    else:
        flatness = flat_rms = float("nan")

    filled = cv2.GaussianBlur(_fill_holes(grid), (0, 0), 0.8)
    quantized = np.clip(np.round(filled / QUANT_MM), -32000, 32000).astype("<i2")

    # --- espesor: distancia al plano de la mesa visible por los huecos / alrededor ---
    thickness, thickness_source = _thickness(heights, finite, polygon, panel, features, width, height, margin)

    # --- altura de cada feature sobre la placa ---------------------------------
    feature_heights = {}
    for feature in features:
        if feature["type"] == "BARRENO":
            continue
        measured_height = _feature_height(heights, feature)
        if measured_height is not None:
            feature_heights[str(feature["id"])] = {
                "height_mm": round(max(measured_height, 0.0), 2),
                "reliable": bool(abs(measured_height) > 2.0 * (noise_mm or 0.3)),
            }

    warnings_list: list[str] = []
    if tilt > TILT_WARN_DEG:
        warnings_list.append(
            f"La pieza está inclinada {tilt:.1f}° respecto a la cámara; colóquela perpendicular para medir mejor."
        )
    if valid_fraction < VALID_WARN:
        warnings_list.append(
            f"Solo {valid_fraction * 100:.0f}% de la pieza tiene profundidad válida (reflejos o fuera de rango)."
        )
    if thickness is None:
        warnings_list.append("No se pudo medir el espesor; se usa uno estimado (ajústelo en el visor).")

    if valid_fraction >= 0.85 and rms <= 0.8:
        quality = "Buena"
    elif valid_fraction >= VALID_WARN and rms <= 1.5:
        quality = "Regular"
    else:
        quality = "Pobre"

    return {
        "available": True,
        "grid": {
            "width": gw,
            "height": gh,
            "step_px": step,
            "bounds_px": [u0, v0, u0 + gw * step, v0 + gh * step],
            "quant_mm": QUANT_MM,
            "data_b64": base64.b64encode(quantized.tobytes()).decode("ascii"),
        },
        "measures": {
            "scale_mm_per_px": round(scale_mm_per_px, 5),
            "distance_mm": round(distance, 1),
            "tilt_deg": round(tilt, 2),
            "thickness_mm": None if thickness is None else round(thickness, 2),
            "thickness_source": thickness_source,
            "flatness_mm": None if math.isnan(flatness) else round(flatness, 2),
            "rms_mm": None if math.isnan(flat_rms) else round(flat_rms, 3),
            "plane_rms_mm": round(rms, 3),
            "valid_fraction": round(valid_fraction, 3),
            "noise_mm": None if noise_mm is None or math.isnan(noise_mm) else round(float(noise_mm), 3),
            "quality": quality,
            "warnings": warnings_list,
        },
        "feature_heights": feature_heights,
    }


def _feature_height(heights: np.ndarray, feature: dict[str, Any]) -> float | None:
    """Altura de la cabeza del feature sobre la placa a su alrededor (mm)."""
    height, width = heights.shape
    radius = float(feature["radius_px"])
    bore = float(feature.get("bore_px", 0.3 * radius))
    reach = int(radius * 2.3) + 2
    x, y = int(round(feature["x_px"])), int(round(feature["y_px"]))
    x0, y0, x1, y1 = max(0, x - reach), max(0, y - reach), min(width, x + reach + 1), min(height, y + reach + 1)
    window = heights[y0:y1, x0:x1]
    if window.size == 0:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    distance = np.hypot(xx - feature["x_px"], yy - feature["y_px"])
    crown = window[(distance >= max(bore * 1.05, bore + 0.8)) & (distance <= 1.02 * radius)]
    ring = window[(distance >= 1.5 * radius) & (distance <= 2.4 * radius)]
    base = _robust_median(ring, 10)
    crown = crown[np.isfinite(crown)]
    if base is None or crown.size < 5:
        return None
    # Cono: la cabeza es el punto alto junto al barreno; discos: el nivel medio de la corona.
    top = float(np.percentile(crown, 95)) if feature["type"] == "DELRON_CONICO" else float(np.median(crown))
    return top - base


def _thickness(
    heights: np.ndarray, finite: np.ndarray, polygon: np.ndarray, panel: np.ndarray,
    features: list[dict[str, Any]], width: int, height: int, margin: int,
) -> tuple[float | None, str | None]:
    """Espesor = cuanto mas lejos esta la mesa que la cara de la placa (pieza apoyada en la mesa)."""
    candidates: list[tuple[float, str]] = []

    big = [f for f in features if f["type"] == "BARRENO" and f["radius_px"] > 0.025 * max(width, height)]
    for hole in big:
        probe = np.zeros((height, width), np.uint8)
        cv2.circle(probe, (int(round(hole["x_px"])), int(round(hole["y_px"]))), int(hole["radius_px"] * 0.7), 1, -1)
        values = heights[(probe > 0) & finite]
        median = _robust_median(values, 300)
        if median is not None and median < 0:
            candidates.append((-median, "hueco"))
            break

    ring_outer = cv2.dilate(panel, np.ones((2 * (margin + 28) + 1,) * 2, np.uint8))
    ring_inner = cv2.dilate(panel, np.ones((2 * (margin + 6) + 1,) * 2, np.uint8))
    ring = (ring_outer > 0) & (ring_inner == 0) & finite
    median = _robust_median(heights[ring], 500)
    if median is not None and median < 0:
        candidates.append((-median, "mesa"))

    if not candidates:
        return None, None
    value, source = candidates[0]
    if len(candidates) == 2 and abs(candidates[0][0] - candidates[1][0]) > max(0.8, 0.5 * value):
        return None, None  # las dos mediciones no concuerdan: no es confiable
    if len(candidates) == 2:
        value = 0.5 * (candidates[0][0] + candidates[1][0])
        source = "hueco+mesa"
    if not 0.3 <= value <= 30.0:
        return None, None
    return value, source


def to_model(relief: dict[str, Any], scale: float) -> dict[str, Any]:
    """Anade los limites de la malla en unidades del modelo (x = u*scale, y = -v*scale)."""
    if not relief.get("available"):
        return relief
    u0, v0, u1, v1 = relief["grid"]["bounds_px"]
    out = {**relief, "grid": {**relief["grid"], "bounds": {
        "x0": u0 * scale, "x1": u1 * scale, "y0": -v1 * scale, "y1": -v0 * scale,
    }}}
    return out


def pack_depth(depth_mm: np.ndarray) -> bytes:
    """Profundidad cruda comprimida (uint16 en decimas de mm; 0 = sin dato) para reprocesar."""
    raw = np.where(np.isfinite(depth_mm), np.clip(np.round(depth_mm * 10.0), 1, 65535), 0).astype(np.uint16)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, depth=raw)
    return buffer.getvalue()


def unpack_depth(data: bytes) -> np.ndarray:
    raw = np.load(io.BytesIO(data))["depth"].astype(np.float32)
    raw[raw == 0] = np.nan
    return raw / 10.0


def contour_to_px(contour: list[list[float]], scale: float) -> np.ndarray:
    """Contorno del analisis (unidades del modelo, Y arriba) a pixeles de imagen."""
    return np.array([[x / scale, -y / scale] for x, y in contour], dtype=np.float64)


def features_to_px(features: list[dict[str, Any]], scale: float) -> list[dict[str, Any]]:
    """Features del analisis (unidades del modelo) a pixeles de imagen; solo los activos."""
    return [
        {
            "id": item["id"],
            "type": item["type"],
            "x_px": item["x"] / scale,
            "y_px": -item["y"] / scale,
            "radius_px": item.get("outer_radius", item["radius"]) / scale,
            "bore_px": item.get("bore_radius", 0.3 * item["radius"]) / scale,
        }
        for item in features
        if item.get("enabled", True)
    ]
