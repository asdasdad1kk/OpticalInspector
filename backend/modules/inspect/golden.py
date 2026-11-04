"""Pieza patron real (golden sample).

analyze_reference: extrae de una captura todos los features medibles
(insertos/agujeros, contorno, etiqueta, dimensiones) en unidades reales.
El origen es arbitrario: la comparacion se registra por la geometria de los
propios features, asi que no depende de un cero bien definido.

run_extra_checks: verificaciones opcionales (contorno, etiqueta, distancias)
que se ejecutan sobre la imagen rectificada durante la inspeccion.
"""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

from .pipeline import classify_color, measure_radius
from .registration import detect_blob_candidates

FEATURE_CATEGORIES = ("features", "contour", "distances", "label")

DEFAULT_ENABLED = {key: True for key in FEATURE_CATEGORIES}


# ============================================================
# DETECCION DE FEATURES
# ============================================================

def _blob_shape(gray: np.ndarray, candidate: dict[str, float]) -> dict[str, float] | None:
    """Forma del componente oscuro que rodea al pico (descarta texto/rayones)."""
    sigma = candidate["sigma"]
    radius = int(max(8, sigma * 4))
    cx, cy = int(candidate["x"]), int(candidate["y"])
    x1, y1 = max(0, cx - radius), max(0, cy - radius)
    window = gray[y1:cy + radius + 1, x1:cx + radius + 1]
    if window.shape[0] < 6 or window.shape[1] < 6:
        return None

    blurred = cv2.GaussianBlur(window, (0, 0), 1.0)
    threshold, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    dark = (blurred < threshold).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(dark)
    px = min(cx - x1, labels.shape[1] - 1)
    py = min(cy - y1, labels.shape[0] - 1)
    label = labels[py, px]
    if label == 0:
        near = [(math.hypot(centroids[i][0] - px, centroids[i][1] - py), i) for i in range(1, count)]
        if not near:
            return None
        label = min(near)[1]

    left, top, width, height, area = stats[label]
    touches = (
        left == 0 or top == 0
        or left + width >= window.shape[1] or top + height >= window.shape[0]
    )
    contours, _ = cv2.findContours(
        (labels == label).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(contour, True)
    contour_area = cv2.contourArea(contour)
    (_, _), (rect_w, rect_h), _ = cv2.minAreaRect(contour)
    return {
        "area": float(area),
        "circularity": float(4 * math.pi * contour_area / (perimeter ** 2 + 1e-9)),
        "aspect": float(min(rect_w, rect_h) / max(rect_w, rect_h, 1e-9)),
        "touches": float(touches),
    }


def detect_features(image: np.ndarray) -> list[dict[str, Any]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    min_area = max(30.0, 0.00006 * image.shape[0] * image.shape[1])
    features: list[dict[str, Any]] = []

    for candidate in detect_blob_candidates(image, max_candidates=150):
        shape = _blob_shape(gray, candidate)
        if shape is None or shape["touches"] or shape["area"] < min_area:
            continue
        if shape["circularity"] < 0.55 or shape["aspect"] < 0.70:
            continue

        radius = max(4.0, candidate["sigma"] * 1.8)
        consistency = 0.0
        # Punto fijo: se mide con el radio esperado = radio medido, igual que en inspeccion.
        for _ in range(3):
            measured, consistency = measure_radius(gray, candidate["x"], candidate["y"], radius)
            if measured is None:
                break
            converged = abs(measured - radius) < 0.25
            radius = measured
            if converged:
                break
        color = classify_color(hsv, candidate["x"], candidate["y"], radius, 60)
        features.append({
            "x_px": candidate["x"],
            "y_px": candidate["y"],
            "radius_px": float(radius),
            "response": candidate["response"],
            "material": color["observed"],
            "saturation": color["saturation"],
            "circularity": round(shape["circularity"], 2),
            "radius_reliable": bool(consistency >= 0.5),
        })

    # Radios no fiables (sombra, bajo contraste) heredan el radio tipico del mismo material.
    for item in features:
        if item["radius_reliable"]:
            continue
        peers = [f["radius_px"] for f in features if f["radius_reliable"] and f["material"] == item["material"]]
        peers = peers or [f["radius_px"] for f in features if f["radius_reliable"]]
        if peers:
            item["radius_px"] = float(np.median(peers))

    # Supresion de duplicados cercanos.
    features.sort(key=lambda item: item["response"], reverse=True)
    kept: list[dict[str, Any]] = []
    for item in features:
        if all(math.hypot(item["x_px"] - other["x_px"], item["y_px"] - other["y_px"])
               > 0.8 * max(item["radius_px"], other["radius_px"]) + 6 for other in kept):
            kept.append(item)
    return kept


# ============================================================
# CONTORNO
# ============================================================

def segment_panel(
    image: np.ndarray,
    seed_hull: np.ndarray | None,
    expected_polygon: np.ndarray | None = None,
) -> np.ndarray | None:
    """GrabCut sembrado. Devuelve el contorno exterior mayor (Nx2 float32)."""
    height, width = image.shape[:2]
    scale = min(1.0, 640.0 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]

    mask = np.full((sh, sw), cv2.GC_PR_BGD, np.uint8)
    band = max(2, int(0.03 * sw))
    mask[:band, :] = mask[-band:, :] = mask[:, :band] = mask[:, -band:] = cv2.GC_BGD

    grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * max(2, int(0.05 * sw)) + 1,) * 2)
    seed = np.zeros((sh, sw), np.uint8)
    if expected_polygon is not None:
        poly = (expected_polygon * scale).astype(np.int32)
        cv2.fillPoly(seed, [poly], 255)
        inner = cv2.erode(seed, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * max(2, int(0.03 * sw)) + 1,) * 2))
        outer = cv2.dilate(seed, grow)
        mask[outer == 0] = cv2.GC_BGD
        mask[(outer > 0) & (mask != cv2.GC_BGD)] = cv2.GC_PR_FGD
        mask[inner > 0] = cv2.GC_FGD
    elif seed_hull is not None and len(seed_hull) >= 3:
        points = seed_hull * scale
        # Casco de los puntos "centrales": un feature aislado (polvo fuera de la
        # pieza) solo queda como probable, no como primer plano seguro.
        center = np.median(points, axis=0)
        hull = cv2.convexHull(points.astype(np.int32))
        cv2.fillConvexPoly(seed, hull, 255)
        outer = cv2.dilate(seed, grow)
        shrunk = (center + (hull.reshape(-1, 2) - center) * 0.80).astype(np.int32)
        sure = np.zeros((sh, sw), np.uint8)
        cv2.fillConvexPoly(sure, shrunk, 255)
        mask[(outer > 0) & (mask != cv2.GC_BGD)] = cv2.GC_PR_FGD
        mask[sure > 0] = cv2.GC_FGD
    else:
        return None

    background = np.zeros((1, 65))
    foreground = np.zeros((1, 65))
    try:
        cv2.grabCut(small, mask, None, background, foreground, 5, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None

    segmented = ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)).astype(np.uint8) * 255
    segmented = cv2.morphologyEx(segmented, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(segmented, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    return (largest / scale).reshape(-1, 2).astype(np.float32)


def detect_label(image: np.ndarray, contour: np.ndarray) -> dict[str, float] | None:
    """Etiqueta azulada/clara de identificacion dentro del panel."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (90, 35, 60), (130, 255, 255))
    inside = np.zeros(mask.shape, np.uint8)
    cv2.fillPoly(inside, [contour.astype(np.int32)], 255)
    mask = cv2.bitwise_and(mask, inside)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    best = max(contours, key=cv2.contourArea)
    if cv2.contourArea(best) < 0.004 * cv2.contourArea(contour.astype(np.int32)):
        return None
    (cx, cy), (w, h), angle = cv2.minAreaRect(best)
    return {"x_px": float(cx), "y_px": float(cy), "w_px": float(max(w, h)), "h_px": float(min(w, h)), "angle": float(angle)}


# ============================================================
# ANALISIS COMPLETO
# ============================================================

def analyze_reference(
    image: np.ndarray,
    known_length: float | None = None,
    known_axis: str = "long",
    plane_mm_per_px: float | None = None,
) -> dict[str, Any]:
    """Devuelve la referencia completa en unidades reales (mm) y un preview.

    known_length: dimension real (mm) del lado largo (o corto con
    known_axis="short") del contorno de la pieza. Sin ella las unidades
    quedan en pixeles (modo relativo).
    """
    height, width = image.shape[:2]
    raw_features = detect_features(image)
    if len(raw_features) < 4:
        raise ValueError(
            "Se detectaron menos de 4 features en la captura. Mejore iluminacion/enfoque "
            "o agregue features manualmente."
        )

    seed = np.float32([[f["x_px"], f["y_px"]] for f in raw_features])
    contour = segment_panel(image, seed)
    if contour is None or len(contour) < 10:
        raise ValueError("No fue posible segmentar el contorno de la pieza.")

    inside = np.zeros((height, width), np.uint8)
    cv2.fillPoly(inside, [contour.astype(np.int32)], 255)
    inside = cv2.erode(inside, np.ones((5, 5), np.uint8))
    raw_features = [
        f for f in raw_features
        if inside[min(height - 1, int(f["y_px"])), min(width - 1, int(f["x_px"]))] > 0
    ]

    rect = cv2.minAreaRect(contour)
    long_px = max(rect[1])
    short_px = min(rect[1])
    reference_px = long_px if known_axis == "long" else short_px
    scale_source = "dimension"
    if known_length and reference_px > 0:
        scale = float(known_length) / reference_px
    elif plane_mm_per_px:
        scale = float(plane_mm_per_px)  # tablero medido en el plano de la pieza
        scale_source = "calibracion"
    else:
        scale = 1.0
        scale_source = "relativa"
    relative = scale_source == "relativa"

    # Orden estable: filas (arriba->abajo) y columnas (izq->der).
    raw_features.sort(key=lambda f: (round(f["y_px"] / (0.08 * height)), f["x_px"]))

    features = []
    for index, item in enumerate(raw_features, start=1):
        brass = item["material"] == "LATON"
        features.append({
            "id": index,
            "type": "INSERTO_LATON" if brass else "INSERTO",
            "material": item["material"],
            "x": round(item["x_px"] * scale, 4),
            "y": round(-item["y_px"] * scale, 4),
            "radius": round(max(item["radius_px"], 3.0) * scale, 4),
            "enabled": True,
            "radius_reliable": item["radius_reliable"],
            "saturation": item["saturation"],
        })

    epsilon = 0.003 * cv2.arcLength(contour, True)
    polygon = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2).astype(np.float64)
    contour_ref = [[round(float(px * scale), 4), round(float(-py * scale), 4)] for px, py in polygon]
    xs = [point[0] for point in contour_ref]
    ys = [point[1] for point in contour_ref]
    bounds = {"xmin": min(xs), "xmax": max(xs), "ymin": min(ys), "ymax": max(ys)}

    label = detect_label(image, contour)
    label_ref = None
    if label is not None:
        label_ref = {
            "x": round(label["x_px"] * scale, 4),
            "y": round(-label["y_px"] * scale, 4),
            "w": round(label["w_px"] * scale, 4),
            "h": round(label["h_px"] * scale, 4),
            "angle": round(label["angle"], 2),
        }

    dimensions = {
        "length": round(long_px * scale, 3),
        "width": round(short_px * scale, 3),
        "area": round(float(cv2.contourArea(contour)) * scale * scale, 2),
        "feature_count": len(features),
        "unit": "px" if relative else "mm",
    }

    preview = draw_reference_preview(image, features, contour, label, scale)
    return {
        "features": features,
        "contour": contour_ref,
        "bounds": bounds,
        "label": label_ref,
        "dimensions": dimensions,
        "scale": scale,
        "relative": relative,
        "scale_source": scale_source,
        "preview": preview,
        "enabled": dict(DEFAULT_ENABLED),
    }


def draw_reference_preview(
    image: np.ndarray,
    features: list[dict[str, Any]],
    contour: np.ndarray,
    label: dict[str, float] | None,
    scale: float,
) -> np.ndarray:
    output = image.copy()
    cv2.polylines(output, [contour.astype(np.int32)], True, (0, 255, 255), 2, cv2.LINE_AA)
    for item in features:
        px = int(round(item["x"] / scale))
        py = int(round(-item["y"] / scale))
        radius = max(5, int(round(item["radius"] / scale)))
        color = (0, 165, 255) if item["material"] == "LATON" else (0, 200, 0)
        if not item.get("enabled", True):
            color = (128, 128, 128)
        cv2.circle(output, (px, py), radius + 3, color, 2, cv2.LINE_AA)
        cv2.putText(output, str(item["id"]), (px + radius + 4, py - radius - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv2.LINE_AA)
    if label is not None:
        box = cv2.boxPoints(((label["x_px"], label["y_px"]), (label["w_px"], label["h_px"]), label["angle"]))
        cv2.polylines(output, [box.astype(np.int32)], True, (255, 128, 0), 2, cv2.LINE_AA)
    return output


# ============================================================
# VERIFICACIONES EXTRA EN INSPECCION
# ============================================================

def _status_from_checks(ranks: list[int]) -> str:
    return ("OK", "REVISAR", "FALLO")[max(ranks) if ranks else 0]


def check_contour(
    rectified: np.ndarray,
    canvas: dict[str, Any],
    golden: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    scale = canvas["scale"]
    xmin_c, _, _, ymax_c = canvas["bounds"]
    expected = np.float32([
        [(x - xmin_c) * scale, (ymax_c - y) * scale] for x, y in golden["contour"]
    ])
    segmented = segment_panel(rectified, None, expected)
    result: dict[str, Any] = {"id": "CONTORNO", "type": "CONTORNO", "kind": "contour"}
    if segmented is None:
        result.update({"status": "FALLO", "score": 0.0, "reasons": ["No se pudo medir el contorno."], "checks": {}})
        return result

    height, width = rectified.shape[:2]
    mask_expected = np.zeros((height, width), np.uint8)
    mask_found = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask_expected, [expected.astype(np.int32)], 255)
    cv2.fillPoly(mask_found, [segmented.astype(np.int32)], 255)
    union = cv2.countNonZero(cv2.bitwise_or(mask_expected, mask_found))
    iou = cv2.countNonZero(cv2.bitwise_and(mask_expected, mask_found)) / max(union, 1)

    rect = cv2.minAreaRect(segmented)
    measured_long = max(rect[1]) / scale
    measured_short = min(rect[1]) / scale
    expected_long = golden["dimensions"]["length"]
    expected_short = golden["dimensions"]["width"]
    unit = golden["dimensions"].get("unit", "mm")

    tolerance_pct = float(options.get("dimension_tolerance_pct", 1.5))
    # Con 3+ piezas buenas se usa la media y la dispersion real (3 sigmas).
    samples = golden.get("dim_samples") or []
    if len(samples) >= 3:
        longs = np.array([sample[0] for sample in samples], dtype=float)
        shorts = np.array([sample[1] for sample in samples], dtype=float)
        expected_long, expected_short = float(longs.mean()), float(shorts.mean())
        spread_pct = max(longs.std(ddof=1) / expected_long, shorts.std(ddof=1) / expected_short) * 100.0
        tolerance_pct = max(tolerance_pct, 3.0 * spread_pct)
    ranks: list[int] = []
    reasons: list[str] = []
    errors = {}
    for name, measured, nominal in (
        ("largo", measured_long, expected_long),
        ("ancho", measured_short, expected_short),
    ):
        error_pct = abs(measured - nominal) / max(nominal, 1e-9) * 100.0
        errors[name] = {"measured": round(measured, 3), "expected": round(nominal, 3),
                        "error_pct": round(error_pct, 2)}
        if error_pct > tolerance_pct * 2:
            ranks.append(2)
            reasons.append(f"{name.capitalize()} {measured:.2f} {unit} vs {nominal:.2f} {unit} ({error_pct:.1f}%).")
        elif error_pct > tolerance_pct:
            ranks.append(1)
            reasons.append(f"{name.capitalize()} {measured:.2f} {unit} vs {nominal:.2f} {unit} ({error_pct:.1f}%).")
        else:
            ranks.append(0)

    if iou < 0.90:
        ranks.append(2)
        reasons.append(f"Forma del contorno distinta (IoU {iou:.2f}).")
    elif iou < 0.95:
        ranks.append(1)
        reasons.append(f"Forma del contorno con diferencias (IoU {iou:.2f}).")

    score = float(np.clip(iou * 100.0 - max(e["error_pct"] for e in errors.values()) * 3.0, 0, 100))
    result.update({
        "status": _status_from_checks(ranks),
        "score": round(score, 2),
        "iou": round(float(iou), 4),
        "dimensions": errors,
        "reasons": reasons,
        "checks": {"iou": "OK" if iou >= 0.95 else "REVISAR" if iou >= 0.90 else "FALLO"},
        "polygon": segmented.astype(np.int32).tolist(),
        "expected_polygon": expected.astype(np.int32).tolist(),
    })
    return result


def check_label(
    rectified: np.ndarray,
    canvas: dict[str, Any],
    golden: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    label = golden.get("label")
    result: dict[str, Any] = {"id": "ETIQUETA", "type": "ETIQUETA", "kind": "label"}
    scale = canvas["scale"]
    xmin_c, _, _, ymax_c = canvas["bounds"]
    cx = (label["x"] - xmin_c) * scale
    cy = (ymax_c - label["y"]) * scale
    w = label["w"] * scale
    h = label["h"] * scale

    hsv = cv2.cvtColor(rectified, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (90, 35, 60), (130, 255, 255))
    margin = 0.6 * max(w, h)
    x1, y1 = int(max(0, cx - w / 2 - margin)), int(max(0, cy - h / 2 - margin))
    x2, y2 = int(min(mask.shape[1], cx + w / 2 + margin)), int(min(mask.shape[0], cy + h / 2 + margin))
    window = cv2.morphologyEx(mask[y1:y2, x1:x2], cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(window, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = max(contours, key=cv2.contourArea) if contours else None
    expected_area = max(w * h, 1.0)

    if best is None or cv2.contourArea(best) < 0.4 * expected_area:
        result.update({
            "status": "FALLO", "score": 0.0, "found": False,
            "center_x_px": int(cx), "center_y_px": int(cy),
            "reasons": ["No se detecta la etiqueta en su posicion."], "checks": {"presence": "FALLO"},
            "box": [int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)],
        })
        return result

    (fx, fy), (fw, fh), _ = cv2.minAreaRect(best)
    fx += x1
    fy += y1
    deviation = math.hypot(fx - cx, fy - cy) / scale
    area_ratio = cv2.contourArea(best) / expected_area
    ranks = [0]
    reasons: list[str] = []
    if deviation > 0.15 * max(label["w"], label["h"]):
        ranks.append(1)
        reasons.append(f"Etiqueta desplazada {deviation:.2f}.")
    if area_ratio < 0.7:
        ranks.append(1)
        reasons.append("Etiqueta incompleta o de tamano distinto.")
    result.update({
        "status": _status_from_checks(ranks),
        "score": round(float(np.clip(100 - deviation * 10 - abs(1 - area_ratio) * 50, 0, 100)), 2),
        "found": True,
        "deviation": round(deviation, 3),
        "center_x_px": int(fx), "center_y_px": int(fy),
        "reasons": reasons,
        "checks": {"presence": "OK"},
        "box": [int(fx - w / 2), int(fy - h / 2), int(fx + w / 2), int(fy + h / 2)],
    })
    return result


def check_distances(
    items: list[dict[str, Any]],
    results: list[dict[str, Any]],
    canvas: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Distancias entre vecinos mas cercanos: independientes del origen."""
    scale = canvas["scale"]
    found = [(item, res) for item, res in zip(items, results) if res.get("found")]
    result: dict[str, Any] = {"id": "DISTANCIAS", "type": "DISTANCIAS", "kind": "distances"}
    if len(found) < 3:
        result.update({"status": "FALLO", "score": 0.0, "reasons": ["Muy pocos features encontrados."], "checks": {}})
        return result

    pts = np.float64([[i["x"], -i["y"]] for i, _ in found])
    measured = np.float64([[r["center_x_subpx"], r["center_y_subpx"]] for _, r in found]) / scale
    tolerance_pct = float(options.get("distance_tolerance_pct", 1.5))
    pairs = set()
    for index in range(len(found)):
        order = np.argsort(np.hypot(*(pts - pts[index]).T))[1:4]
        for other in order:
            pairs.add(tuple(sorted((index, int(other)))))

    ranks = [0]
    worst = 0.0
    worst_pair = None
    detail = []
    for a, b in pairs:
        nominal = float(np.hypot(*(pts[a] - pts[b])))
        actual = float(np.hypot(*(measured[a] - measured[b])))
        error_pct = abs(actual - nominal) / max(nominal, 1e-9) * 100.0
        # Piso absoluto de ~3 px de canvas: en distancias cortas el ruido de
        # medicion domina el porcentaje.
        error_pct = max(0.0, error_pct - 3.0 / scale / max(nominal, 1e-9) * 100.0 * 0.0)
        floor_pct = 3.0 / scale / max(nominal, 1e-9) * 100.0
        error_pct = error_pct if error_pct > floor_pct else 0.0
        if error_pct > worst:
            worst = error_pct
            worst_pair = (found[a][0]["id"], found[b][0]["id"], nominal, actual)
        detail.append({"a": found[a][0]["id"], "b": found[b][0]["id"],
                       "expected": round(nominal, 3), "measured": round(actual, 3),
                       "error_pct": round(error_pct, 2)})
    reasons: list[str] = []
    if worst > tolerance_pct * 2:
        ranks.append(2)
    elif worst > tolerance_pct:
        ranks.append(1)
    if worst_pair is not None and worst > tolerance_pct:
        reasons.append(f"Distancia {worst_pair[0]}-{worst_pair[1]}: {worst_pair[3]:.2f} vs {worst_pair[2]:.2f} ({worst:.1f}%).")

    result.update({
        "status": _status_from_checks(ranks),
        "score": round(float(np.clip(100 - worst * 20, 0, 100)), 2),
        "max_error_pct": round(worst, 2),
        "pairs_checked": len(pairs),
        "distances": sorted(detail, key=lambda d: -d["error_pct"])[:20],
        "reasons": reasons,
        "checks": {},
    })
    return result


def run_extra_checks(
    rectified: np.ndarray,
    canvas: dict[str, Any],
    golden: dict[str, Any],
    items: list[dict[str, Any]],
    results: list[dict[str, Any]],
    options: dict[str, Any],
) -> list[dict[str, Any]]:
    enabled = {**DEFAULT_ENABLED, **(golden.get("enabled") or {})}
    extras: list[dict[str, Any]] = []
    if enabled.get("contour") and golden.get("contour"):
        extras.append(check_contour(rectified, canvas, golden, options))
    if enabled.get("label") and golden.get("label"):
        extras.append(check_label(rectified, canvas, golden, options))
    if enabled.get("distances"):
        extras.append(check_distances(items, results, canvas, options))
    return extras
