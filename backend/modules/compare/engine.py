"""Motor del comparador optico general (sin base de datos ni HTTP).

Compara una foto de la pieza contra su pieza patron (golden) sin suponer que sea un panel:

1. Alinea la foto con el patron (puntos SIFT + RANSAC y afinado ECC): la pieza puede estar en otra
   posicion, giro o distancia, y el recorte no tiene que ser identico.
2. Normaliza la iluminacion (ganancia global y un campo suave de brillo) para no confundir luz con defecto.
3. Calcula tres mapas de diferencia tolerantes a un pequeno desplazamiento: color (Lab), borde que sobra
   y borde que falta.
4. Agrupa lo anomalo en regiones y de cada una explica que es distinto, donde esta y como se ve en el
   patron y en la pieza.

Todos los umbrales estan en `PRESETS` y en las constantes de arriba (ver README_COMPARADOR.md).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Parametros
# ---------------------------------------------------------------------------

WORK_SIDE = 1400                # lado mayor (px) con el que se compara
MIN_INPUT_SIDE = 80             # una foto o recorte mas chico no sirve
MIN_MATCHES = 14                # coincidencias RANSAC minimas para aceptar el alineamiento por puntos
MIN_COVERAGE = 0.80             # fraccion del patron que debe quedar cubierta por la foto alineada
MAX_EXCLUDED = 0.45             # fraccion maxima excluida (brillos saturados / sin dato) para poder decidir
SATURATED_L = 247.0             # L (0-255) desde la cual un pixel se considera reflejo/quemado
TOLERANCE_FRACTION = 0.0035     # desplazamiento admitido por el alineamiento, fraccion del lado mayor
NOISE_SIGMAS = 6.0              # umbral adaptativo: mediana + k * MAD del mapa de diferencias

# Cada preset: (dE minimo de color, fuerza minima de borde, area minima de region en % de la pieza,
#               % de area anomala para REVISAR, para FALLO). "estricto" es el valor por defecto.
PRESETS: dict[str, dict[str, float]] = {
    "estricto": {"delta_e": 9.0, "edge": 0.16, "min_area_pct": 0.012, "review_pct": 0.04, "fail_pct": 0.60},
    "normal": {"delta_e": 13.0, "edge": 0.24, "min_area_pct": 0.04, "review_pct": 0.15, "fail_pct": 1.50},
    "tolerante": {"delta_e": 19.0, "edge": 0.36, "min_area_pct": 0.12, "review_pct": 0.50, "fail_pct": 4.00},
}
DEFAULT_PRESET = "estricto"


class CompareInconclusive(ValueError):
    """La foto no permite decidir (no se alinea, esta borrosa, etc.): hay que repetirla."""


@dataclass
class Alignment:
    matrix: np.ndarray          # 3x3, foto (espacio de trabajo) -> patron
    method: str
    inliers: int
    matches: int
    ecc: float | None
    coverage: float


# ---------------------------------------------------------------------------
# Utilidades de imagen
# ---------------------------------------------------------------------------

def _fit(image: np.ndarray, side: int) -> tuple[np.ndarray, float]:
    """Reduce (nunca amplia) para que el lado mayor sea `side`. Devuelve (imagen, escala)."""
    longest = max(image.shape[:2])
    if longest <= side:
        return image, 1.0
    scale = side / longest
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), scale


def _gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _check_input(image: np.ndarray, name: str) -> None:
    if image is None or image.ndim != 3 or min(image.shape[:2]) < MIN_INPUT_SIDE:
        raise CompareInconclusive(f"La imagen {name} es demasiado pequeña para comparar (minimo {MIN_INPUT_SIDE} px por lado).")


def image_quality(image: np.ndarray) -> dict[str, float]:
    gray = _gray(_fit(image, 1024)[0])
    return {
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        "brightness": round(float(gray.mean()), 1),
        "contrast": round(float(gray.std()), 1),
        "clipped": round(float(np.mean((gray >= 250) | (gray <= 5))), 4),
    }


# ---------------------------------------------------------------------------
# Alineamiento
# ---------------------------------------------------------------------------

def _match_points(golden: np.ndarray, test: np.ndarray) -> tuple[np.ndarray | None, int, int]:
    """Homografia test -> golden por puntos SIFT. Devuelve (matriz, inliers, coincidencias)."""
    if hasattr(cv2, "SIFT_create"):
        detector, norm = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.02), cv2.NORM_L2
    else:  # pragma: no cover
        detector, norm = cv2.ORB_create(nfeatures=4000), cv2.NORM_HAMMING
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    kp_g, des_g = detector.detectAndCompute(clahe.apply(_gray(golden)), None)
    kp_t, des_t = detector.detectAndCompute(clahe.apply(_gray(test)), None)
    if des_g is None or des_t is None or len(kp_g) < 8 or len(kp_t) < 8:
        return None, 0, 0
    pairs = cv2.BFMatcher(norm).knnMatch(des_t, des_g, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return None, 0, len(good)
    src = np.float32([kp_t[m.queryIdx].pt for m in good])
    dst = np.float32([kp_g[m.trainIdx].pt for m in good])
    # Primero similitud (4 grados de libertad): es lo que mas se parece a "mover la pieza" y no deforma.
    similarity, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0, maxIters=4000)
    if similarity is None or mask is None:
        return None, 0, len(good)
    inliers = int(mask.sum())
    matrix = np.vstack([similarity, [0.0, 0.0, 1.0]])
    # Si hay muchas coincidencias se permite perspectiva (la camara no esta perfectamente perpendicular).
    if inliers >= 40:
        homography, hmask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if homography is not None and hmask is not None and int(hmask.sum()) >= 0.9 * inliers:
            corners = np.float32([[0, 0], [test.shape[1], 0], [test.shape[1], test.shape[0]], [0, test.shape[0]]])
            warped = cv2.perspectiveTransform(corners[None], homography)[0]
            area = abs(cv2.contourArea(warped))
            if area > 0 and 0.2 < area / (golden.shape[0] * golden.shape[1]) < 5.0 and cv2.isContourConvex(warped.astype(np.float32)):
                matrix, inliers = homography, int(hmask.sum())
    return matrix, inliers, len(good)


def _refine_ecc(golden: np.ndarray, test: np.ndarray, matrix: np.ndarray) -> tuple[np.ndarray, float | None]:
    """Afina la homografia minimizando la diferencia de gradientes (robusto a la luz)."""
    def edges(image: np.ndarray) -> np.ndarray:
        blurred = cv2.GaussianBlur(_gray(image).astype(np.float32), (0, 0), 1.6)
        gx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(blurred, cv2.CV_32F, 0, 1)
        return cv2.GaussianBlur(cv2.magnitude(gx, gy), (0, 0), 1.2)

    scale = min(1.0, 700.0 / max(golden.shape[:2]))
    small_g = edges(cv2.resize(golden, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
    small_t = edges(cv2.resize(test, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
    shrink = np.diag([scale, scale, 1.0])
    start = (shrink @ matrix @ np.linalg.inv(shrink)).astype(np.float32)
    # El warp de ECC va de la imagen de referencia (golden) a la otra (test): se pasa la inversa.
    try:
        inverse = np.linalg.inv(start).astype(np.float32)
        correlation, refined = cv2.findTransformECC(
            small_g, small_t, inverse, cv2.MOTION_HOMOGRAPHY,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), None, 5,
        )
    except cv2.error:
        return matrix, None
    forward = np.linalg.inv(refined)
    result = np.linalg.inv(shrink) @ forward @ shrink
    # Se descarta el afinado si se mueve demasiado (fue a un minimo falso).
    probe = np.float32([[0, 0], [test.shape[1], 0], [test.shape[1], test.shape[0]], [0, test.shape[0]]])[None]
    moved = cv2.perspectiveTransform(probe, matrix)[0] - cv2.perspectiveTransform(probe, result)[0]
    if float(np.abs(moved).max()) > 0.04 * max(golden.shape[:2]):
        return matrix, None
    return result / result[2, 2], float(correlation)


def align(golden: np.ndarray, test: np.ndarray) -> Alignment:
    """Alinea `test` con `golden` (ambas ya a tamano de trabajo)."""
    matrix, inliers, matches = _match_points(golden, test)
    method = "PUNTOS"
    if matrix is None or inliers < MIN_MATCHES:
        # Pieza con poca textura: se intenta a ciegas, asumiendo mismo encuadre aproximado.
        matrix = np.diag([golden.shape[1] / test.shape[1], golden.shape[0] / test.shape[0], 1.0])
        method = "ENCUADRE"
    refined, ecc = _refine_ecc(golden, test, matrix)
    if ecc is not None:
        matrix = refined
        method += "+ECC"
    elif method == "ENCUADRE":
        raise CompareInconclusive(
            "No fue posible alinear la foto con la pieza patron: se encontraron muy pocos puntos en comun. "
            "Verifique que sea la pieza correcta, el enfoque y la iluminacion, y que el recorte la incluya."
        )
    mask = _coverage_mask(golden, test, matrix)
    coverage = float(mask.mean())
    if method.startswith("ENCUADRE") and (ecc is None or ecc < 0.45):
        raise CompareInconclusive("La foto no coincide con la pieza patron (alineamiento poco confiable).")
    if method.startswith("PUNTOS") and ecc is None and inliers < 2 * MIN_MATCHES and coverage < 0.9:
        raise CompareInconclusive("Alineamiento poco confiable: pocas coincidencias con la pieza patron.")
    return Alignment(matrix, method, inliers, matches, ecc, coverage)


def _coverage_mask(golden: np.ndarray, test: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    ones = np.full(test.shape[:2], 255, np.uint8)
    warped = cv2.warpPerspective(ones, matrix, (golden.shape[1], golden.shape[0]), flags=cv2.INTER_NEAREST)
    return cv2.erode(warped, np.ones((5, 5), np.uint8)) > 0


# ---------------------------------------------------------------------------
# Mapas de diferencia
# ---------------------------------------------------------------------------

def _lab(image: np.ndarray) -> np.ndarray:
    """Lab en float32 con L, a, b en escala 0-255 (a y b centrados en 128)."""
    return cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)


def _normalize_light(golden_lab: np.ndarray, test_lab: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Iguala la luz de la foto con la del patron (ganancia global + campo suave de brillo)."""
    out = test_lab.copy()
    for channel in (1, 2):
        shift = float(np.median(golden_lab[..., channel][valid] - out[..., channel][valid]))
        out[..., channel] += float(np.clip(shift, -18, 18))
    # Brillo: campo de diferencia de L calculado en celdas grandes con mediana (un defecto chico no pesa).
    height, width = valid.shape
    cells_x, cells_y = 10, max(2, int(round(10 * height / width)))
    delta = (golden_lab[..., 0] - out[..., 0])
    delta = np.where(valid, delta, np.nan)
    small = cv2.resize(np.nan_to_num(delta, nan=0.0), (cells_x, cells_y), interpolation=cv2.INTER_AREA)
    count = cv2.resize(valid.astype(np.float32), (cells_x, cells_y), interpolation=cv2.INTER_AREA)
    medians = np.zeros((cells_y, cells_x), np.float32)
    for cy in range(cells_y):
        for cx in range(cells_x):
            ys = slice(cy * height // cells_y, (cy + 1) * height // cells_y)
            xs = slice(cx * width // cells_x, (cx + 1) * width // cells_x)
            block = delta[ys, xs]
            block = block[np.isfinite(block)]
            medians[cy, cx] = float(np.median(block)) if block.size > 50 else 0.0
    medians[count < 0.2] = float(np.median(medians[count >= 0.2])) if (count >= 0.2).any() else 0.0
    field = cv2.resize(cv2.GaussianBlur(medians, (3, 3), 0), (width, height), interpolation=cv2.INTER_CUBIC)
    out[..., 0] += np.clip(field, -30, 30)
    return out


def _envelope(channel: np.ndarray, radius: int) -> tuple[np.ndarray, np.ndarray]:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    return cv2.erode(channel, kernel), cv2.dilate(channel, kernel)


def _tolerant_color(golden_lab: np.ndarray, test_lab: np.ndarray, radius: int) -> tuple[np.ndarray, np.ndarray]:
    """dE de color tolerante a +-radius px. Devuelve (dE, signo de L: + la foto esta mas clara)."""
    deviation1 = np.zeros(golden_lab.shape[:2], np.float32)
    deviation2 = np.zeros_like(deviation1)
    sign = np.zeros_like(deviation1)
    weights = (0.85, 1.0, 1.0)
    for channel in range(3):
        g_min, g_max = _envelope(golden_lab[..., channel], radius)
        t_min, t_max = _envelope(test_lab[..., channel], radius)
        above = test_lab[..., channel] - g_max
        below = g_min - test_lab[..., channel]
        d1 = np.maximum(np.maximum(above, below), 0)
        d2 = np.maximum(np.maximum(golden_lab[..., channel] - t_max, t_min - golden_lab[..., channel]), 0)
        deviation1 += (weights[channel] * d1) ** 2
        deviation2 += (weights[channel] * d2) ** 2
        if channel == 0:
            sign = np.where(above > below, 1.0, -1.0).astype(np.float32)
    delta = np.sqrt(np.maximum(deviation1, deviation2))
    return cv2.GaussianBlur(delta, (0, 0), 1.0), sign


def _gradient(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (0, 0), 1.3)
    gx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(blurred, cv2.CV_32F, 0, 1)
    return cv2.magnitude(gx, gy)


def _edge_maps(
    golden_gray: np.ndarray, test_gray: np.ndarray, radius: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(borde que sobra, borde que falta, energia de borde del patron, de la foto), 0-1, tolerantes a +-radius px."""
    g, t = _gradient(golden_gray), _gradient(test_gray)
    scale = float(np.percentile(np.maximum(g, t), 99.0)) or 1.0
    g, t = g / scale, t / scale
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    extra = np.clip(t - cv2.dilate(g, kernel), 0, None)
    missing = np.clip(g - cv2.dilate(t, kernel), 0, None)
    return cv2.GaussianBlur(extra, (0, 0), 1.0), cv2.GaussianBlur(missing, (0, 0), 1.0), g, t


def _adaptive(values: np.ndarray, valid: np.ndarray, minimum: float) -> float:
    sample = values[valid]
    if sample.size == 0:
        return minimum
    median = float(np.median(sample))
    mad = float(np.median(np.abs(sample - median))) * 1.4826
    return max(minimum, median + NOISE_SIGMAS * mad)


# ---------------------------------------------------------------------------
# Descripcion de las regiones
# ---------------------------------------------------------------------------

_HUES = [(0, "rojo"), (11, "naranja"), (22, "amarillo"), (38, "verde amarillento"), (50, "verde"), (85, "turquesa"),
         (100, "azul claro"), (125, "azul"), (145, "violeta"), (165, "magenta"), (180, "rojo")]


def describe_color(bgr: np.ndarray) -> str:
    """Nombre aproximado del color medio (BGR 0-255)."""
    pixel = np.uint8([[np.clip(bgr, 0, 255)]])
    hue, sat, val = [int(v) for v in cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)[0, 0]]
    if val < 45:
        return "negro"
    if sat < 38:
        return "blanco" if val > 205 else "gris claro" if val > 150 else "gris" if val > 95 else "gris oscuro"
    name = next(label for limit, label in reversed(_HUES) if hue >= limit)
    tone = "oscuro" if val < 110 else "claro" if val > 200 and sat < 120 else ""
    return f"{name} {tone}".strip()


def _zone_name(cx: float, cy: float, width: int, height: int) -> str:
    column = "izquierda" if cx < width / 3 else "derecha" if cx > 2 * width / 3 else "centro"
    row = "arriba" if cy < height / 3 else "abajo" if cy > 2 * height / 3 else "centro"
    if row == "centro" and column == "centro":
        return "centro"
    if row == "centro":
        return column
    if column == "centro":
        return row
    return f"{row}-{column}"


def _severity(delta_max: float, area_pct: float, preset: dict[str, float]) -> str:
    ratio = delta_max / preset["delta_e"]
    if (ratio >= 3.0 and area_pct >= preset["min_area_pct"] * 3) or area_pct >= preset["fail_pct"] * 0.5:
        return "critica"
    if ratio >= 1.8 or area_pct >= preset["review_pct"]:
        return "moderada"
    return "leve"


def _local_shift(golden_gray: np.ndarray, test_gray: np.ndarray, box: tuple[int, int, int, int]) -> tuple[float, float, float]:
    """Desplazamiento local (dx, dy) de la foto respecto al patron en una ventana, y su confianza."""
    x, y, w, h = box
    pad = max(w, h) // 2 + 6
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(golden_gray.shape[1], x + w + pad), min(golden_gray.shape[0], y + h + pad)
    if x1 - x0 < 24 or y1 - y0 < 24:
        return 0.0, 0.0, 0.0
    a = golden_gray[y0:y1, x0:x1].astype(np.float32)
    b = test_gray[y0:y1, x0:x1].astype(np.float32)
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, window)
    return float(dx), float(dy), float(response)


def _explain(region: dict[str, Any]) -> tuple[str, list[str], str]:
    """(tipo, hechos, causa probable) en texto, a partir de las medidas de la region."""
    facts: list[str] = []
    g_name, t_name = region["golden_color"], region["test_color"]
    d_l = region["delta_l"]
    shift = region["shift_px"]
    kind = "diferencia"
    if region["glare"]:
        kind = "reflejo"
        facts.append("La zona es muy clara (casi quemada) en las dos fotos y cambia la cantidad de brillo.")
        cause = "Reflejo de luz, no necesariamente un defecto: repita la foto cambiando el angulo de la luz."
    elif region["chroma_change"] >= 18 and region["edge_extra"] + region["edge_missing"] < 3 * region["edge_threshold"]:
        kind = "color"
        facts.append(f"El color cambia de {g_name} (patron) a {t_name} (pieza).")
        cause = "Acabado, material o pintura distinta; oxidacion o mancha; pieza de otro lote."
    elif region["shifted"]:
        kind = "desplazado"
        facts.append(
            f"El detalle de esta zona esta presente en ambas pero movido {shift:.1f} px "
            f"({region['shift_dx']:+.1f}, {region['shift_dy']:+.1f}) respecto al patron."
        )
        cause = "Elemento fuera de posicion, pieza deformada o mal asentada en esa zona."
    elif region["edge_missing"] > 1.6 * region["edge_extra"] and region["edge_missing"] > 0.5 * region["edge_threshold"]:
        kind = "falta"
        facts.append("En el patron hay bordes/detalle en esta zona que en la pieza no estan.")
        cause = "Falta un elemento (agujero, marca, borde, tornillo) o esta cubierto/danado."
    elif region["edge_extra"] > 1.6 * region["edge_missing"] and region["edge_extra"] > 0.5 * region["edge_threshold"]:
        kind = "extra"
        facts.append("En la pieza hay bordes/detalle que el patron no tiene en esta zona.")
        cause = "Elemento o marca adicional, rayon, golpe, rebaba, suciedad u objeto sobre la pieza."
    elif region["chroma_change"] >= 12 and region["chroma_change"] > 1.5 * abs(d_l) * 0.6:
        kind = "color"
        facts.append(f"El color cambia de {g_name} (patron) a {t_name} (pieza).")
        cause = "Acabado, material o pintura distinta; oxidacion o mancha; pieza de otro lote."
    elif abs(d_l) >= 8:
        kind = "brillo"
        facts.append(
            f"La zona esta {'mas clara' if d_l > 0 else 'mas oscura'} en la pieza "
            f"({abs(d_l):.0f} de 255 de luminosidad) que en el patron: {g_name} -> {t_name}."
        )
        cause = (
            "Mancha, aceite, quemadura o sombra." if d_l < 0
            else "Desgaste, pulido, material mas claro o reflejo."
        )
    else:
        facts.append(f"La textura/estructura de la zona es distinta ({g_name} en el patron, {t_name} en la pieza).")
        cause = "Cambio de textura o acabado superficial."
    if kind not in ("desplazado", "brillo", "color") and abs(d_l) >= 8:
        facts.append(
            f"Ademas esta {'mas clara' if d_l > 0 else 'mas oscura'} ({abs(d_l):.0f} de 255): {g_name} -> {t_name}."
        )
    return kind, facts, cause


KIND_LABEL = {
    "desplazado": "Elemento desplazado", "falta": "Falta detalle", "extra": "Detalle adicional",
    "reflejo": "Posible reflejo", "color": "Cambio de color", "brillo": "Cambio de brillo", "diferencia": "Diferencia",
}


# ---------------------------------------------------------------------------
# Comparacion
# ---------------------------------------------------------------------------

def compare(
    golden_bgr: np.ndarray,
    test_bgr: np.ndarray,
    sensitivity: str = DEFAULT_PRESET,
    min_quality: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compara `test_bgr` contra `golden_bgr`. Devuelve el informe y las imagenes (numpy).

    Lanza CompareInconclusive si la foto no permite decidir.
    """
    preset = PRESETS.get(sensitivity, PRESETS[DEFAULT_PRESET])
    _check_input(golden_bgr, "patron")
    _check_input(test_bgr, "de la pieza")

    quality = image_quality(test_bgr)
    limits = {"sharpness": 12.0, "brightness_min": 28.0, "brightness_max": 232.0, "contrast": 6.0, "clipped": 0.35}
    limits.update(min_quality or {})
    if quality["brightness"] < limits["brightness_min"]:
        raise CompareInconclusive(f"Foto muy oscura (brillo {quality['brightness']:.0f}). Revise la iluminacion.")
    if quality["brightness"] > limits["brightness_max"]:
        raise CompareInconclusive(f"Foto sobreexpuesta (brillo {quality['brightness']:.0f}). Reduzca la exposicion.")
    if quality["sharpness"] < limits["sharpness"] or quality["contrast"] < limits["contrast"]:
        raise CompareInconclusive(f"Foto desenfocada o sin contraste (nitidez {quality['sharpness']:.0f}). Revise el enfoque.")
    if quality["clipped"] > limits["clipped"]:
        raise CompareInconclusive(f"Demasiados pixeles saturados ({quality['clipped'] * 100:.0f} %). Reduzca reflejos o exposicion.")

    golden, g_scale = _fit(golden_bgr, WORK_SIDE)
    # La foto se lleva a una resolucion de trabajo parecida a la del patron antes de alinear.
    test, t_scale = _fit(test_bgr, int(max(golden.shape[:2]) * 1.6))
    alignment = align(golden, test)
    if alignment.coverage < MIN_COVERAGE:
        raise CompareInconclusive(
            f"La foto solo cubre el {alignment.coverage * 100:.0f} % de la pieza patron. "
            "Recorte un area que incluya toda la zona del patron."
        )

    height, width = golden.shape[:2]
    warped = cv2.warpPerspective(test, alignment.matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    covered = _coverage_mask(golden, test, alignment.matrix)

    golden_lab, test_lab = _lab(cv2.GaussianBlur(golden, (0, 0), 0.8)), _lab(cv2.GaussianBlur(warped, (0, 0), 0.8))
    # Solo es "no evaluable" donde AMBAS fotos estan quemadas (no hay nada que comparar). Si solo una lo esta,
    # la diferencia es real (o de luz) y se compara.
    glare = (golden_lab[..., 0] >= SATURATED_L) & (test_lab[..., 0] >= SATURATED_L)
    glare = cv2.morphologyEx(glare.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0  # sin dilatar: no tapar rayones finos
    valid = covered & ~glare
    excluded = 1.0 - float(valid.mean())
    if excluded > MAX_EXCLUDED:
        raise CompareInconclusive(
            f"El {excluded * 100:.0f} % de la pieza no es evaluable (reflejos saturados o fuera de la foto). "
            "Reduzca los reflejos o mejore el encuadre."
        )
    test_lab = _normalize_light(golden_lab, test_lab, valid)

    radius = max(2, int(round(TOLERANCE_FRACTION * max(width, height))))
    color, sign = _tolerant_color(golden_lab, test_lab, radius)
    edge_extra, edge_missing, energy_g, energy_t = _edge_maps(_gray(golden), _gray(warped), radius)

    t_color = _adaptive(color, valid, preset["delta_e"])
    t_edge = _adaptive(np.maximum(edge_extra, edge_missing), valid, preset["edge"])
    anomalous = ((color > t_color) | (edge_extra > t_edge) | (edge_missing > t_edge)) & valid
    mask = anomalous.astype(np.uint8) * 255
    merge = max(3, int(0.012 * max(width, height)) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (merge, merge)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    piece_area = float(valid.sum()) or 1.0
    min_area = max(12.0, preset["min_area_pct"] / 100.0 * piece_area)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    golden_gray, test_gray = _gray(golden), _gray(warped)
    regions: list[dict[str, Any]] = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if area < min_area:
            continue
        inside = (labels == index) & valid
        if not inside.any():
            continue
        g_bgr = golden[inside].mean(axis=0)
        t_bgr = warped[inside].mean(axis=0)
        dx, dy, response = _local_shift(golden_gray, test_gray, (x, y, w, h))
        shift = math.hypot(dx, dy)
        delta_max = float(np.percentile(color[inside], 98))
        area_pct = 100.0 * float(inside.sum()) / piece_area
        d_l = float((test_lab[..., 0][inside] - golden_lab[..., 0][inside]).mean())
        d_chroma = float(np.hypot(
            (test_lab[..., 1][inside] - golden_lab[..., 1][inside]).mean(),
            (test_lab[..., 2][inside] - golden_lab[..., 2][inside]).mean(),
        ))
        region = {
            "id": 0, "x": x, "y": y, "w": w, "h": h,
            "cx": float(centroids[index][0]), "cy": float(centroids[index][1]),
            "area_px": int(inside.sum()), "area_pct": round(area_pct, 3),
            "delta_e_max": round(delta_max, 1), "delta_e_mean": round(float(color[inside].mean()), 1),
            "delta_l": round(d_l, 1), "chroma_change": round(d_chroma, 1),
            "edge_extra": round(float(edge_extra[inside].mean()), 3), "edge_missing": round(float(edge_missing[inside].mean()), 3),
            "edge_threshold": round(float(t_edge), 3),
            "golden_color": describe_color(g_bgr), "test_color": describe_color(t_bgr),
            "golden_bgr": [round(float(v)) for v in g_bgr], "test_bgr": [round(float(v)) for v in t_bgr],
            "shift_dx": round(dx, 1), "shift_dy": round(dy, 1), "shift_px": round(shift, 1),
            "shifted": False,
            "energy_golden": round(float(energy_g[inside].mean()), 3), "energy_test": round(float(energy_t[inside].mean()), 3),
            # Reflejo: las dos fotos son muy claras ahi y solo cambia cuanto (luz, no objeto).
            "glare": bool(float(min(golden_lab[..., 0][inside].mean(), test_lab[..., 0][inside].mean())) >= SATURATED_L - 40),
        }
        similar = region["energy_golden"] > 0.03 and 0.4 <= region["energy_test"] / max(region["energy_golden"], 1e-6) <= 2.5
        region["shifted"] = bool(
            similar and response >= 0.25 and shift >= max(1.5 * radius, 2.5) and shift <= 0.5 * max(w, h) + 12
        )
        region["zone"] = _zone_name(region["cx"], region["cy"], width, height)
        region["severity"] = _severity(delta_max, area_pct, preset)
        if region["glare"]:
            region["severity"] = "leve"  # luz, no objeto: se avisa pero no falla la pieza
        kind, facts, cause = _explain(region)
        region.update(kind=kind, kind_label=KIND_LABEL[kind], facts=facts, probable_cause=cause)
        regions.append(region)

    regions.sort(key=lambda r: ({"critica": 0, "moderada": 1, "leve": 2}[r["severity"]], -r["area_pct"]))
    regions = regions[:40]
    for number, region in enumerate(regions, start=1):
        region["id"] = number
        region["explanation"] = _sentence(region, width, height)

    total_pct = round(sum(r["area_pct"] for r in regions if not r["glare"]), 3)
    if any(r["severity"] == "critica" for r in regions) or total_pct >= preset["fail_pct"]:
        status = "FALLO"
    elif regions:
        status = "REVISAR"
    else:
        status = "OK"
    ssim = _ssim(golden_gray, test_gray, valid)
    score = float(np.clip(100.0 - 22.0 * math.sqrt(total_pct) - 4.0 * len(regions) + 12.0 * (ssim - 0.9), 0, 100)) if regions else float(np.clip(80 + 20 * ssim, 0, 100))

    images = _render(golden, warped, mask, regions, color, edge_extra, edge_missing, valid, glare & covered)
    report = {
        "status": status,
        "score": round(score, 1),
        "summary": _summary(status, regions, total_pct, excluded, alignment),
        "regions": regions,
        "metrics": {
            "anomalous_area_pct": total_pct,
            "regions": len(regions),
            "similarity": round(ssim, 4),
            "excluded_pct": round(100.0 * excluded, 1),
            "threshold_delta_e": round(float(t_color), 1),
            "threshold_edge": round(float(t_edge), 3),
            "tolerance_px": radius,
            "work_size": [width, height],
        },
        "alignment": {
            "method": alignment.method, "inliers": alignment.inliers, "matches": alignment.matches,
            "ecc": None if alignment.ecc is None else round(alignment.ecc, 3),
            "coverage_pct": round(100.0 * alignment.coverage, 1),
        },
        "quality": quality,
        "sensitivity": sensitivity if sensitivity in PRESETS else DEFAULT_PRESET,
        "preset": preset,
    }
    return {"report": report, "images": images, "golden_work": golden, "test_aligned": warped}


def _ssim(a: np.ndarray, b: np.ndarray, valid: np.ndarray) -> float:
    """SSIM medio (ventana gaussiana) sobre la zona evaluable."""
    a, b = a.astype(np.float32), b.astype(np.float32)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (0, 0), 2.0)
    mu_a, mu_b = blur(a), blur(b)
    var_a, var_b = blur(a * a) - mu_a ** 2, blur(b * b) - mu_b ** 2
    cov = blur(a * b) - mu_a * mu_b
    ssim = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (var_a + var_b + c2))
    return float(ssim[valid].mean()) if valid.any() else 0.0


def _sentence(region: dict[str, Any], width: int, height: int) -> str:
    x0, x1 = 100.0 * region["x"] / width, 100.0 * (region["x"] + region["w"]) / width
    y0, y1 = 100.0 * region["y"] / height, 100.0 * (region["y"] + region["h"]) / height
    head = (
        f"Zona {region['id']} ({region['zone']}; x {x0:.0f}-{x1:.0f} %, y {y0:.0f}-{y1:.0f} %; "
        f"{region['area_px']} px = {region['area_pct']:.2f} % de la pieza; severidad {region['severity']}): "
        f"{region['kind_label']}."
    )
    return " ".join([head, *region["facts"], f"Intensidad de la diferencia: dE max {region['delta_e_max']}.",
                     f"Causa probable: {region['probable_cause']}"])


def _summary(status: str, regions: list[dict[str, Any]], total_pct: float, excluded: float, alignment: Alignment) -> str:
    parts: list[str] = []
    if status == "OK":
        parts.append("La pieza coincide con el patron: no se encontraron diferencias por encima del umbral.")
    else:
        kinds = sorted({r["kind_label"].lower() for r in regions})
        parts.append(
            f"Se encontraron {len(regions)} zona(s) distinta(s) que suman {total_pct:.2f} % de la pieza "
            f"({', '.join(kinds)})."
        )
        worst = regions[0]
        parts.append(f"La mas importante es la zona {worst['id']} ({worst['zone']}): {worst['kind_label'].lower()}.")
    if excluded > 0.08:
        parts.append(f"{excluded * 100:.0f} % de la pieza no se pudo evaluar por reflejos saturados o quedar fuera de la foto.")
    parts.append(f"Alineamiento: {alignment.method.lower()} ({alignment.inliers} coincidencias).")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Imagenes del informe
# ---------------------------------------------------------------------------

SEVERITY_BGR = {"critica": (60, 60, 255), "moderada": (0, 165, 255), "leve": (0, 215, 255)}


def _render(
    golden: np.ndarray, warped: np.ndarray, mask: np.ndarray, regions: list[dict[str, Any]],
    color: np.ndarray, edge_extra: np.ndarray, edge_missing: np.ndarray, valid: np.ndarray, glare: np.ndarray,
) -> dict[str, np.ndarray]:
    scale = max(1, round(max(warped.shape[:2]) / 700))
    thick = max(2, scale + 1)
    annotated = warped.copy()
    shade = annotated.copy()
    shade[glare] = (shade[glare] * 0.5 + np.array([255, 120, 0]) * 0.5).astype(np.uint8)  # azul = no evaluable
    annotated = cv2.addWeighted(shade, 0.35, annotated, 0.65, 0)
    for region in regions:
        c = SEVERITY_BGR[region["severity"]]
        pad = 6 * scale
        p0 = (max(0, region["x"] - pad), max(0, region["y"] - pad))
        p1 = (min(annotated.shape[1] - 1, region["x"] + region["w"] + pad), min(annotated.shape[0] - 1, region["y"] + region["h"] + pad))
        cv2.rectangle(annotated, p0, p1, c, thick, cv2.LINE_AA)
        label = str(region["id"])
        font_scale = 0.45 * (1 + 0.5 * scale)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 2)
        top = max(th + 4, p0[1])
        cv2.rectangle(annotated, (p0[0], top - th - 6), (p0[0] + tw + 8, top), c, -1)
        cv2.putText(annotated, label, (p0[0] + 4, top - 4), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), 2, cv2.LINE_AA)

    heat_value = np.clip(np.maximum(color / 40.0, np.maximum(edge_extra, edge_missing) * 2.0), 0, 1)
    heat_value[~valid] = 0
    heat = cv2.applyColorMap((heat_value * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    heatmap = cv2.addWeighted(warped, 0.45, heat, 0.55, 0)
    heatmap[~valid] = (warped[~valid] * 0.6).astype(np.uint8)

    images = {"annotated": annotated, "heatmap": heatmap}
    for region in regions:
        pad = max(region["w"], region["h"]) // 2 + 12
        x0, y0 = max(0, region["x"] - pad), max(0, region["y"] - pad)
        x1, y1 = min(warped.shape[1], region["x"] + region["w"] + pad), min(warped.shape[0], region["y"] + region["h"] + pad)
        g_crop, t_crop = golden[y0:y1, x0:x1].copy(), warped[y0:y1, x0:x1].copy()
        box = (region["x"] - x0, region["y"] - y0, region["x"] - x0 + region["w"], region["y"] - y0 + region["h"])
        for crop in (g_crop, t_crop):
            cv2.rectangle(crop, box[:2], box[2:], SEVERITY_BGR[region["severity"]], 2, cv2.LINE_AA)
        factor = min(4.0, max(1.0, 260.0 / max(g_crop.shape[:2])))
        pair = []
        for crop, title in ((g_crop, "PATRON"), (t_crop, "PIEZA")):
            big = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
            band = np.full((26, big.shape[1], 3), 24, np.uint8)
            cv2.putText(band, title, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1, cv2.LINE_AA)
            pair.append(np.vstack([band, big]))
        height = max(p.shape[0] for p in pair)
        pair = [cv2.copyMakeBorder(p, 0, height - p.shape[0], 0, 6, cv2.BORDER_CONSTANT, value=(24, 24, 24)) for p in pair]
        images[f"region-{region['id']}"] = np.hstack(pair)
    return images
