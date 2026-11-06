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

from .pipeline import LARGE_HOLE_FRACTION, LARGE_HOLE_MIN_PX, MIN_ANCHORS, classify_color, classify_feature_type, classify_large_hole, measure_radius
from .registration import detect_blob_candidates

LABEL_MIN_SOLIDITY = 0.75

FEATURE_CATEGORIES = ("features", "contour", "distances", "label")

DEFAULT_ENABLED = {key: True for key in FEATURE_CATEGORIES}

MATERIAL_BY_TYPE = {"INSERTO": "ACERO", "DELRON_CONICO": "DELRON", "DELRON_PLANO": "DELRON", "BARRENO": "BARRENO"}
# Mismos colores (BGR) que el preview del DXF.
TYPE_COLORS = {
    "INSERTO": (0, 165, 255),
    "DELRON_CONICO": (255, 0, 255),
    "DELRON_PLANO": (0, 200, 0),
    "BARRENO": (255, 200, 0),
}


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
    darkest = float(blurred.min())
    # Si el componente toca la ventana (agujero unido a la sombra de un cono o
    # a un gradiente de luz) se reintenta con un umbral mas cercano al nucleo.
    for _ in range(3):
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
        if not touches:
            break
        threshold = (darkest + threshold) / 2.0
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
        if shape["circularity"] < 0.55 or shape["aspect"] < 0.62:
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
        shape_type = classify_feature_type(gray, hsv, candidate["x"], candidate["y"], radius)
        if shape_type is None:
            continue  # centro no oscuro: reflejo o mancha, no un agujero
        color = classify_color(hsv, candidate["x"], candidate["y"], radius, 60)
        features.append({
            "type": shape_type["observed_type"],
            "x_px": candidate["x"],
            "y_px": candidate["y"],
            "radius_px": float(radius),
            "bore_px": float(radius if shape_type["observed_type"] == "BARRENO" else shape_type["bore_radius_px"]),
            "rim_px": shape_type.get("rim_radius_px"),
            "response": candidate["response"],
            "material": MATERIAL_BY_TYPE[shape_type["observed_type"]],
            "saturation": color["saturation"],
            "circularity": round(shape["circularity"], 2),
            "radius_reliable": bool(consistency >= 0.5),
        })

    # Radios no fiables (sombra, bajo contraste) heredan el radio tipico del mismo tipo.
    for item in features:
        if item["radius_reliable"]:
            continue
        peers = [f["radius_px"] for f in features if f["radius_reliable"] and f["type"] == item["type"]]
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
# HUECOS GRANDES
# ============================================================

def rescale_analysis(analysis: dict[str, Any], factor: float, source: str) -> dict[str, Any]:
    """Convierte un analisis en pixeles (escala 1) a unidades reales: `factor` mm por pixel."""
    for feature in analysis["features"]:
        for key in ("x", "y", "radius", "outer_radius", "bore_radius"):
            feature[key] = round(feature[key] * factor, 4)
    analysis["contour"] = [[round(x * factor, 4), round(y * factor, 4)] for x, y in analysis["contour"]]
    analysis["bounds"] = {key: round(value * factor, 4) for key, value in analysis["bounds"].items()}
    if analysis.get("label"):
        for key in ("x", "y", "w", "h"):
            analysis["label"][key] = round(analysis["label"][key] * factor, 4)
    dims = analysis["dimensions"]
    dims.update({
        "length": round(dims["length"] * factor, 3),
        "width": round(dims["width"] * factor, 3),
        "area": round(dims["area"] * factor * factor, 2),
        "unit": "mm",
    })
    analysis["scale"] = analysis["scale"] * factor
    analysis["relative"] = False
    analysis["scale_source"] = source
    return analysis


def _outer_px(item: dict[str, Any]) -> float:
    """Radio exterior del feature (cabeza del delron, disco del inserto), en pixeles."""
    if item["type"] == "BARRENO":
        return float(item["radius_px"])
    return float(item.get("rim_px") or max(2.2 * item["bore_px"], item["radius_px"]))


def detect_large_holes(
    image: np.ndarray,
    inside: np.ndarray,
    features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Huecos circulares grandes (se ve el fondo a traves), p. ej. el del centro.

    El detector de blobs solo llega a radios de ~10 px, asi que se buscan con
    Hough y se validan: el centro debe estar dentro de la pieza, el interior no
    debe contener otro feature (un delron/inserto tambien es un disco) y el
    borde debe ser circular y continuo (classify_large_hole).
    """
    height, width = image.shape[:2]
    longest = max(height, width)
    scale = min(1.0, 800.0 / longest)
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else image
    blurred = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (0, 0), 2.0)
    min_radius = max(15, int(0.03 * max(small.shape[:2])))
    circles = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT, dp=1.2, minDist=min_radius * 2,
        param1=120, param2=35, minRadius=min_radius, maxRadius=int(0.2 * max(small.shape[:2])),
    )
    if circles is None:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    found: list[dict[str, Any]] = []
    for cx, cy, radius in circles[0]:
        cx, cy, radius = float(cx) / scale, float(cy) / scale, float(radius) / scale
        if radius < LARGE_HOLE_MIN_PX * (width / 1800.0):
            continue
        px, py = int(round(cx)), int(round(cy))
        if not (0 <= px < width and 0 <= py < height) or inside[py, px] == 0:
            continue
        if any(math.hypot(f["x_px"] - cx, f["y_px"] - cy) < radius for f in features):
            continue
        verdict = classify_large_hole(gray, hsv, cx, cy, radius)
        if verdict is None:
            continue
        # Hueco entero dentro de la pieza (no una muesca del contorno).
        probe = np.zeros_like(inside)
        cv2.circle(probe, (px, py), int(verdict["hole_radius_px"] * 1.15), 255, -1)
        outside_fraction = cv2.countNonZero(cv2.bitwise_and(probe, cv2.bitwise_not(inside))) / max(cv2.countNonZero(probe), 1)
        if outside_fraction > 0.35:
            continue
        found.append({
            "type": "BARRENO",
            "x_px": cx,
            "y_px": cy,
            "radius_px": float(verdict["hole_radius_px"]),
            "bore_px": float(verdict["hole_radius_px"]),
            "response": float(verdict["contrast"]),
            "material": MATERIAL_BY_TYPE["BARRENO"],
            "saturation": 0.0,
            "circularity": 1.0,
            "radius_reliable": bool(verdict["edge_consistency"] >= 0.7),
        })
    return found


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
    # Mismo afinado de borde que usa el alta de la pieza, para que medir y dar de alta coincidan.
    no_points = np.zeros((0, 2), np.float32)
    return _refine_edges(image, (largest / scale).reshape(-1, 2).astype(np.float32), no_points, np.zeros(0, np.float32))


def _plate_features(image: np.ndarray, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Features cuyo entorno inmediato es placa metalica.

    Descarta falsos positivos sobre la mesa (una banda negra, un objeto oscuro): su anillo
    exterior tiene el color de la mesa y no el de la mayoria de los features, que estan en la placa.
    """
    if len(features) < 4:
        return features
    height, width = image.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(image, (0, 0), 1.5), cv2.COLOR_BGR2LAB).astype(np.float32)
    colors: list[np.ndarray | None] = []
    for feature in features:
        radius = _outer_px(feature)
        x, y = int(feature["x_px"]), int(feature["y_px"])
        reach = int(2.6 * radius) + 2
        x0, y0, x1, y1 = max(0, x - reach), max(0, y - reach), min(width, x + reach + 1), min(height, y + reach + 1)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        distance = np.hypot(xx - feature["x_px"], yy - feature["y_px"])
        ring = lab[y0:y1, x0:x1][(distance >= 1.4 * radius) & (distance <= 2.4 * radius)]
        colors.append(np.median(ring, axis=0) if len(ring) >= 12 else None)
    known = np.array([c for c in colors if c is not None])
    if len(known) < 4:
        return features
    center = np.median(known, axis=0)
    weights = np.array([0.25, 1.0, 1.0])  # el brillo varia mucho con el reflejo: pesa menos que el color
    deviation = np.array([np.linalg.norm((c - center) * weights) if c is not None else np.inf for c in colors])
    finite = deviation[np.isfinite(deviation)]
    limit = max(14.0, float(np.median(finite) + 3.5 * 1.4826 * np.median(np.abs(finite - np.median(finite)))))
    good = [f for f, d in zip(features, deviation) if d <= limit]
    return good if len(good) >= 4 else features


# Un pixel se descarta del contorno si su color (a, b de Lab) se aleja de la placa mas que esto,
# medido en desviaciones robustas del color de la placa (con un piso para placas casi neutras).
# Solidez (area / area del casco) desde la cual el contorno se sustituye por su casco convexo.
HULL_MIN_SOLIDITY = 0.985
OFF_COLOR_SIGMAS = 3.0
OFF_COLOR_MIN_DISTANCE = 4.5


def _drop_off_color(
    small: np.ndarray, segmented: np.ndarray, points: np.ndarray, radii: np.ndarray
) -> np.ndarray:
    """Quita de la segmentacion lo que no tiene el color de la placa (madera, mesa de color).

    GrabCut parte de una caja convexa de features y, en piezas con esquinas cortadas, arrastra
    pedazos de mesa. El color de la placa se muestrea en el anillo alrededor de cada feature
    (placa segura). Solo se usa el croma (a, b): el brillo cambia mucho con los reflejos, y una
    mesa gris sobre placa gris no se toca aqui (la resuelve GrabCut).
    """
    sh, sw = small.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(small, (0, 0), 1.2), cv2.COLOR_BGR2LAB).astype(np.float32)
    ring = np.zeros((sh, sw), np.uint8)
    for (x, y), radius in zip(points, radii):
        cv2.circle(ring, (int(x), int(y)), int(2.6 * radius) + 2, 255, -1)
        cv2.circle(ring, (int(x), int(y)), int(1.4 * radius) + 1, 0, -1)
    samples = lab[(ring > 0) & (segmented > 0)][:, 1:]
    if len(samples) < 200:
        return segmented
    center = np.median(samples, axis=0)
    spread = 1.4826 * np.median(np.abs(samples - center), axis=0)
    limit = max(OFF_COLOR_SIGMAS * float(spread.max()), OFF_COLOR_MIN_DISTANCE)
    distance = np.linalg.norm(lab[:, :, 1:] - center, axis=2)
    outside = lab[segmented == 0][:, 1:]
    if len(outside) < 200:
        return segmented
    background = np.median(outside, axis=0)
    if float(np.linalg.norm(background - center)) < limit:
        return segmented  # mesa y placa del mismo color: el croma no distingue, se queda GrabCut
    # Solo sale lo que ademas se parece mas a la mesa que a la placa (no etiquetas ni reflejos de color).
    to_background = np.linalg.norm(lab[:, :, 1:] - background, axis=2)
    cleaned = segmented.copy()
    cleaned[(distance > limit) & (to_background < distance) & (segmented > 0)] = 0
    # Lo que se quito tiene que ser una region (no pixeles sueltos de la placa): se abre y se cierra.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    return cleaned if cv2.countNonZero(cleaned) >= 0.5 * cv2.countNonZero(segmented) else segmented


# Banda (fraccion del lado mayor) alrededor del contorno aproximado donde se afina el borde.
REFINE_BAND = 0.025
REFINE_MAX_SIDE = 1600
# Ancho (fraccion del lado mayor) por debajo del cual una punta del contorno se considera rebaba.
REFINE_SPUR = 0.01
# Al inspeccionar la pieza ya esta alineada: el borde solo puede moverse esta fraccion del lado corto.
INSPECT_BAND_RATIO = 0.04


def _refine_edges(
    image: np.ndarray, contour: np.ndarray, points: np.ndarray, radii: np.ndarray, band_ratio: float = 0.12
) -> np.ndarray:
    """Segundo GrabCut a mayor resolucion, solo en una banda angosta del contorno aproximado.

    El primer paso trabaja reducido y deja el borde redondeado; aqui el interior seguro y el exterior
    seguro quedan fuera de la banda y GrabCut decide el borde real dentro de ella, asi se siguen
    pestanas, escalones y bordes inclinados de la pieza. Si el resultado se aleja mucho del primero
    (se fugo a la mesa) se conserva el contorno aproximado.
    """
    height, width = image.shape[:2]
    scale = min(1.0, REFINE_MAX_SIDE / max(height, width))
    work = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else image
    wh, ww = work.shape[:2]
    shape = np.zeros((wh, ww), np.uint8)
    cv2.fillPoly(shape, [(contour * scale).astype(np.int32)], 255)
    # La banda no debe pasar de una fraccion del lado corto: en una tira angosta una banda ancha deja
    # que el borde baile varios pixeles entre una medicion y otra.
    short_side = min(cv2.minAreaRect((contour * scale).astype(np.float32))[1])
    band = max(3, int(min(REFINE_BAND * max(wh, ww), band_ratio * short_side)))
    kernel = lambda size: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * size + 1, 2 * size + 1))
    inner = cv2.erode(shape, kernel(band))
    outer = cv2.dilate(shape, kernel(band))
    mask = np.full((wh, ww), cv2.GC_BGD, np.uint8)
    mask[outer > 0] = cv2.GC_PR_BGD
    mask[shape > 0] = cv2.GC_PR_FGD
    mask[inner > 0] = cv2.GC_FGD
    border = max(2, int(0.004 * max(wh, ww)))
    mask[:border, :] = mask[-border:, :] = mask[:, :border] = mask[:, -border:] = cv2.GC_BGD
    background, foreground = np.zeros((1, 65)), np.zeros((1, 65))
    try:
        cv2.grabCut(work, mask, None, background, foreground, 4, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return contour
    refined = ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)).astype(np.uint8) * 255
    for (x, y), radius in zip(points * scale, radii * scale):
        cv2.circle(refined, (int(x), int(y)), int(1.3 * radius) + 1, 255, -1)
    # Rebabas y puntas sueltas (restos de la mesa, borde negro del encuadre): hasta ~1 % del lado mayor u 8 % del corto.
    spur = max(5, int(max(REFINE_SPUR * max(wh, ww), 0.08 * short_side)) | 1)
    refined = cv2.morphologyEx(refined, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (spur, spur)))
    refined = cv2.morphologyEx(refined, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    contours, _ = cv2.findContours(refined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return contour
    best = max(contours, key=cv2.contourArea)
    overlap = cv2.countNonZero(cv2.bitwise_and(refined, shape)) / max(cv2.countNonZero(shape), 1)
    if overlap < 0.9 or cv2.contourArea(best) < 0.8 * cv2.countNonZero(shape):
        return contour
    return (best / scale).reshape(-1, 2).astype(np.float32)


def segment_panel_from_features(image: np.ndarray, features: list[dict[str, Any]]) -> np.ndarray | None:
    """Contorno exterior de la placa sembrado con los propios features.

    Cada feature aporta placa segura a su alrededor (su anillo exterior), asi GrabCut aprende el
    aspecto de TODA la placa, incluidas las esquinas en sombra o con reflejo lejos del centro.
    El fondo seguro es lo que queda lejos de los features y una franja fina del borde de la
    imagen; la zona intermedia se deja probable para que GrabCut decida.
    """
    height, width = image.shape[:2]
    scale = min(1.0, 800.0 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else image.copy()
    sh, sw = small.shape[:2]
    points = np.float32([[f["x_px"], f["y_px"]] for f in features]) * scale
    radii = np.float32([_outer_px(f) for f in features]) * scale
    if len(points) < 3:
        return None

    span = max(float(np.ptp(points[:, 0])), float(np.ptp(points[:, 1])), 10.0)
    big = float(radii.max())
    hull = np.zeros((sh, sw), np.uint8)
    cv2.fillConvexPoly(hull, cv2.convexHull(points.astype(np.int32)), 255)

    def grow(mask: np.ndarray, amount: float) -> np.ndarray:
        size = 2 * max(1, int(amount)) + 1
        return cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))

    probable_fg = grow(hull, 0.05 * span + 2.0 * big)
    possible = grow(hull, 0.30 * span + 4.0 * big)

    mask = np.full((sh, sw), cv2.GC_BGD, np.uint8)
    mask[possible > 0] = cv2.GC_PR_BGD
    mask[probable_fg > 0] = cv2.GC_PR_FGD
    border = max(2, int(0.004 * max(sh, sw)))
    mask[:border, :] = mask[-border:, :] = mask[:, :border] = mask[:, -border:] = cv2.GC_BGD

    sure = np.zeros((sh, sw), np.uint8)
    for (x, y), radius in zip(points, radii):
        cv2.circle(sure, (int(x), int(y)), int(1.8 * radius) + 2, 255, -1)
    shrunk = (points.mean(axis=0) + (cv2.convexHull(points.astype(np.int32)).reshape(-1, 2) - points.mean(axis=0)) * 0.7)
    cv2.fillConvexPoly(sure, shrunk.astype(np.int32), 255)
    mask[sure > 0] = cv2.GC_FGD

    background = np.zeros((1, 65))
    foreground = np.zeros((1, 65))
    try:
        cv2.grabCut(small, mask, None, background, foreground, 6, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None

    segmented = ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)).astype(np.uint8) * 255
    segmented = _drop_off_color(small, segmented, points, radii)
    # Los features siempre son parte de la pieza.
    for (x, y), radius in zip(points, radii):
        cv2.circle(segmented, (int(x), int(y)), int(1.3 * radius) + 1, 255, -1)
    segmented = cv2.morphologyEx(segmented, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    close = max(3, int(0.012 * max(sh, sw)))
    segmented = cv2.morphologyEx(segmented, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close, close)))

    contours, _ = cv2.findContours(segmented, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    # La region que contiene mas features (no una mancha suelta de la mesa).
    def covered(contour: np.ndarray) -> int:
        return sum(cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0 for x, y in points)

    best = max(contours, key=lambda c: (covered(c), cv2.contourArea(c)))
    best = _refine_edges(image, (best / scale).astype(np.float32), points / scale, radii / scale)
    scale = 1.0
    # Las piezas son casi convexas: una muesca pequena es un reflejo o una sombra, no la forma.
    hull = cv2.convexHull(best)
    if cv2.contourArea(best) >= HULL_MIN_SOLIDITY * cv2.contourArea(hull):
        best = hull
    return (best / scale).reshape(-1, 2).astype(np.float32)


def detect_label(image: np.ndarray, contour: np.ndarray) -> dict[str, float] | None:
    """Etiqueta azulada/clara de identificacion dentro del panel."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (90, 35, 60), (130, 255, 255))
    inside = np.zeros(mask.shape, np.uint8)
    cv2.fillPoly(inside, [contour.astype(np.int32)], 255)
    mask = cv2.bitwise_and(mask, inside)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # Una etiqueta es una mancha maciza y rectangular (solidez ~0.9); un trazo azulado de tinta o
    # una sombra (solidez ~0.6) no lo es.
    def solid(blob: np.ndarray) -> bool:
        (_, _), (bw, bh), _ = cv2.minAreaRect(blob)
        return cv2.contourArea(blob) >= LABEL_MIN_SOLIDITY * max(bw * bh, 1.0)

    contours = [c for c in contours if solid(c)]
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

    plate_features = _plate_features(image, raw_features)
    contour = segment_panel_from_features(image, plate_features)
    if contour is None or len(contour) < 10:
        seed = np.float32([[f["x_px"], f["y_px"]] for f in plate_features])
        contour = segment_panel(image, seed)
    if contour is None or len(contour) < 10:
        raise ValueError("No fue posible segmentar el contorno de la pieza.")

    inside = np.zeros((height, width), np.uint8)
    cv2.fillPoly(inside, [contour.astype(np.int32)], 255)
    # Un feature pegado al borde puede quedar a unos pixeles fuera del contorno suavizado: se
    # conserva si su centro esta a menos de 0.8 de su radio de la silueta.
    outside_distance = cv2.distanceTransform(255 - inside, cv2.DIST_L2, 3)
    raw_features = [
        f for f in raw_features
        if inside[min(height - 1, int(f["y_px"])), min(width - 1, int(f["x_px"]))] > 0
        or outside_distance[min(height - 1, int(f["y_px"])), min(width - 1, int(f["x_px"]))] <= 0.8 * _outer_px(f)
    ]
    # Los huecos grandes dentro de la pieza (el GrabCut los deja fuera del contorno
    # exterior, asi que la mascara "inside" sigue siendo la silueta exterior).
    filled = np.zeros((height, width), np.uint8)
    cv2.fillPoly(filled, [contour.astype(np.int32)], 255)
    raw_features += detect_large_holes(image, filled, raw_features)
    anchors = [f for f in raw_features if f["type"] != "BARRENO" or f["radius_px"] < LARGE_HOLE_FRACTION * max(width, height)]
    if len(anchors) < MIN_ANCHORS:
        raise ValueError(
            f"Solo hay {len(anchors)} features pequeños (insertos, delrones o barrenos chicos) en la zona analizada; "
            f"se necesitan al menos {MIN_ANCHORS} para alinear la pieza (un hueco grande no sirve para alinear). "
            "Amplíe el recorte, mejore la iluminación o enfoque."
        )

    epsilon = 0.003 * cv2.arcLength(contour, True)
    polygon = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2).astype(np.float64)
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
        features.append({
            "id": index,
            "type": item["type"],
            "material": item["material"],
            "x": round(item["x_px"] * scale, 4),
            "y": round(-item["y_px"] * scale, 4),
            "radius": round(max(item["radius_px"], 3.0) * scale, 4),
            # Para el modelo 3D: radio exterior de la cabeza/disco y del barreno central.
            "outer_radius": round(_outer_px(item) * scale, 4),
            "bore_radius": round(
                (item["radius_px"] if item["type"] == "BARRENO" else min(item["bore_px"], 0.85 * _outer_px(item))) * scale, 4
            ),
            "enabled": True,
            "radius_reliable": item["radius_reliable"],
            "saturation": item["saturation"],
        })

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
        color = TYPE_COLORS.get(item["type"], (0, 165, 255))
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
    # La pieza ya esta alineada: el borde se mide directamente en una banda alrededor del contorno esperado,
    # igual que se afino al dar de alta, asi ambas mediciones comparten el mismo ruido.
    segmented = _refine_edges(
        rectified, expected, np.zeros((0, 2), np.float32), np.zeros(0, np.float32), band_ratio=INSPECT_BAND_RATIO
    )
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

    # Misma simplificacion que el contorno esperado: se comparan poligonos equivalentes.
    simplified = cv2.approxPolyDP(segmented.reshape(-1, 1, 2), 0.003 * cv2.arcLength(segmented.reshape(-1, 1, 2), True), True)
    rect = cv2.minAreaRect(simplified.reshape(-1, 2).astype(np.float32))
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
        # Piso absoluto: el centro de un feature se mide con la misma tolerancia que su posicion
        # (fraccion de su radio); en distancias cortas ese ruido, no el porcentaje, domina. Solo la
        # parte del error que excede el piso cuenta.
        radius = max(float(found[a][0]["radius"]), float(found[b][0]["radius"]))
        floor_abs = max(float(options.get("position_ok_radii", 0.45)) * radius, 3.0 / scale)
        error_pct = max(0.0, abs(actual - nominal) - floor_abs) / max(nominal, 1e-9) * 100.0
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
    if enabled.get("reading", True) and golden.get("readings"):
        extras.extend(check_readings(rectified, canvas, golden))
    return extras


def check_readings(rectified: np.ndarray, canvas: dict[str, Any], golden: dict[str, Any]) -> list[dict[str, Any]]:
    """Zonas de lectura (codigos / texto) sobre la imagen rectificada.

    Las zonas se guardan en unidades de la referencia (como los features: y hacia arriba negativa); aqui se
    pasan a pixeles del lienzo. Un resultado por zona, con su valor leido y el esperado.
    """
    from modules.reading import service as reading  # noqa: PLC0415 - opcional

    scale = canvas["scale"]
    xmin_c, _, _, ymax_c = canvas["bounds"]
    zones = []
    for zone in golden["readings"]:
        zones.append({**zone, "x": (zone["x"] - xmin_c) * scale, "y": (ymax_c - zone["y"]) * scale,
                      "w": zone["w"] * scale, "h": zone["h"] * scale})
    out: list[dict[str, Any]] = []
    for zone, read in zip(zones, reading.read_zones(rectified, zones)):
        status = "OK" if read["status"] == "OMITIDO" else read["status"]
        x1, y1 = int(zone["x"]), int(zone["y"])
        out.append({
            "id": f"LECTURA {read['name']}", "type": "LECTURA", "kind": "reading",
            "status": status, "score": {"OK": 100.0, "REVISAR": 50.0, "FALLO": 0.0}[status],
            # En OK se muestra lo leido (el panel de resultados lista el primer motivo de cada item).
            "reasons": ([f"Leido «{read['value']}»"] if read["value"] else []) if read["status"] == "OK" else [read["reason"]],
            "value": read["value"], "expected": read["expected"], "rule": read["rule"], "format": read["format"],
            "confidence": read["confidence"], "reading_status": read["status"], "name": read["name"],
            "box": [x1, y1, int(zone["x"] + zone["w"]), int(zone["y"] + zone["h"])],
        })
    return out
