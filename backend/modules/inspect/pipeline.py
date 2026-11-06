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
    # Compuertas de calidad: si no se cumplen el resultado es NO CONCLUYENTE.
    "min_inlier_fraction": 0.6,
    "min_visible_fraction": 0.97,    # fraccion minima de la silueta que debe verse en la foto
    "frame_margin": 0.005,           # tolerancia (fraccion del ancho) para features pegados al borde
    "min_sharpness": 25.0,
    "min_brightness": 40.0,
    "max_brightness": 215.0,
    "max_clipped_fraction": 0.15,
}


class InconclusiveError(ValueError):
    """La imagen no permite decidir; hay que repetir la captura."""


def check_fits_frame(
    matrix: np.ndarray,
    items: list[dict[str, Any]],
    anchor_index: list[int],
    bounds: tuple[float, float, float, float],
    golden: dict[str, Any] | None,
    image_size: tuple[int, int],
    options: dict[str, Any],
) -> None:
    """La pieza debe verse completa, sea cual sea su posicion o giro en la mesa.

    Se evalua la silueta real de la pieza (no la caja que la envuelve, que en una pieza girada
    cae fuera de la foto aunque la pieza se vea entera): todos los features deben quedar dentro
    del encuadre y la silueta proyectada debe quedar casi toda dentro (`min_visible_fraction`).
    """
    image_width, image_height = image_size
    margin = options["frame_margin"] * image_width
    low, high = np.array([-margin, -margin]), np.array([image_width + margin, image_height + margin])
    points = _apply(matrix, np.float64([[items[i]["x"], -items[i]["y"]] for i in anchor_index]))
    if np.any(points < low) or np.any(points > high):
        raise InconclusiveError("La pieza no cabe completa en el encuadre. Centre la pieza o aleje la camara.")

    contour = (golden or {}).get("contour")
    if contour and len(contour) >= 3:
        polygon = _apply(matrix, np.float64([[x, -y] for x, y in contour]))
    else:
        polygon = _apply(matrix, np.float64([
            [bounds[0], -bounds[3]], [bounds[1], -bounds[3]], [bounds[1], -bounds[2]], [bounds[0], -bounds[2]],
        ]))
    # Mascara de la silueta con un lienzo mayor que la foto: lo que sale del encuadre se mide.
    pad = int(0.5 * max(image_width, image_height))
    shifted = (polygon + pad).astype(np.int32)
    if shifted.min() < 0 or shifted.max() > 32000:
        raise InconclusiveError("La pieza no cabe completa en el encuadre. Centre la pieza o aleje la camara.")
    total = np.zeros((int(image_height + 2 * pad), int(image_width + 2 * pad)), np.uint8)
    cv2.fillPoly(total, [shifted], 1)
    visible = float(total[pad:pad + image_height, pad:pad + image_width].sum()) / max(float(total.sum()), 1.0)
    if visible < options["min_visible_fraction"]:
        raise InconclusiveError(
            f"La pieza no cabe completa en el encuadre (se ve el {visible * 100:.0f}%). "
            "Centre la pieza o aleje la camara."
        )


def check_image_quality(image: np.ndarray, options: dict[str, Any]) -> dict[str, float]:
    """Nitidez y exposicion. Lanza InconclusiveError si no son aptas."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if gray.shape[1] > 1024:
        factor = 1024.0 / gray.shape[1]
        gray = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    clipped = float(np.mean((gray >= 250) | (gray <= 5)))
    if brightness < options["min_brightness"]:
        raise InconclusiveError(f"Imagen muy oscura (brillo {brightness:.0f}). Revise la iluminacion.")
    if brightness > options["max_brightness"]:
        raise InconclusiveError(f"Imagen sobreexpuesta (brillo {brightness:.0f}). Reduzca la exposicion.")
    if sharpness < options["min_sharpness"]:
        raise InconclusiveError(
            f"Imagen desenfocada (nitidez {sharpness:.0f} < {options['min_sharpness']:.0f}). Revise el enfoque."
        )
    if clipped > options["max_clipped_fraction"]:
        raise InconclusiveError(
            f"Demasiados pixeles saturados ({clipped * 100:.0f}%). Reduzca reflejos o exposicion."
        )
    return {"sharpness": round(sharpness, 1), "brightness": round(brightness, 1), "clipped": round(clipped, 4)}

TYPE_BRASS = {"DELRON_CONICO", "DELRON_PLANO"}


# Fraccion del tamano de la pieza (lado mayor) desde la cual un barreno es un "hueco grande".
LARGE_HOLE_FRACTION = 0.025
# Features necesarios para alinear la foto con la pieza patron.
MIN_ANCHORS = 4


def is_anchor(item: dict[str, Any], extent: float) -> bool:
    """Los huecos grandes no son puntos detectables (no son blobs): no sirven para alinear."""
    return not (item["type"] == "BARRENO" and float(item["radius"]) >= LARGE_HOLE_FRACTION * extent)


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
    # El lado mayor mide RECTIFIED_WIDTH: una tira angosta no se amplia de mas (y el lienzo no se dispara).
    scale = RECTIFIED_WIDTH / max(width_units, height_units)
    width_px = int(round(width_units * scale))
    height_px = int(round(height_units * scale))
    # Referencia (x, -y) -> canvas.
    matrix = np.array(
        [[scale, 0.0, -xmin * scale], [0.0, scale, ymax * scale], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    return {
        "scale": scale,
        "width": width_px,
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


def locate_large_hole(
    gray: np.ndarray, cx: float, cy: float, radius_px: float, search_px: float
) -> tuple[float, float, float, float] | None:
    """Centro y radio de un hueco grande por ajuste de circulo a su borde.

    El LoG local no sirve aqui: el interior es suave y el maximo se desplaza.
    Devuelve (x, y, presencia 0-1, radio) o None. La presencia es la fraccion
    de direcciones cuyo borde cae sobre el circulo ajustado.
    """
    sigma = max(1.5, 0.03 * radius_px)
    blurred = cv2.GaussianBlur(gray, (0, 0), sigma).astype(np.float32)
    height, width = gray.shape[:2]
    angles = np.linspace(0, 2 * math.pi, 90, endpoint=False)
    center = np.array([cx, cy], dtype=np.float64)
    radius = float(radius_px)
    inliers_fraction = 0.0
    for _ in range(4):
        radii = np.arange(0.6 * radius, 1.5 * radius, max(0.5, radius / 120.0))
        points = []
        for angle in angles:
            xs = center[0] + radii * math.cos(angle)
            ys = center[1] + radii * math.sin(angle)
            valid = (xs >= 0) & (xs < width - 1) & (ys >= 0) & (ys < height - 1)
            if valid.sum() < 8:
                continue
            profile = cv2.remap(
                blurred, xs[valid].astype(np.float32).reshape(1, -1),
                ys[valid].astype(np.float32).reshape(1, -1), cv2.INTER_LINEAR,
            ).ravel()
            index = int(np.argmax(np.abs(np.gradient(profile))))
            points.append((xs[valid][index], ys[valid][index]))
        if len(points) < 24:
            return None
        pts = np.array(points, dtype=np.float64)
        keep = np.ones(len(pts), dtype=bool)
        for _ in range(3):
            a = np.c_[2 * pts[keep, 0], 2 * pts[keep, 1], np.ones(keep.sum())]
            b = (pts[keep] ** 2).sum(axis=1)
            solution, *_ = np.linalg.lstsq(a, b, rcond=None)
            fit_center = solution[:2]
            fit_radius = math.sqrt(max(solution[2] + fit_center @ fit_center, 1e-9))
            residual = np.abs(np.hypot(*(pts - fit_center).T) - fit_radius)
            keep = residual < max(2.0, 0.04 * fit_radius)
            if keep.sum() < 16:
                return None
        center, radius = fit_center, fit_radius
        inliers_fraction = float(keep.sum()) / len(angles)
    if math.hypot(center[0] - cx, center[1] - cy) > search_px or abs(radius / radius_px - 1.0) > 0.5:
        return None
    return float(center[0]), float(center[1]), float(np.clip(inliers_fraction, 0.0, 1.0)), float(radius)


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


# Tipos que el clasificador de forma sabe distinguir.
FEATURE_TYPES = ("INSERTO", "DELRON_CONICO", "DELRON_PLANO", "BARRENO")
# Delron conico: la cara inclinada del cono se ve saturada (dorada) junto al
# agujero y pierde saturacion hacia el borde. Se evalua Sin + (Sin - Sout).
CONE_SCORE_MIN = 54.0
# Inserto: disco gris claramente mas oscuro que la placa alrededor.
INSERT_DISK_RATIO_MAX = 0.79
# Barreno liso: hueco sin disco; fuera del borde del hueco ya no hay otro borde.
# Se mide el mayor gradiente entre 1.5 y 3.6 radios, relativo al borde del hueco
# (barrenos 0.01-0.02, delron plano >= 0.12, inserto >= 0.35).
BARRENO_RING_MAX = 0.07
# Cono tostado (camaras de alta resolucion como la BRIO): el cono mide mas que el barreno central, asi que
# las bandas 1.2-2.4 R siguen sobre la cara del cono y conservan color (sat 32-60). Un inserto es gris
# (sat <= 17 en esas bandas) y un delron plano es claro (disk_ratio alto).
CONE_TAN_SAT_MIN = 28.0
# Un hueco real es casi negro (interior/placa ~0.2); si el interior es gris medio
# el borde medido es el de un disco, no el de un hueco.
INSERT_INTERIOR_RATIO_MIN = 0.45
# Cono sin color (gris oscuro): el brillo interior SUBE de forma continua del centro al borde
# (embudo); un barreno liso es una meseta plana. Rampa = (brillo en 0.5-0.85 R - brillo en 0-0.3 R)
# / placa: conos grises 0.23-0.31, insertos <= 0.16, barrenos <= 0.05.
CONE_RAMP_MIN = 0.20
# Pendiente interior maxima (relativa al borde exterior) de un embudo; un escalon interior es de inserto.
CONE_MAX_INNER_STEP = 0.40
# Centro / placa maximo para aceptar un hueco (reales <= 0.4; manchas >= 0.69).
CENTER_DARK_MAX = 0.55
# Un barreno de este radio (px del canvas) o mayor es un hueco grande: no es
# oscuro (se ve el fondo a traves) y se verifica por su borde, no por el centro.
LARGE_HOLE_MIN_PX = 45.0
# Casquillo de laton con centro metalico claro: saturacion minima dentro y maxima en la placa vecina.
BRASS_CORE_SAT_MIN = 60.0
BRASS_PLATE_SAT_MAX = 25.0


def _radial_profile(
    channel: np.ndarray, cx: float, cy: float, radii: np.ndarray, quantile: float = 50.0
) -> np.ndarray:
    """Mediana angular por radio: robusta a reflejos y a las pestañas del inserto.

    `quantile` > 50 se usa para estimar la placa cuando el circulo puede salirse del panel
    (la mesa es mas oscura): un percentil alto ignora la parte que cae fuera."""
    angles = np.linspace(0, 2 * math.pi, 96, endpoint=False)
    xs = (cx + np.outer(radii, np.cos(angles))).astype(np.float32)
    ys = (cy + np.outer(radii, np.sin(angles))).astype(np.float32)
    values = cv2.remap(channel, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return np.median(values, axis=1) if quantile == 50.0 else np.percentile(values, quantile, axis=1)


def classify_feature_type(
    gray: np.ndarray, hsv: np.ndarray, cx: float, cy: float, radius_px: float
) -> dict[str, Any] | None:
    """Clasifica el feature; si el radio inicial se aleja del medido, repite
    la medicion con el radio medido (la resolucion del perfil depende de el)."""
    result = _classify_feature_type_once(gray, hsv, cx, cy, radius_px)
    if result is None:
        # Un inserto trae un punto oscuro central cuya subida es la primera del perfil; si con ella no
        # hay hueco valido se prueba con el siguiente borde (el del disco).
        result = _classify_feature_type_once(gray, hsv, cx, cy, radius_px, skip=1)
    if result is not None and abs(result["hole_radius_px"] / max(radius_px, 1e-6) - 1.0) > 0.25:
        refined = _classify_feature_type_once(gray, hsv, cx, cy, result["hole_radius_px"])
        if refined is not None:
            return refined
    return result


def _grey_cone(
    brightness: np.ndarray, radii: np.ndarray, gradient: np.ndarray, half: int, peak: float
) -> dict[str, float] | None:
    """Cono sin color evaluado en el borde mas fuerte del perfil.

    Un cono gris se ve como un embudo: negro en el centro, el brillo sube de forma continua y
    salta a la placa en el borde exterior, sin reborde mas alla. Se evalua aparte de la
    deteccion del "primer borde" porque, segun la escala, la rampa del embudo puede parecer un
    borde propio y confundir la medicion del radio.
    """
    index = int(np.argmax(gradient[:half]))
    edge = float(radii[index])
    if edge < 1.0:
        return None

    def band(low: float, high: float) -> float:
        mask = (radii >= low * edge) & (radii <= high * edge)
        return float(np.median(brightness[mask])) if mask.any() else float("nan")

    core, inner = band(0.0, 0.3), band(0.5, 0.85)
    plates = [v for v in (band(1.3, 2.0), band(2.0, 3.0), band(3.0, 4.5)) if np.isfinite(v)]
    if not plates or not np.isfinite(core) or not np.isfinite(inner):
        return None
    plate = max(plates)
    ramp = (inner - core) / max(plate, 1.0)
    if core > 0.30 * plate or inner > 0.70 * plate or ramp < CONE_RAMP_MIN:
        return None
    # La subida debe ser gradual: un inserto tiene un escalon brusco del nucleo oscuro a su disco.
    inside = np.abs(gradient[(radii >= 0.12 * edge) & (radii <= 0.85 * edge)])
    if inside.size and peak > 0 and float(inside.max()) > CONE_MAX_INNER_STEP * peak:
        return None
    beyond = np.abs(gradient[(radii >= 1.4 * edge) & (radii <= 3.6 * edge)])
    ring = float(beyond.max() / peak) if beyond.size and peak > 0 else 0.0
    if ring > 0.2:
        return None  # hay otro borde importante despues: es un disco/inserto, no un cono
    return {"edge": edge, "ramp": ramp, "ring": ring, "inner": inner / max(plate, 1.0)}


def _classify_feature_type_once(
    gray: np.ndarray, hsv: np.ndarray, cx: float, cy: float, radius_px: float, skip: int = 0
) -> dict[str, Any] | None:
    """INSERTO / DELRON_CONICO / DELRON_PLANO por el perfil radial.

    - Inserto: disco gris (con pestañas) mas oscuro que la placa, sin color.
    - Delron conico: cono saturado alrededor del agujero que se desvanece
      hacia el borde (a veces con aro brillante exterior).
    - Delron plano: anillo beige uniforme y ancho, sin gradiente.
    Todo se mide en multiplos del radio del agujero: no depende de la escala.
    Devuelve None si el centro no es un agujero oscuro (reflejo, mancha).
    """
    rmax = max(40.0, 10.0 * radius_px)
    step = rmax / 240.0
    radii = np.arange(0.0, rmax, step)
    gray_f = gray.astype(np.float32) if gray.dtype != np.float32 else gray
    sat = hsv[:, :, 1].astype(np.float32)
    brightness = _radial_profile(gray_f, cx, cy, radii)
    saturation = _radial_profile(sat, cx, cy, radii)

    # Borde del agujero: primera subida significativa del perfil; el radio es
    # el punto medio de esa subida (estable aunque el borde sea una rampa).
    # Ventana de suavizado proporcional al feature (no a los pixeles): asi el
    # resultado no cambia al reescalar la imagen al canvas de inspeccion.
    k = max(1, int(round(0.15 * radius_px / step)))
    smooth = np.convolve(np.pad(brightness, k, mode="edge"), np.ones(k) / k, mode="same")[k:-k]
    gradient = np.gradient(smooth, radii)
    # El borde del hueco se busca cerca del radio esperado: mas lejos hay bordes
    # ajenos (orilla de la placa, etiquetas) que no deben dominar la medicion.
    half = int(min(len(radii) // 2, np.searchsorted(radii, 2.5 * radius_px) + 1))
    if half < 4:
        return None
    peak = float(gradient[:half].max())
    if peak <= 0:
        return None
    grey = _grey_cone(brightness, radii, gradient, half, peak)
    if grey is not None:
        return {
            "observed_type": "DELRON_CONICO",
            "hole_radius_px": round(grey["edge"], 2),
            "cone_score": 0.0,
            "disk_ratio": 1.0,
            "ring_ratio": round(grey["ring"], 3),
            "interior_ratio": round(grey["inner"], 3),
            "ramp": round(grey["ramp"], 3),
            "rim_radius_px": round(grey["edge"], 2),
            "bore_radius_px": round(0.3 * grey["edge"], 2),
        }
    # Subidas candidatas del perfil (primero la mas cercana al centro). Un inserto tiene un punto
    # central pequeno cuya subida no es el borde del hueco: si da un radio ~0 se pasa a la siguiente.
    rises = [
        index for index in range(1, half - 1)
        if gradient[index] >= 0.25 * peak
        and gradient[index] >= gradient[index - 1]
        and gradient[index] >= gradient[index + 1]
    ]
    # Si el centro esta desplazado un par de pixeles, el borde mas fuerte (el del disco) es la ultima opcion.
    strongest = int(np.argmax(gradient[:half]))
    if strongest not in rises and 0 < strongest < half - 1:
        rises.append(strongest)
    rises = rises[skip:]
    hole = 0.0
    rise = None
    for rise in rises:
        # El borde puede ser un escalon doble (sombra + borde): se sigue mientras la
        # pendiente siga siendo significativa respecto al borde mas fuerte.
        end = rise
        while end < half - 1 and gradient[end] >= 0.2 * peak:
            end += 1
        darkest = int(np.argmin(smooth[:rise + 1]))  # un destello en el centro no cuenta como borde
        low = float(smooth[darkest])
        middle = (low + float(smooth[end])) / 2.0
        above = np.nonzero(smooth[darkest:end + 1] >= middle)[0]
        hole = float(radii[darkest + above[0]]) if len(above) else float(radii[rise])
        if hole >= 1.0:
            break
    if rise is None or hole < 1.0:
        return None

    def band(profile: np.ndarray, low: float, high: float) -> float:
        mask = (radii >= low * hole) & (radii <= high * hole)
        return float(np.median(profile[mask])) if mask.any() else float("nan")

    interior = band(brightness, 0.35, 0.85)
    center = band(brightness, 0.0, 0.4)
    disk = band(brightness, 1.6, 2.4)
    # Placa: la mayor de tres bandas lejanas. En paneles angostos la banda de 4.5-6 R puede caer
    # en la mesa (mas oscura); la mas brillante es la que sigue sobre el metal.
    plate = max(
        (value for value in (band(brightness, 3.8, 4.8), band(brightness, 4.8, 6.2), band(brightness, 6.2, 8.0))
         if np.isfinite(value)),
        default=float("nan"),
    )
    sat_in = band(saturation, 1.15, 1.45)
    sat_out = band(saturation, 2.0, 2.4)
    if not all(np.isfinite([interior, center, disk, plate, sat_in, sat_out])):
        return None
    if center > 0.85 * disk or center > CENTER_DARK_MAX * plate:
        # El centro de un hueco es claramente mas oscuro que la placa. Excepcion: casquillo de laton
        # con centro metalico claro (color latonado dentro, placa sin color alrededor) = delron conico.
        if band(saturation, 0.0, 0.8) >= BRASS_CORE_SAT_MIN and band(saturation, 2.0, 3.0) <= BRASS_PLATE_SAT_MAX:
            return {
                "observed_type": "DELRON_CONICO", "hole_radius_px": round(hole, 2), "cone_score": 0.0,
                "disk_ratio": 1.0, "ring_ratio": 0.0, "interior_ratio": 1.0, "ramp": 0.0,
                "rim_radius_px": round(hole, 2), "bore_radius_px": round(0.3 * hole, 2),
            }
        return None

    # Borde mas fuerte dentro del hueco vs. borde mas fuerte del entorno cercano.
    fine = np.arange(0.0, 4.0 * hole + 1.0, max(hole / 40.0, 0.25))
    fine_profile = _radial_profile(gray_f, cx, cy, fine)
    smooth_fine = np.convolve(np.pad(fine_profile, 2, mode="edge"), np.ones(5) / 5, mode="same")[2:-2]
    fine_gradient = np.abs(np.gradient(smooth_fine, fine))
    inner_peak = float(fine_gradient[fine <= 1.3 * hole].max())
    outer = fine_gradient[(fine >= 1.5 * hole) & (fine <= 3.6 * hole)]
    ring_ratio = float(outer.max() / inner_peak) if len(outer) and inner_peak > 0 else 0.0
    # Radio del borde exterior (cabeza del delron / disco del inserto): el mayor gradiente tras el hueco.
    outer_radii = fine[(fine >= 1.5 * hole) & (fine <= 3.6 * hole)]
    rim_radius = float(outer_radii[int(np.argmax(outer))]) if len(outer) and ring_ratio >= 0.12 else None

    cone_score = sat_in + (sat_in - sat_out)
    disk_ratio = disk / max(plate, 1.0)
    interior_ratio = interior / max(plate, 1.0)
    core = band(brightness, 0.0, 0.3)
    ramp = (band(brightness, 0.5, 0.85) - core) / max(plate, 1.0)
    grey_cone = False
    tan_ring = min(band(saturation, 1.2, 1.5), band(saturation, 1.5, 2.0), band(saturation, 2.0, 2.4))
    if cone_score >= CONE_SCORE_MIN:
        observed = "DELRON_CONICO"
    elif tan_ring >= CONE_TAN_SAT_MIN and disk_ratio <= INSERT_DISK_RATIO_MAX:
        observed = "DELRON_CONICO"
    elif interior_ratio >= INSERT_INTERIOR_RATIO_MIN:
        # Interior gris medio (no negro): el "hueco" medido es el borde de un
        # disco metalico con un punto oscuro central, es decir un inserto.
        observed = "INSERTO"
    elif ramp >= CONE_RAMP_MIN and ring_ratio <= 0.2:
        # Cono sin tono dorado: el borde medido es el borde exterior del cono y el
        # centro oscuro es su barreno.
        observed = "DELRON_CONICO"
        grey_cone = True
    elif ring_ratio <= BARRENO_RING_MAX and disk_ratio > INSERT_DISK_RATIO_MAX:
        observed = "BARRENO"
    elif disk_ratio <= INSERT_DISK_RATIO_MAX:
        observed = "INSERTO"
    else:
        observed = "DELRON_PLANO"
    if observed in ("DELRON_PLANO", "INSERTO") and interior_ratio < 0.3:
        # Interior casi negro y "anillo" fuerte: puede que el borde medido fuera
        # un escalon (sombra) dentro del mismo hueco. Se busca donde el perfil
        # supera la mitad del nivel del entorno inmediato y se vuelve a medir.
        near = (fine >= 1.2 * hole) & (fine <= 2.2 * hole)
        level = float(smooth_fine[near].max()) if near.any() else 0.0
        crossing = np.nonzero(smooth_fine >= 0.5 * level)[0]
        wide = float(fine[crossing[0]]) if len(crossing) else hole
        if hole * 1.05 < wide <= 1.8 * hole:
            wide_peak = float(fine_gradient[fine <= 1.3 * wide].max())
            wide_outer = fine_gradient[(fine >= 1.5 * wide) & (fine <= 3.6 * wide)]
            wide_ratio = float(wide_outer.max() / wide_peak) if len(wide_outer) and wide_peak > 0 else 1.0
            if wide_ratio <= BARRENO_RING_MAX:
                observed, hole, ring_ratio = "BARRENO", wide, wide_ratio
    return {
        "observed_type": observed,
        "hole_radius_px": round(hole, 2),
        "cone_score": round(cone_score, 1),
        "disk_ratio": round(disk_ratio, 3),
        "ring_ratio": round(ring_ratio, 3),
        "interior_ratio": round(interior_ratio, 3),
        "ramp": round(ramp, 3),
        "rim_radius_px": (
            round(hole, 2) if grey_cone
            else None if rim_radius is None or observed == "BARRENO" else round(rim_radius, 2)
        ),
        # Radio del barreno central: en conos grises es el nucleo oscuro, no el borde medido.
        "bore_radius_px": round(0.3 * hole, 2) if grey_cone else round(hole, 2),
    }


def classify_large_hole(
    gray: np.ndarray, hsv: np.ndarray, cx: float, cy: float, radius_px: float
) -> dict[str, Any] | None:
    """Verifica un hueco grande (se ve el fondo a traves): borde circular continuo
    y interior distinto del metal. Devuelve None si no hay tal hueco."""
    radii = np.arange(0.0, 1.6 * radius_px, max(radius_px / 60.0, 0.5))
    gray_f = gray.astype(np.float32) if gray.dtype != np.float32 else gray
    brightness = _radial_profile(gray_f, cx, cy, radii)
    saturation = _radial_profile(hsv[:, :, 1].astype(np.float32), cx, cy, radii)

    measured, consistency = measure_radius(gray, cx, cy, radius_px)
    if measured is None or consistency < 0.5 or abs(measured / radius_px - 1.0) > 0.35:
        return None

    def band(profile: np.ndarray, low: float, high: float) -> float:
        mask = (radii >= low * measured) & (radii <= high * measured)
        return float(np.median(profile[mask])) if mask.any() else float("nan")

    inside = band(brightness, 0.0, 0.8)
    outside = band(brightness, 1.15, 1.5)
    sat_in = band(saturation, 0.0, 0.8)
    sat_out = band(saturation, 1.15, 1.5)
    if not all(np.isfinite([inside, outside, sat_in, sat_out])):
        return None
    contrast = abs(inside - outside) / max(outside, 1.0)
    sat_shift = abs(sat_in - sat_out)
    if contrast < 0.12 and sat_shift < 25.0:
        return None  # interior igual al metal: no es un hueco
    return {
        "observed_type": "BARRENO",
        "hole_radius_px": round(measured, 2),
        "contrast": round(contrast, 3),
        "edge_consistency": round(consistency, 2),
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

    large_hole = item["type"] == "BARRENO" and radius_px >= LARGE_HOLE_MIN_PX
    fitted_radius: float | None = None
    if large_hole:
        fit = locate_large_hole(gray, expected_x, expected_y, radius_px, search_px)
        located = None if fit is None else fit[:3]
        fitted_radius = None if fit is None else fit[3]
    else:
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

    # Con varias piezas buenas, la tolerancia nunca baja de 3 / 5 sigmas observadas.
    sigma_radii = float(item.get("pos_std") or 0.0) * scale / radius_px
    ok_limit = max(options["position_ok_radii"], 3.0 * sigma_radii)
    review_limit = max(options["position_review_radii"], 5.0 * sigma_radii)

    if options["check_position"]:
        if deviation_radii <= ok_limit:
            checks["position"] = "OK"
        elif deviation_radii <= review_limit:
            checks["position"] = "REVISAR"
            status_rank = max(status_rank, 1)
            reasons.append(f"Posicion desviada {deviation_units:.3f}.")
        else:
            checks["position"] = "FALLO"
            status_rank = 2
            reasons.append(f"Posicion fuera de tolerancia ({deviation_units:.3f}).")
        score_parts.append(
            float(np.clip(1.0 - deviation_radii / (review_limit * 1.2), 0.0, 1.0)) * 100.0
        )

    color = classify_color(
        hsv, found_x, found_y, radius_px, options["brass_saturation"], item.get("color_reach", 1.3)
    )
    expected_brass = item["type"] in TYPE_BRASS or item.get("material") == "LATON"
    shape = None
    if large_hole:
        shape = classify_large_hole(gray, hsv, found_x, found_y, fitted_radius or radius_px)
    elif item["type"] in FEATURE_TYPES:
        shape = classify_feature_type(gray, hsv, found_x, found_y, radius_px)
    if options["check_type"] and shape is not None:
        if shape["observed_type"] == item["type"]:
            checks["type"] = "OK"
            score_parts.append(100.0)
        else:
            checks["type"] = "FALLO"
            status_rank = 2
            reasons.append(
                f"Tipo incorrecto: se esperaba {item['type'].replace('_', ' ').lower()} "
                f"y se observa {shape['observed_type'].replace('_', ' ').lower()}."
            )
            score_parts.append(0.0)
    elif options["check_type"] and item["type"] in FEATURE_TYPES:
        checks["type"] = "REVISAR"
        status_rank = max(status_rank, 1)
        reasons.append("No se pudo determinar el tipo (inserto / delron).")
    elif options["check_type"]:
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

    if fitted_radius is not None:
        measured_radius, consistency = fitted_radius, presence
    else:
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
        "observed_type": None if shape is None else shape["observed_type"],
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

    quality = check_image_quality(image, options)
    image_height, image_width = image.shape[:2]
    ref_points = np.float64([[item["x"], -item["y"]] for item in items])
    candidates = detect_blob_candidates(image)
    extent = (bounds[1] - bounds[0], bounds[3] - bounds[2])
    # Solo los features pequenos alinean; los huecos grandes se miden despues, ya alineados.
    anchor_index = [i for i, item in enumerate(items) if is_anchor(item, max(extent))] or list(range(len(items)))
    registration = register(ref_points[anchor_index], candidates, (image_width, image_height), extent)
    if registration is not None:
        registration["pairs"] = [(anchor_index[r], c, d) for r, c, d in registration["pairs"]]
    if registration is None or registration["inliers"] < 4:
        raise InconclusiveError(
            "No fue posible alinear la pieza: se detectaron muy pocos features "
            "coincidentes con la referencia. Verifique encuadre, enfoque e iluminacion."
        )
    inlier_fraction = registration["inliers"] / len(anchor_index)
    if inlier_fraction < options["min_inlier_fraction"]:
        raise InconclusiveError(
            f"Alineacion poco confiable: solo {registration['inliers']} de {len(anchor_index)} features "
            f"coinciden ({inlier_fraction * 100:.0f}% < {options['min_inlier_fraction'] * 100:.0f}%). "
            "Puede ser un encuadre incorrecto o una pieza distinta a la dada de alta."
        )
    check_fits_frame(registration["matrix"], items, anchor_index, bounds, golden, (image_width, image_height), options)

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
            "inlier_fraction": round(inlier_fraction, 3),
            "matches": matches,
            "quality": quality,
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
