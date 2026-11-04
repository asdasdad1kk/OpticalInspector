"""Pipeline de comparacion optica independiente del origen.

Recibe un conjunto de features de referencia (de un DXF o de una pieza
patron real) y una fotografia, registra ambos por geometria de los propios
features y mide cada uno localmente: posicion, diametro, tipo (color) y
coincidencia de bordes.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import cv2
import numpy as np

from .registration import _apply, detect_blob_candidates, register


RECTIFIED_WIDTH = 1800
CANVAS_MARGIN = 0.04

# Umbrales por defecto; cada inspeccion puede sobreescribirlos con options.
DEFAULT_OPTIONS: dict[str, Any] = {
    "check_position": True,
    "check_type": True,
    "check_diameter": True,
    "check_edges": True,
    "position_ok_radii": 0.45,       # desviacion maxima OK, en radios del feature
    "position_review_radii": 0.90,   # hasta aqui REVISAR, despues FALLO
    "diameter_review_ratio": 0.35,
    "brass_saturation": 60,
    "min_presence": 0.30,
    "dimension_tolerance_pct": 1.5,  # contorno (pieza real)
    "distance_tolerance_pct": 1.5,   # distancias entre features (pieza real)
}

TYPE_BRASS = {"DELRON_CONICO", "DELRON_PLANO"}


def merge_options(options: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_OPTIONS)
    if options:
        merged.update({k: v for k, v in options.items() if k in DEFAULT_OPTIONS})
    return merged


# ============================================================
# GEOMETRIA
# ============================================================

def build_canvas(bounds: tuple[float, float, float, float]) -> dict[str, Any]:
    xmin, xmax, ymin, ymax = bounds
    width_units = xmax - xmin
    height_units = ymax - ymin
    margin = CANVAS_MARGIN * max(width_units, height_units)
    xmin -= margin
    xmax += margin
    ymin -= margin
    ymax += margin
    width_units = xmax - xmin
    height_units = ymax - ymin
    scale = RECTIFIED_WIDTH / width_units
    height_px = int(round(height_units * scale))
    # Referencia (x, -y) -> canvas.
    matrix = np.array(
        [[scale, 0.0, -xmin * scale], [0.0, scale, ymax * scale], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    return {
        "scale": scale,
        "width": RECTIFIED_WIDTH,
        "height": height_px,
        "matrix": matrix,
        "bounds": (xmin, xmax, ymin, ymax),
    }


def reference_to_canvas(canvas: dict[str, Any], x: float, y: float) -> tuple[float, float]:
    return (
        float((x - canvas["bounds"][0]) * canvas["scale"]),
        float((canvas["bounds"][3] - y) * canvas["scale"]),
    )


# ============================================================
# MEDICION LOCAL DE UN FEATURE
# ============================================================

def _parabolic_offset(prev: float, cur: float, nxt: float) -> float:
    denominator = prev - 2.0 * cur + nxt
    if abs(denominator) < 1e-9:
        return 0.0
    return float(np.clip(0.5 * (prev - nxt) / denominator, -1.0, 1.0))


def locate_center(
    gray: np.ndarray, cx: float, cy: float, radius_px: float, search_px: float
) -> tuple[float, float, float] | None:
    """Centro del agujero/inserto por LoG local. Devuelve (x, y, presencia 0-1)."""
    height, width = gray.shape[:2]
    half = int(math.ceil(search_px + radius_px * 1.5))
    x1 = max(0, int(cx) - half)
    y1 = max(0, int(cy) - half)
    x2 = min(width, int(cx) + half + 1)
    y2 = min(height, int(cy) + half + 1)
    window = gray[y1:y2, x1:x2].astype(np.float32)
    if window.shape[0] < 9 or window.shape[1] < 9:
        return None

    best: tuple[float, float, float] | None = None
    best_value = 0.0
    for factor in (0.30, 0.42, 0.56):
        sigma = max(1.5, radius_px * factor)
        blurred = cv2.GaussianBlur(window, (0, 0), sigma)
        response = cv2.Laplacian(blurred, cv2.CV_32F, ksize=3) * sigma * sigma
        yy, xx = np.mgrid[y1:y2, x1:x2]
        inside = (xx - cx) ** 2 + (yy - cy) ** 2 <= search_px ** 2
        # Sesgo suave hacia la posicion esperada: evita saltar a texto/rayones cercanos.
        weight = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * (0.6 * search_px) ** 2)))
        masked = np.where(inside, response * weight, -1e9)
        index = np.unravel_index(int(np.argmax(masked)), masked.shape)
        value = float(masked[index])
        if value > best_value:
            ry, rx = index
            offset_x = offset_y = 0.0
            if 0 < rx < response.shape[1] - 1:
                offset_x = _parabolic_offset(
                    response[ry, rx - 1], response[ry, rx], response[ry, rx + 1]
                )
            if 0 < ry < response.shape[0] - 1:
                offset_y = _parabolic_offset(
                    response[ry - 1, rx], response[ry, rx], response[ry + 1, rx]
                )
            best_value = value
            best = (x1 + rx + offset_x, y1 + ry + offset_y, value)

    if best is None:
        return None

    local_contrast = float(np.std(window)) + 1e-6
    presence = float(np.clip(best[2] / (local_contrast * 2.2), 0.0, 1.0))
    return best[0], best[1], presence


def measure_radius(
    gray: np.ndarray, cx: float, cy: float, expected_radius_px: float
) -> tuple[float | None, float]:
    """Radio por borde radial dominante; devuelve (radio, consistencia 0-1)."""
    blurred = cv2.GaussianBlur(gray, (0, 0), max(1.0, 0.12 * expected_radius_px)).astype(np.float32)
    height, width = gray.shape[:2]
    low = max(2.0, expected_radius_px * 0.35)
    high = expected_radius_px * 1.9
    radii = np.arange(low, high, 0.5)
    found = []
    for angle in np.linspace(0, 2 * math.pi, 48, endpoint=False):
        xs = cx + radii * math.cos(angle)
        ys = cy + radii * math.sin(angle)
        valid = (xs >= 0) & (xs < width - 1) & (ys >= 0) & (ys < height - 1)
        if valid.sum() < 6:
            continue
        profile = cv2.remap(
            blurred,
            xs[valid].astype(np.float32).reshape(1, -1),
            ys[valid].astype(np.float32).reshape(1, -1),
            cv2.INTER_LINEAR,
        ).ravel()
        gradient = np.abs(np.gradient(profile))
        found.append(radii[valid][int(np.argmax(gradient))])
    if len(found) < 12:
        return None, 0.0
    median = float(np.median(found))
    spread = float(np.median(np.abs(np.asarray(found) - median)))
    consistency = float(np.clip(1.0 - spread / max(median, 1.0) / 0.35, 0.0, 1.0))
    return median, consistency


def classify_color(
    hsv: np.ndarray, cx: float, cy: float, radius_px: float, brass_sat: float, reach_factor: float = 1.3
) -> dict[str, Any]:
    """Latón vs acero por saturacion, ignorando el hueco oscuro central.

    La saturacion se infla en pixeles oscuros, asi que solo cuentan los
    pixeles razonablemente brillantes del disco (1.3 R).
    """
    height, width = hsv.shape[:2]
    reach = max(3, int(round(radius_px * reach_factor)))
    x1, y1 = max(0, int(cx) - reach), max(0, int(cy) - reach)
    x2, y2 = min(width, int(cx) + reach + 1), min(height, int(cy) + reach + 1)
    window = hsv[y1:y2, x1:x2].reshape(-1, 3).astype(np.float32)
    yy, xx = np.mgrid[y1:y2, x1:x2]
    inside = ((xx - cx) ** 2 + (yy - cy) ** 2 <= reach ** 2).ravel()
    pixels = window[inside]
    bright = pixels[pixels[:, 2] >= 55]
    if len(bright) < max(6, 0.2 * len(pixels)):
        bright = pixels
    hue, sat, val = (float(np.mean(bright[:, i])) for i in range(3))
    return {
        "hue": round(hue, 1),
        "saturation": round(sat, 1),
        "value": round(val, 1),
        "observed": "LATON" if sat >= brass_sat and val >= 55 else "ACERO",
    }


# ============================================================
# EVALUACION DE UN FEATURE
# ============================================================

def evaluate_feature(
    item: dict[str, Any],
    canvas: dict[str, Any],
    gray: np.ndarray,
    hsv: np.ndarray,
    options: dict[str, Any],
    edge_score_fn: Callable[[int, int, int, int, int], dict[str, float]] | None,
) -> dict[str, Any]:
    scale = canvas["scale"]
    expected_x, expected_y = reference_to_canvas(canvas, item["x"], item["y"])
    radius_px = max(4.0, float(item["radius"]) * scale)
    search_px = radius_px * 1.6

    located = locate_center(gray, expected_x, expected_y, radius_px, search_px)
    reasons: list[str] = []
    checks: dict[str, str] = {}

    result: dict[str, Any] = {
        "id": item["id"],
        "type": item["type"],
        "expected_x_px": round(expected_x, 1),
        "expected_y_px": round(expected_y, 1),
        "expected_radius_px": round(radius_px, 1),
    }

    if located is None or located[2] < options["min_presence"]:
        result.update({
            "status": "FALLO",
            "score": 0.0,
            "found": False,
            "center_x_px": int(round(expected_x)),
            "center_y_px": int(round(expected_y)),
            "deviation": None,
            "reasons": ["No se detecta el feature en la posicion esperada."],
            "checks": {"presence": "FALLO"},
        })
        return result

    found_x, found_y, presence = located
    deviation_px = math.hypot(found_x - expected_x, found_y - expected_y)
    deviation_units = deviation_px / scale
    deviation_radii = deviation_px / radius_px

    score_parts: list[float] = [presence * 100.0]
    status_rank = 0  # 0 OK, 1 REVISAR, 2 FALLO

    if options["check_position"]:
        if deviation_radii <= options["position_ok_radii"]:
            checks["position"] = "OK"
        elif deviation_radii <= options["position_review_radii"]:
            checks["position"] = "REVISAR"
            status_rank = max(status_rank, 1)
            reasons.append(f"Posicion desviada {deviation_units:.3f}.")
        else:
            checks["position"] = "FALLO"
            status_rank = 2
            reasons.append(f"Posicion fuera de tolerancia ({deviation_units:.3f}).")
        score_parts.append(
            float(np.clip(1.0 - deviation_radii / (options["position_review_radii"] * 1.2), 0.0, 1.0)) * 100.0
        )

    color = classify_color(
        hsv, found_x, found_y, radius_px, options["brass_saturation"], item.get("color_reach", 1.3)
    )
    expected_brass = item["type"] in TYPE_BRASS or item.get("material") == "LATON"
    if options["check_type"]:
        observed_brass = color["observed"] == "LATON"
        if observed_brass == expected_brass:
            checks["type"] = "OK"
            score_parts.append(100.0)
        else:
            checks["type"] = "FALLO"
            status_rank = 2
            reasons.append(
                f"Tipo incorrecto: se esperaba {'laton' if expected_brass else 'acero'} "
                f"y se observa {color['observed'].lower()}."
            )
            score_parts.append(0.0)

    measured_radius, consistency = measure_radius(gray, found_x, found_y, radius_px)
    radius_ratio: float | None = None
    if measured_radius is not None:
        radius_ratio = measured_radius / radius_px
    if radius_ratio is not None and item.get("radii"):
        # Features con varios anillos (cono): se compara con el anillo mas cercano.
        ratios = [measured_radius / (r * scale) for r in item["radii"] if r > 0]
        radius_ratio = min(ratios, key=lambda value: abs(value - 1.0))
    if (
        options["check_diameter"]
        and radius_ratio is not None
        and consistency >= 0.5
        and item.get("radius_reliable", True)
    ):
        if abs(radius_ratio - 1.0) > options["diameter_review_ratio"]:
            checks["diameter"] = "REVISAR"
            status_rank = max(status_rank, 1)
            reasons.append(f"Diametro medido {radius_ratio * 100:.0f}% del esperado.")
        else:
            checks["diameter"] = "OK"

    edge: dict[str, float] | None = None
    if options["check_edges"] and edge_score_fn is not None:
        edge = edge_score_fn(
            int(round(expected_x)), int(round(expected_y)),
            int(round(found_x - expected_x)), int(round(found_y - expected_y)),
            int(round(radius_px)),
        )
        score_parts.append(float(edge["score"]))

    score = float(np.mean(score_parts)) if score_parts else 0.0
    status = ("OK", "REVISAR", "FALLO")[status_rank]

    result.update({
        "status": status,
        "score": round(score, 2),
        "found": True,
        "presence": round(presence, 3),
        "center_x_px": int(round(found_x)),
        "center_y_px": int(round(found_y)),
        "center_x_subpx": round(found_x, 2),
        "center_y_subpx": round(found_y, 2),
        "deviation": round(deviation_units, 4),
        "deviation_px": round(deviation_px, 2),
        "measured_radius_px": None if measured_radius is None else round(measured_radius, 2),
        "radius_ratio": None if radius_ratio is None else round(radius_ratio, 3),
        "color": color,
        "chamfer": None if edge is None else round(edge["chamfer"], 2),
        "coverage": None if edge is None else round(edge["coverage"], 2),
        "reasons": reasons,
        "checks": checks,
        # Compatibilidad con el formato anterior.
        "global_center_x_px": int(round(expected_x)),
        "global_center_y_px": int(round(expected_y)),
        "local_shift_x": int(round(found_x - expected_x)),
        "local_shift_y": int(round(found_y - expected_y)),
    })
    return result


# ============================================================
# DIBUJO
# ============================================================

STATUS_COLOR = {"OK": (0, 200, 0), "REVISAR": (0, 165, 255), "FALLO": (0, 0, 255)}


def draw_results(canvas_image: np.ndarray, results: list[dict[str, Any]]) -> np.ndarray:
    output = canvas_image.copy()
    height, width = output.shape[:2]
    for item in results:
        color = STATUS_COLOR[item["status"]]
        kind = item.get("kind")
        if kind == "contour":
            if item.get("expected_polygon"):
                cv2.polylines(output, [np.int32(item["expected_polygon"])], True, (255, 255, 0), 2, cv2.LINE_AA)
            if item.get("polygon"):
                cv2.polylines(output, [np.int32(item["polygon"])], True, color, 3, cv2.LINE_AA)
            continue
        if kind == "label":
            x1, y1, x2, y2 = item["box"]
            cv2.rectangle(output, (x1, y1), (x2, y2), color, 3, cv2.LINE_AA)
            cv2.putText(output, f"ETIQUETA {item['status']}", (x1, max(15, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)
            continue
        if kind == "distances":
            continue
        cx, cy = item["center_x_px"], item["center_y_px"]
        radius = int(round(item["expected_radius_px"]))
        x1, y1 = max(0, cx - radius), max(0, cy - radius)
        x2, y2 = min(width - 1, cx + radius), min(height - 1, cy + radius)
        cv2.rectangle(output, (x1, y1), (x2, y2), color, 3, cv2.LINE_AA)
        cv2.drawMarker(output, (cx, cy), color, cv2.MARKER_CROSS, 12, 2, cv2.LINE_AA)

        ex, ey = int(round(item["expected_x_px"])), int(round(item["expected_y_px"]))
        if item.get("found") and (ex, ey) != (cx, cy):
            cv2.line(output, (ex, ey), (cx, cy), (255, 255, 0), 2, cv2.LINE_AA)
            cv2.circle(output, (ex, ey), 3, (255, 0, 255), -1, cv2.LINE_AA)

        deviation = item.get("deviation")
        label = f"{item['id']} {item['type']} {item['status']} {item['score']:.0f}"
        if deviation is not None:
            label += f" d={deviation:.2f}"
        size, baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        label_y = max(size[1] + 5, y1 - 5)
        label_right = min(width - 1, x1 + size[0] + 8)
        cv2.rectangle(output, (x1, label_y - size[1] - 5), (label_right, label_y + baseline + 3), color, -1)
        cv2.putText(output, label, (x1 + 4, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (255, 255, 255), 1, cv2.LINE_AA)
    return output


def summarize(results: list[dict[str, Any]]) -> tuple[float, str, dict[str, int]]:
    summary = {"OK": 0, "REVISAR": 0, "FALLO": 0}
    for item in results:
        summary[item["status"]] += 1
    if not results:
        return 0.0, "FALLO", summary
    score = sum(float(item["score"]) for item in results) / len(results)
    if summary["FALLO"]:
        status = "FALLO"
    elif summary["REVISAR"]:
        status = "REVISAR"
    else:
        status = "OK"
    return round(score, 2), status, summary


# ============================================================
# ENTRADA PRINCIPAL
# ============================================================

def run_pipeline(
    image: np.ndarray,
    items: list[dict[str, Any]],
    bounds: tuple[float, float, float, float],
    options: dict[str, Any] | None = None,
    template_fn: Callable[[dict[str, Any]], np.ndarray | None] | None = None,
    golden: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """items: [{id, type, x, y, radius, material?}] en unidades de referencia.

    bounds: (xmin, xmax, ymin, ymax) del panel en las mismas unidades.
    template_fn(canvas) -> imagen binaria de bordes esperados en el canvas
    rectificado (opcional, solo para comparar bordes).
    """
    options = merge_options(options)
    items = [item for item in items if item.get("enabled", True)]
    if golden is not None and not (golden.get("enabled") or {}).get("features", True):
        items = []
    if not items and golden is None:
        raise ValueError("No hay features de referencia.")
    if len(items) < 4:
        raise ValueError(
            "Se necesitan al menos 4 features activos para alinear la pieza con la referencia."
        )

    image_height, image_width = image.shape[:2]
    ref_points = np.float64([[item["x"], -item["y"]] for item in items])
    candidates = detect_blob_candidates(image)
    extent = (bounds[1] - bounds[0], bounds[3] - bounds[2])
    registration = register(ref_points, candidates, (image_width, image_height), extent)
    if registration is None or registration["inliers"] < 4:
        raise ValueError(
            "No fue posible alinear la pieza: se detectaron muy pocos features "
            "coincidentes con la referencia. Verifique encuadre, enfoque e iluminacion."
        )

    canvas = build_canvas(bounds)
    # canvas <- imagen: canvas_from_ref * ref_from_image(inv(H))
    homography_image_from_ref = registration["matrix"]
    canvas_from_image = canvas["matrix"] @ np.linalg.inv(homography_image_from_ref)
    rectified = cv2.warpPerspective(
        image, canvas_from_image, (canvas["width"], canvas["height"]),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0),
    )
    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(rectified, cv2.COLOR_BGR2HSV)

    edge_score_fn = None
    template = template_fn(canvas) if template_fn is not None and options["check_edges"] else None
    if template is not None:
        edge_score_fn = _make_edge_scorer(rectified, template)

    results = [evaluate_feature(item, canvas, gray, hsv, options, edge_score_fn) for item in items]
    feature_results = list(results)
    if golden is not None:
        from .golden import run_extra_checks  # import perezoso: golden usa este modulo

        results = results + run_extra_checks(rectified, canvas, golden, items, feature_results, options)
    score, status, summary = summarize(results)
    result_image = draw_results(rectified, results)

    matches = []
    for ref_index, cand_index, distance in registration["pairs"]:
        point = candidates[cand_index]
        matches.append({
            "roi_id": items[ref_index]["id"],
            "detected_x": round(point["x"], 2),
            "detected_y": round(point["y"], 2),
            "displacement": round(distance, 2),
        })

    debug = image.copy()
    for point in candidates:
        cv2.circle(debug, (int(point["x"]), int(point["y"])), 2, (255, 0, 0), -1)
    projected = _apply(homography_image_from_ref, ref_points)
    for item, (px, py) in zip(items, projected):
        cv2.circle(debug, (int(px), int(py)), 12, (0, 255, 0), 1, cv2.LINE_AA)
        cv2.putText(debug, str(item["id"]), (int(px) + 8, int(py) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1, cv2.LINE_AA)

    overlay = rectified.copy()
    if template is not None:
        tint = np.zeros_like(rectified)
        tint[:, :, 2] = template
        overlay = cv2.addWeighted(rectified, 0.8, tint, 0.85, 0)

    return {
        "rectified": rectified,
        "result_image": result_image,
        "overlay": overlay,
        "debug": debug,
        "results": results,
        "score": score,
        "status": status,
        "summary": summary,
        "alignment": {
            "method": registration["method"],
            "inliers": registration["inliers"],
            "matches": matches,
        },
        "canvas": canvas,
        "options": options,
    }


def _make_edge_scorer(rectified: np.ndarray, template: np.ndarray):
    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    enhanced = cv2.bilateralFilter(enhanced, 7, 40, 40)
    edges = cv2.Canny(enhanced, 40, 120)
    distance_map = cv2.distanceTransform(np.where(edges > 0, 0, 255).astype(np.uint8), cv2.DIST_L2, 3)
    expanded = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    max_distance = 12.0

    def score(ex: int, ey: int, dx: int, dy: int, radius: int) -> dict[str, float]:
        # El template (en la posicion esperada) se compara contra los bordes
        # reales desplazados al centro detectado.
        height, width = template.shape[:2]
        pad = radius + 6
        x1, y1 = max(0, ex - pad), max(0, ey - pad)
        x2, y2 = min(width, ex + pad + 1), min(height, ey + pad + 1)
        points = np.argwhere(template[y1:y2, x1:x2] > 0)
        if len(points) < 5:
            return {"score": 0.0, "chamfer": 0.0, "coverage": 0.0}
        rows = np.clip(points[:, 0] + y1 + dy, 0, height - 1)
        cols = np.clip(points[:, 1] + x1 + dx, 0, width - 1)
        distances = distance_map[rows, cols]
        chamfer = (1.0 - float(np.mean(np.clip(distances, 0, max_distance))) / max_distance) * 100.0
        coverage = float(np.mean(expanded[rows, cols] > 0)) * 100.0
        return {"score": chamfer * 0.55 + coverage * 0.45, "chamfer": chamfer, "coverage": coverage}

    return score
