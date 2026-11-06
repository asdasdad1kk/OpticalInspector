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
SEARCH_SIDE = 3200              # lado mayor (px) de la foto en la que se BUSCA el patron (puede ser mucho mas grande que el recorte)
MIN_INPUT_SIDE = 80             # una foto o recorte mas chico no sirve
MIN_MATCHES = 14                # coincidencias RANSAC minimas para aceptar el alineamiento por puntos
MIN_ECC = 0.30                  # correlacion minima de bordes tras alinear: menos = no es la misma pieza
MIN_COVERAGE = 0.80             # fraccion del patron que debe quedar cubierta por la foto alineada
MAX_EXCLUDED = 0.45             # fraccion maxima excluida (brillos saturados / sin dato) para poder decidir
SATURATED_L = 247.0             # L (0-255) desde la cual un pixel se considera reflejo/quemado
TOLERANCE_FRACTION = 0.0035     # desplazamiento admitido por el alineamiento, fraccion del lado mayor
STRONG_FACTOR = 2.5              # marca con intensidad >= 2.5 veces el umbral: cuenta aunque sea menor al area minima
NOISE_SIGMAS = 6.0              # umbral adaptativo: mediana + k * MAD del mapa de diferencias

# Cada preset: (dE minimo de color, fuerza minima de borde, area minima de region en % de la pieza,
#               % de area anomala para REVISAR, para FALLO). "estricto" es el valor por defecto.
# `noise_sigmas`: cuanto sube el umbral con el ruido de la propia foto (mediana + k * MAD); menor = ve marcas mas debiles.
# "maximo" es para marcas muy finas o tenues (pluma, rayones, manchas leves) en piezas de fondo limpio.
PRESETS: dict[str, dict[str, float]] = {
    "maximo": {"delta_e": 5.0, "edge": 0.08, "min_area_pct": 0.003, "review_pct": 0.01, "fail_pct": 0.30, "noise_sigmas": 3.5},
    "estricto": {"delta_e": 9.0, "edge": 0.16, "min_area_pct": 0.012, "review_pct": 0.04, "fail_pct": 0.60, "noise_sigmas": 6.0},
    "normal": {"delta_e": 13.0, "edge": 0.24, "min_area_pct": 0.04, "review_pct": 0.15, "fail_pct": 1.50, "noise_sigmas": 6.0},
    "tolerante": {"delta_e": 19.0, "edge": 0.36, "min_area_pct": 0.12, "review_pct": 0.50, "fail_pct": 4.00, "noise_sigmas": 7.0},
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
    test_scale: float = 1.0     # foto usada = foto original * test_scale
    object_scale: float = 1.0   # tamano del patron en la foto, respecto a su tamano en el patron
    rotation_deg: float = 0.0
    verify: float = 0.0         # parecido (ZNCC) entre el patron y la foto alineada


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

def _vote_pose(matches: list[Any], kp_t: Any, kp_g: Any) -> list[Any]:
    """Votacion de Lowe: cada coincidencia propone una escala y un giro; se conservan las que coinciden con la
    propuesta mas votada. Quita casi todas las falsas (fondo, texturas repetidas) antes de RANSAC."""
    if len(matches) < 12:
        return matches
    log_scale = np.array([math.log(max(kp_t[m.queryIdx].size, 1e-3) / max(kp_g[m.trainIdx].size, 1e-3)) for m in matches])
    angle = np.array([(kp_t[m.queryIdx].angle - kp_g[m.trainIdx].angle + 180.0) % 360.0 - 180.0 for m in matches])
    scale_bin, angle_bin = 0.5, 30.0
    # Cada coincidencia vota con su vecindad; el centro mas votado define el giro y la escala dominantes.
    delta_s = np.abs(log_scale[:, None] - log_scale[None, :]) <= scale_bin
    delta_a = np.abs((angle[:, None] - angle[None, :] + 180.0) % 360.0 - 180.0) <= angle_bin
    votes = (delta_s & delta_a).sum(axis=1)
    center = int(np.argmax(votes))
    if votes[center] < 8:
        return matches
    keep = delta_s[center] & delta_a[center]
    return [m for m, k in zip(matches, keep) if k]


def _match_points(golden: np.ndarray, test: np.ndarray) -> tuple[np.ndarray | None, int, int]:
    """Homografia test -> golden por puntos SIFT. Devuelve (matriz, inliers, coincidencias)."""
    # La foto puede ser mucho mas grande que el patron (el patron es un recorte): mas puntos en la foto.
    big = test.shape[0] * test.shape[1] > 1.5 * golden.shape[0] * golden.shape[1]
    if hasattr(cv2, "SIFT_create"):
        detector, norm = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.02), cv2.NORM_L2
        detector_test = cv2.SIFT_create(nfeatures=12000 if big else 4000, contrastThreshold=0.015 if big else 0.02)
    else:  # pragma: no cover
        detector, norm = cv2.ORB_create(nfeatures=4000), cv2.NORM_HAMMING
        detector_test = cv2.ORB_create(nfeatures=12000 if big else 4000)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    kp_g, des_g = detector.detectAndCompute(clahe.apply(_gray(golden)), None)
    kp_t, des_t = detector_test.detectAndCompute(clahe.apply(_gray(test)), None)
    if des_g is None or des_t is None or len(kp_g) < 8 or len(kp_t) < 8:
        return None, 0, 0
    pairs = cv2.BFMatcher(norm).knnMatch(des_t, des_g, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
    # SIFT da varios puntos en el mismo lugar (distinta orientacion): se conserva el mejor de cada lugar; si no,
    # RANSAC puede "ajustar" un modelo degenerado que manda todo a un punto.
    unique: dict[tuple[int, int, int, int], Any] = {}
    for m in sorted(good, key=lambda item: item.distance):
        key = (round(kp_t[m.queryIdx].pt[0] / 2), round(kp_t[m.queryIdx].pt[1] / 2),
               round(kp_g[m.trainIdx].pt[0] / 2), round(kp_g[m.trainIdx].pt[1] / 2))
        unique.setdefault(key, m)
    everything = list(unique.values())
    best: tuple[np.ndarray, int, int] | None = None
    for subset in (everything, _vote_pose(everything, kp_t, kp_g)):
        if len(subset) < 8:
            continue
        src = np.float32([kp_t[m.queryIdx].pt for m in subset])
        dst = np.float32([kp_g[m.trainIdx].pt for m in subset])
        # Primero similitud (4 grados de libertad): es lo que mas se parece a "mover la pieza" y no deforma.
        similarity, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0, maxIters=4000)
        if similarity is None or mask is None:
            continue
        scale = math.sqrt(abs(float(np.linalg.det(similarity[:, :2]))))
        if not (0.05 <= scale <= 20.0):
            continue  # modelo degenerado
        count = int(mask.sum())
        if best is None or count > best[1]:
            best = (np.vstack([similarity, [0.0, 0.0, 1.0]]), count, len(subset))
            best_points = (src, dst)
    if best is None:
        return None, 0, len(everything)
    matrix, inliers, _ = best
    src, dst = best_points
    good = everything
    # Si hay muchas coincidencias se permite perspectiva (la camara no esta perfectamente perpendicular).
    if inliers >= 40:
        homography, hmask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if homography is not None and hmask is not None and int(hmask.sum()) >= 0.9 * inliers:
            # La perspectiva solo se acepta si el patron cae casi donde dice la similitud (un objeto chico en una
            # foto grande da homografias degeneradas que ajustan los puntos pero deforman todo lo demas).
            corners = np.float32([[0, 0], [golden.shape[1], 0], [golden.shape[1], golden.shape[0]], [0, golden.shape[0]]])[None]
            try:
                by_similarity = cv2.perspectiveTransform(corners, np.linalg.inv(matrix))[0]
                by_homography = cv2.perspectiveTransform(corners, np.linalg.inv(homography))[0]
                size_in_test = max(golden.shape[:2]) / max(math.sqrt(abs(np.linalg.det(matrix[:2, :2]))), 1e-9)
                close = np.isfinite(by_homography).all() and float(np.abs(by_similarity - by_homography).max()) <= 0.06 * size_in_test
            except np.linalg.LinAlgError:
                close = False
            if close:
                matrix, inliers = homography, int(hmask.sum())
    return matrix, inliers, len(good)


def _refine_ecc(golden: np.ndarray, warped: np.ndarray) -> tuple[np.ndarray | None, float | None]:
    """Correccion fina (homografia) entre el patron y la foto YA llevada a su marco, minimizando la diferencia
    de gradientes (robusto a la luz). Devuelve (correccion warped->patron, correlacion) o (None, None)."""
    def edges(image: np.ndarray) -> np.ndarray:
        blurred = cv2.GaussianBlur(_gray(image).astype(np.float32), (0, 0), 1.6)
        gx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(blurred, cv2.CV_32F, 0, 1)
        return cv2.GaussianBlur(cv2.magnitude(gx, gy), (0, 0), 1.2)

    scale = min(1.0, 700.0 / max(golden.shape[:2]))
    small_g = edges(cv2.resize(golden, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
    small_t = edges(cv2.resize(warped, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
    identity = np.eye(3, dtype=np.float32)
    try:
        correlation, refined = cv2.findTransformECC(
            small_g, small_t, identity, cv2.MOTION_HOMOGRAPHY,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), None, 5,
        )
    except cv2.error:
        return None, None
    shrink = np.diag([scale, scale, 1.0])
    # `refined` lleva puntos del patron a la foto; la correccion warped -> patron es su inversa.
    correction = np.linalg.inv(np.linalg.inv(shrink) @ refined @ shrink)
    probe = np.float32([[0, 0], [golden.shape[1], 0], [golden.shape[1], golden.shape[0]], [0, golden.shape[0]]])[None]
    moved = cv2.perspectiveTransform(probe, correction)[0] - probe[0]
    if float(np.abs(moved).max()) > 0.04 * max(golden.shape[:2]):
        return None, None  # fue a un minimo falso
    return correction / correction[2, 2], float(correlation)


def _similar_framing(golden: np.ndarray, test: np.ndarray) -> bool:
    """El patron y la foto tienen tamano/proporcion parecidos (la foto es un recorte del mismo encuadre)."""
    ratio_w = test.shape[1] / golden.shape[1]
    ratio_h = test.shape[0] / golden.shape[0]
    return 0.6 <= ratio_w <= 1.7 and 0.6 <= ratio_h <= 1.7 and abs(math.log(ratio_w / ratio_h)) < 0.25


MIN_VERIFY = 0.35               # correlacion minima (gris o bordes) entre el patron y la foto ya alineada (puntos)
MIN_VERIFY_TEMPLATE = 0.55      # idem para la busqueda por plantilla, que siempre encuentra *algo* y por eso exige mas


def _verify(golden: np.ndarray, warped: np.ndarray, covered: np.ndarray) -> float:
    """Que tanto se parece lo encontrado al patron (ZNCC de gris y de bordes, 0-1). Una coincidencia falsa
    (p. ej. puntos casuales sobre un fondo rugoso) da ~0; la pieza correcta, con otra luz, da > 0.5."""
    factor = min(1.0, 320.0 / max(golden.shape[:2]))

    def prep(image: np.ndarray) -> np.ndarray:
        gray = cv2.GaussianBlur(_gray(image), (0, 0), 1.5)
        return cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA).astype(np.float32)

    def edges(gray: np.ndarray) -> np.ndarray:
        return cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))

    a, b = prep(golden), prep(warped)
    mask = cv2.resize(covered.astype(np.uint8), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST) > 0

    def zncc(x: np.ndarray, y: np.ndarray) -> float:
        x, y = x[mask], y[mask]
        if x.size < 50:
            return 0.0
        x, y = x - x.mean(), y - y.mean()
        norm = math.sqrt(float((x * x).sum() * (y * y).sum()))
        return float((x * y).sum() / norm) if norm > 1e-6 else 0.0

    return max(zncc(a, b), zncc(edges(a), edges(b)))


class _Rejected(CompareInconclusive):
    """Un candidato de alineamiento no paso la verificacion (se prueba con el siguiente)."""


def _template_candidates(golden: np.ndarray, test: np.ndarray, keep: int = 5) -> list[np.ndarray]:
    """Busca el patron por plantilla (ZNCC) en toda la foto, con cualquier giro y tamano.

    Respaldo para piezas lisas, con pocos puntos propios, que los puntos SIFT no encuentran (o encuentran en el
    fondo). Va de grueso (foto ~320 px) a fino; devuelve matrices foto -> patron, mejores primero.
    """
    gold_gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(_gray(golden))
    test_gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(_gray(test))
    gh, gw = gold_gray.shape

    def build(factor: float, angle: float) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Plantilla del patron a `factor` y girada `angle`: (imagen, mascara, matriz patron -> plantilla)."""
        size = int(math.ceil(math.hypot(gw, gh) * factor)) + 4
        if size < 14:
            return None
        center = np.array([size / 2.0, size / 2.0])
        scale_m = np.diag([factor, factor, 1.0])
        shift = np.eye(3)
        shift[:2, 2] = center - np.array([gw * factor / 2.0, gh * factor / 2.0])
        rot = np.vstack([cv2.getRotationMatrix2D((float(center[0]), float(center[1])), angle, 1.0), [0, 0, 1]])
        matrix = rot @ shift @ scale_m
        image = cv2.warpAffine(gold_gray, matrix[:2], (size, size), flags=cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR)
        mask = cv2.warpAffine(np.full((gh, gw), 255, np.uint8), matrix[:2], (size, size), flags=cv2.INTER_NEAREST)
        mask = cv2.erode(mask, np.ones((3, 3), np.uint8))
        return image, mask, matrix

    def match(image: np.ndarray, templ: np.ndarray, mask: np.ndarray) -> tuple[float, tuple[int, int]]:
        if templ.shape[0] >= image.shape[0] or templ.shape[1] >= image.shape[1]:
            return -1.0, (0, 0)
        try:
            result = cv2.matchTemplate(image, templ, cv2.TM_CCOEFF_NORMED, mask=mask)
        except cv2.error:
            result = cv2.matchTemplate(image, templ, cv2.TM_CCOEFF_NORMED)
        result = np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, best, _, location = cv2.minMaxLoc(result)
        return float(best), (int(location[0]), int(location[1]))

    # --- grueso ---
    coarse = min(1.0, 320.0 / max(test_gray.shape))
    small = cv2.resize(test_gray, None, fx=coarse, fy=coarse, interpolation=cv2.INTER_AREA)
    found: list[tuple[float, float, float, tuple[int, int]]] = []
    for relative in (0.5, 0.6, 0.71, 0.84, 1.0, 1.19, 1.41, 1.68, 2.0):
        for angle in range(0, 360, 12):
            built = build(coarse * relative, float(angle))
            if built is None:
                continue
            score, location = match(small, built[0], built[1])
            if score > 0:
                found.append((score, relative, float(angle), location))
    # Piezas casi simetricas empatan entre giros: se prefiere el giro menor (lo normal es no darla vuelta).
    found.sort(key=lambda item: -(item[0] - 0.02 * min(item[2], 360.0 - item[2]) / 180.0))
    chosen: list[tuple[float, float, float, tuple[int, int]]] = []
    for item in found:   # distintos: no el mismo lugar con giros vecinos
        radius = 0.5 * math.hypot(gw, gh) * coarse * item[1]
        if all(
            math.hypot(item[3][0] - o[3][0], item[3][1] - o[3][1]) > radius
            or min(abs(item[2] - o[2]), 360.0 - abs(item[2] - o[2])) >= 40.0
            for o in chosen
        ) and sum(1 for o in chosen if math.hypot(item[3][0] - o[3][0], item[3][1] - o[3][1]) <= radius) < 2:
            chosen.append(item)
        if len(chosen) >= keep:
            break

    # --- fino ---
    fine = min(1.0, 900.0 / max(test_gray.shape))
    big = cv2.resize(test_gray, None, fx=fine, fy=fine, interpolation=cv2.INTER_AREA)
    refined: list[tuple[float, np.ndarray]] = []
    for score0, relative, angle0, location in chosen:
        centre = np.array([location[0], location[1]], np.float64) * (fine / coarse)
        built0 = build(coarse * relative, angle0)
        if built0 is None:
            continue
        centre = centre + built0[0].shape[0] / 2.0 * (fine / coarse)
        best: tuple[float, np.ndarray] | None = None
        for rel in (relative * 0.93, relative, relative * 1.07):
            for angle in (angle0 - 8, angle0 - 4, angle0, angle0 + 4, angle0 + 8):
                built = build(fine * rel, float(angle))
                if built is None:
                    continue
                templ, mask, matrix = built
                half = templ.shape[0] / 2.0
                margin = 0.35 * templ.shape[0]
                x0, y0 = int(max(0, centre[0] - half - margin)), int(max(0, centre[1] - half - margin))
                x1, y1 = int(min(big.shape[1], centre[0] + half + margin)), int(min(big.shape[0], centre[1] + half + margin))
                window = big[y0:y1, x0:x1]
                score, location2 = match(window, templ, mask)
                if score <= 0:
                    continue
                to_test = np.eye(3)
                to_test[:2, 2] = [x0 + location2[0], y0 + location2[1]]
                golden_to_big = to_test @ matrix                                   # patron -> foto reducida (fino)
                golden_to_test = np.diag([1.0 / fine, 1.0 / fine, 1.0]) @ golden_to_big
                if best is None or score > best[0]:
                    best = (score, np.linalg.inv(golden_to_test))
        if best is not None:
            refined.append(best)
    refined.sort(key=lambda item: -item[0])
    return [matrix for _, matrix in refined]


def _try_candidate(
    golden: np.ndarray, test: np.ndarray, matrix: np.ndarray, method: str, inliers: int, matches: int,
) -> tuple[Alignment, np.ndarray]:
    """Afina y VERIFICA un alineamiento candidato. Lanza `_Rejected` si no es la pieza."""
    linear = matrix[:2, :2] / matrix[2, 2]
    object_scale = 1.0 / max(math.sqrt(abs(np.linalg.det(linear))), 1e-9)   # >1: el objeto se ve mas grande en la foto
    if not (0.15 <= object_scale <= 8.0 and np.isfinite(matrix).all()):
        raise _Rejected("tamano imposible")
    rotation = math.degrees(math.atan2(-linear[1, 0], linear[0, 0]))
    test_scale = 1.0
    if object_scale > 1.35:
        # Objeto mucho mas grande que en el patron: se reduce la foto para que quede del mismo tamano.
        factor = 1.0 / object_scale
        test = cv2.resize(test, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        matrix = matrix @ np.diag([1.0 / factor, 1.0 / factor, 1.0])
        test_scale = factor

    size = (golden.shape[1], golden.shape[0])
    warped = cv2.warpPerspective(test, matrix, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    correction, ecc = _refine_ecc(golden, warped)
    if correction is not None:
        matrix = correction @ matrix
        method += "+ECC"
        warped = cv2.warpPerspective(test, matrix, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    if ecc is not None and ecc < MIN_ECC:
        raise _Rejected(f"correlacion de bordes {ecc:.2f}")
    covered = _coverage_mask(golden, test, matrix)
    coverage = float(covered.mean())
    similarity = _verify(golden, warped, covered)
    if similarity < (MIN_VERIFY_TEMPLATE if method.startswith("PLANTILLA") else MIN_VERIFY):
        raise _Rejected(f"no se parece al patron ({similarity:.2f})")
    if method.startswith("ENCUADRE") and (ecc is None or ecc < 0.45):
        raise _Rejected("encuadre sin confirmar")
    if method.startswith("PUNTOS") and ecc is None and inliers < 2 * MIN_MATCHES and coverage < 0.9:
        raise _Rejected("pocas coincidencias")
    return Alignment(matrix, method, inliers, matches, ecc, coverage, test_scale, object_scale, rotation, similarity), test


def align(golden: np.ndarray, test: np.ndarray) -> tuple[Alignment, np.ndarray]:
    """Busca el patron dentro de `test` (cualquier posicion, giro o tamano) y los alinea.

    Orden: (1) puntos SIFT; (2) busqueda por plantilla en toda la foto (piezas lisas o cuando los puntos cayeron en
    el fondo); (3) mismo encuadre. Cada candidato se verifica contra el patron antes de aceptarlo.
    Devuelve la alineacion y la foto realmente usada (se reduce si el objeto se ve mucho mas grande que en el
    patron). `Alignment.matrix` lleva la foto usada al marco del patron.
    """
    reasons: list[str] = []
    passed: list[tuple[Alignment, np.ndarray]] = []
    matrix, inliers, matches = _match_points(golden, test)
    if matrix is not None and inliers >= MIN_MATCHES:
        try:
            found = _try_candidate(golden, test, matrix, "PUNTOS", inliers, matches)
            if inliers >= 2 * MIN_MATCHES:
                return found                      # muchas coincidencias verificadas: es esa
            passed.append(found)
        except _Rejected as error:
            reasons.append(f"puntos: {error}")
    else:
        reasons.append("puntos: muy pocas coincidencias")

    # Piezas lisas o con el fondo parecido: busqueda por plantilla; gana el candidato que mas se parece al patron
    # (desempata piezas casi simetricas, donde dos giros se ven casi igual).
    for number, candidate in enumerate(_template_candidates(golden, test), start=1):
        try:
            passed.append(_try_candidate(golden, test, candidate, "PLANTILLA", 0, matches))
        except _Rejected as error:
            reasons.append(f"plantilla {number}: {error}")
    if passed:
        return max(passed, key=lambda item: item[0].verify - 0.02 * min(abs(item[0].rotation_deg), 180.0) / 180.0)

    if _similar_framing(golden, test):
        blind = np.diag([golden.shape[1] / test.shape[1], golden.shape[0] / test.shape[0], 1.0])
        try:
            return _try_candidate(golden, test, blind, "ENCUADRE", 0, matches)
        except _Rejected as error:
            reasons.append(f"encuadre: {error}")

    raise CompareInconclusive(
        "No se encontro la pieza patron en la foto. Verifique que sea la pieza correcta, que se vea completa y "
        "enfocada y que la iluminacion sea parecida a la del patron; si el fondo es muy parecido a la pieza, "
        "recorte la zona donde esta. (" + "; ".join(reasons) + ")"
    )


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


def _adaptive(values: np.ndarray, valid: np.ndarray, minimum: float, sigmas: float = NOISE_SIGMAS) -> float:
    sample = values[valid]
    if sample.size == 0:
        return minimum
    median = float(np.median(sample))
    mad = float(np.median(np.abs(sample - median))) * 1.4826
    return max(minimum, median + sigmas * mad)


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

    if float(_gray(_fit(test_bgr, 512)[0]).mean()) < 10.0:
        raise CompareInconclusive("La foto esta casi negra. Revise la iluminacion o la camara.")

    golden, g_scale = _fit(golden_bgr, WORK_SIDE)
    # El patron puede ser solo un recorte: la foto se conserva grande (hasta SEARCH_SIDE) para buscarlo en ella.
    test, t_scale = _fit(test_bgr, SEARCH_SIDE)
    alignment, test = align(golden, test)
    alignment.test_scale *= t_scale
    if alignment.coverage < MIN_COVERAGE:
        raise CompareInconclusive(
            f"En la foto solo se ve el {alignment.coverage * 100:.0f} % de la pieza patron (el resto queda fuera del encuadre). "
            "Encuadre la pieza completa."
        )

    height, width = golden.shape[:2]
    warped = cv2.warpPerspective(test, alignment.matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    covered = _coverage_mask(golden, test, alignment.matrix)

    # Calidad de la imagen EN EL AREA DEL PATRON (no de toda la foto, que puede incluir mesa, fondo, etc.).
    quality = image_quality(warped)
    limits = {"sharpness": 12.0, "brightness_min": 28.0, "brightness_max": 232.0, "contrast": 6.0, "clipped": 0.35}
    limits.update(min_quality or {})
    if quality["brightness"] < limits["brightness_min"]:
        raise CompareInconclusive(f"La pieza se ve muy oscura (brillo {quality['brightness']:.0f}). Revise la iluminacion.")
    if quality["brightness"] > limits["brightness_max"]:
        raise CompareInconclusive(f"La pieza se ve sobreexpuesta (brillo {quality['brightness']:.0f}). Reduzca la exposicion.")
    if quality["sharpness"] < limits["sharpness"] or quality["contrast"] < limits["contrast"]:
        raise CompareInconclusive(f"La pieza se ve desenfocada o sin contraste (nitidez {quality['sharpness']:.0f}). Revise el enfoque.")
    if quality["clipped"] > limits["clipped"]:
        raise CompareInconclusive(f"Demasiados pixeles saturados en la pieza ({quality['clipped'] * 100:.0f} %). Reduzca reflejos o exposicion.")

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

    sigmas = preset.get("noise_sigmas", NOISE_SIGMAS)
    t_color = _adaptive(color, valid, preset["delta_e"], sigmas)
    t_edge = _adaptive(np.maximum(edge_extra, edge_missing), valid, preset["edge"], sigmas)
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
        if area < max(12.0, min_area / 5.0):
            continue
        inside = (labels == index) & valid
        if not inside.any():
            continue
        # Una marca muy intensa (pluma, rayon) cuenta aunque sea mas chica que el area minima.
        strong = float(np.percentile(color[inside], 98)) >= STRONG_FACTOR * t_color or float(
            np.maximum(edge_extra, edge_missing)[inside].max()) >= STRONG_FACTOR * t_edge
        if area < min_area and not strong:
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

    near_misses = _near_misses(color, edge_extra, edge_missing, valid, mask, t_color, t_edge, min_area, width, height)
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

    located = _locate(test_bgr, golden, alignment)
    images = _render(golden, warped, mask, regions, color, edge_extra, edge_missing, valid, glare & covered, near_misses)
    report = {
        "status": status,
        "score": round(score, 1),
        "summary": _summary(status, regions, total_pct, excluded, alignment) + (
            f" Hay {len(near_misses)} marca(s) debil(es) bajo el umbral que no cuentan (cuadros grises): "
            "si es algo real, suba la sensibilidad a «maximo»." if near_misses else ""
        ),
        "regions": regions,
        "metrics": {
            "anomalous_area_pct": total_pct,
            "regions": len(regions),
            "similarity": round(ssim, 4),
            "excluded_pct": round(100.0 * excluded, 1),
            "threshold_delta_e": round(float(t_color), 1),
            "threshold_edge": round(float(t_edge), 3),
            "tolerance_px": radius,
            "peak_delta_e": round(float(np.percentile(color[valid], 99.9)), 1) if valid.any() else 0.0,
            "near_misses": len(near_misses),
            "work_size": [width, height],
        },
        "alignment": {
            "method": alignment.method, "inliers": alignment.inliers, "matches": alignment.matches,
            "ecc": None if alignment.ecc is None else round(alignment.ecc, 3),
            "coverage_pct": round(100.0 * alignment.coverage, 1),
            "object_scale": round(alignment.object_scale, 3),
            "rotation_deg": round(alignment.rotation_deg, 1),
        },
        "quality": quality,
        "near_misses": near_misses,
        "sensitivity": sensitivity if sensitivity in PRESETS else DEFAULT_PRESET,
        "preset": preset,
    }
    report["located"] = located["info"]
    images["located"] = located["image"]
    # Foto ORIGINAL -> patron ORIGINAL (para leer codigos/texto con la resolucion nativa de la foto).
    golden_from_test = (
        np.diag([1.0 / g_scale, 1.0 / g_scale, 1.0]) @ alignment.matrix @ np.diag([alignment.test_scale, alignment.test_scale, 1.0])
    )
    return {
        "report": report, "images": images, "golden_work": golden, "test_aligned": warped,
        # px de la foto original por px del patron original: cuanto ampliar al enderezar una zona.
        "mapping": {"golden_from_test": golden_from_test,
                    "object_scale": 1.0 / max(math.sqrt(abs(float(np.linalg.det(golden_from_test[:2, :2] / golden_from_test[2, 2])))), 1e-9)},
    }


def _near_misses(
    color: np.ndarray, edge_extra: np.ndarray, edge_missing: np.ndarray, valid: np.ndarray, mask: np.ndarray,
    t_color: float, t_edge: float, min_area: float, width: int, height: int,
) -> list[dict[str, Any]]:
    """Marcas debiles que NO superaron el umbral (para que el operador sepa que hay algo y pueda subir la sensibilidad)."""
    edges = np.maximum(edge_extra, edge_missing)
    weak = ((color > 0.5 * t_color) | (edges > 0.5 * t_edge)) & valid
    weak = cv2.morphologyEx(weak.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    near_final = cv2.dilate(mask, np.ones((15, 15), np.uint8)) > 0
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(weak)
    found: list[dict[str, Any]] = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        inside = (labels == index) & valid
        if area < max(20.0, min_area / 3.0) or not inside.any() or near_final[inside].mean() > 0.3:
            continue
        peak = float(np.percentile(color[inside], 98))
        ratio = max(peak / t_color, float(edges[inside].max()) / t_edge)
        found.append({
            "x": x, "y": y, "w": w, "h": h, "area_px": int(inside.sum()),
            "zone": _zone_name(float(centroids[index][0]), float(centroids[index][1]), width, height),
            "delta_e_max": round(peak, 1), "ratio": round(ratio, 2),
        })
    found.sort(key=lambda item: -item["ratio"])
    return found[:8]


def _locate(test_bgr: np.ndarray, golden: np.ndarray, alignment: Alignment) -> dict[str, Any]:
    """Donde esta el patron dentro de la foto original: poligono (en pixeles de la foto) e imagen con el contorno."""
    corners = np.float32([[0, 0], [golden.shape[1], 0], [golden.shape[1], golden.shape[0]], [0, golden.shape[0]]])[None]
    polygon = cv2.perspectiveTransform(corners, np.linalg.inv(alignment.matrix))[0] / alignment.test_scale
    height, width = test_bgr.shape[:2]
    area = abs(float(cv2.contourArea(polygon.astype(np.float32))))
    preview, factor = _fit(test_bgr, 1600)
    drawn = preview.copy()
    points = (polygon * factor).astype(np.int32)
    overlay = drawn.copy()
    cv2.fillPoly(overlay, [points], (80, 200, 80))
    drawn = cv2.addWeighted(overlay, 0.18, drawn, 0.82, 0)
    cv2.polylines(drawn, [points], True, (80, 220, 80), max(2, round(max(drawn.shape[:2]) / 500)), cv2.LINE_AA)
    return {
        "info": {
            "polygon": [[round(float(x), 1), round(float(y), 1)] for x, y in polygon],
            "photo_size": [width, height],
            "area_fraction": round(area / max(width * height, 1), 4),
        },
        "image": drawn,
    }


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
    near_misses: list[dict[str, Any]] | None = None,
) -> dict[str, np.ndarray]:
    scale = max(1, round(max(warped.shape[:2]) / 700))
    thick = max(2, scale + 1)
    annotated = warped.copy()
    shade = annotated.copy()
    shade[glare] = (shade[glare] * 0.5 + np.array([255, 120, 0]) * 0.5).astype(np.uint8)  # azul = no evaluable
    annotated = cv2.addWeighted(shade, 0.35, annotated, 0.65, 0)
    for miss in near_misses or []:
        pad = 4 * scale
        cv2.rectangle(annotated, (miss["x"] - pad, miss["y"] - pad), (miss["x"] + miss["w"] + pad, miss["y"] + miss["h"] + pad), (170, 170, 170), 1, cv2.LINE_AA)
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
