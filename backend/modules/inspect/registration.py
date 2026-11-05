"""Registro robusto entre un conjunto de puntos de referencia (CAD o pieza
patron) y los features detectados en una fotografia.

No depende del contorno del panel: detecta candidatos (agujeros / insertos),
busca la transformacion de similitud que mas puntos de referencia explica
(RANSAC por pares) y la refina a afin y, si hay evidencia suficiente,
a homografia. El origen no necesita estar bien definido.
"""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


# ============================================================
# DETECCION DE CANDIDATOS
# ============================================================

def detect_blob_candidates(
    image: np.ndarray,
    max_candidates: int = 70,
    sigmas: tuple[float, ...] = (3.0, 4.0, 5.0, 6.0, 7.0),
) -> list[dict[str, float]]:
    """Blobs oscuros (centros de agujeros/insertos) por LoG multiescala.

    Se trabaja sobre una version reducida si la imagen es muy grande para
    mantener los sigmas utiles; las coordenadas se devuelven en pixeles
    originales.
    """
    height, width = image.shape[:2]
    scale = 1.0
    longest = max(height, width)
    if longest > 1400:
        scale = 1280.0 / longest
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        height, width = image.shape[:2]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # Normaliza iluminacion (reflejos, gradientes) antes del LoG.
    background = cv2.GaussianBlur(gray, (0, 0), 25)
    gray = gray - background

    responses = []
    for sigma in sigmas:
        blurred = cv2.GaussianBlur(gray, (0, 0), sigma)
        responses.append(cv2.Laplacian(blurred, cv2.CV_32F, ksize=3) * sigma * sigma)
    stack = np.stack(responses)
    best = stack.max(axis=0)
    best_index = stack.argmax(axis=0)

    dilated = cv2.dilate(best, np.ones((13, 13), dtype=np.uint8))
    threshold = max(5.0, float(np.percentile(best, 99.0)) * 0.12)
    peaks = np.argwhere((best >= dilated) & (best > threshold))

    candidates = [
        {
            "x": float(x) / scale,
            "y": float(y) / scale,
            "sigma": float(sigmas[best_index[y, x]]) / scale,
            "response": float(best[y, x]),
        }
        for y, x in peaks
    ]
    candidates.sort(key=lambda item: item["response"], reverse=True)
    return candidates[:max_candidates]


# ============================================================
# RANSAC POR PARES (SIMILITUD)
# ============================================================

def _similarity_from_pairs(
    src_a: np.ndarray, src_b: np.ndarray, dst_a: np.ndarray, dst_b: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Vectorizado: devuelve (valido, escala, cos, sin, tx, ty)."""
    src_vec = src_b - src_a
    dst_vec = dst_b - dst_a
    src_len = np.hypot(src_vec[..., 0], src_vec[..., 1])
    dst_len = np.hypot(dst_vec[..., 0], dst_vec[..., 1])
    safe = np.where(src_len > 1e-9, src_len, 1.0)
    scale = dst_len / safe
    angle = np.arctan2(dst_vec[..., 1], dst_vec[..., 0]) - np.arctan2(
        src_vec[..., 1], src_vec[..., 0]
    )
    return scale, angle


def find_similarity_ransac(
    reference_points: np.ndarray,
    candidates: np.ndarray,
    scale_range: tuple[float, float],
    tolerance: float,
    max_rotation_deg: float = 35.0,
    allow_flip_180: bool = True,
) -> tuple[np.ndarray | None, int]:
    """Busca la similitud (2x3) reference->imagen con mas coincidencias."""
    count_ref = len(reference_points)
    count_cand = len(candidates)
    if count_ref < 3 or count_cand < 3:
        return None, 0

    # Pares de referencia bien separados (estables numericamente).
    diffs = reference_points[:, None, :] - reference_points[None, :, :]
    dist = np.hypot(diffs[..., 0], diffs[..., 1])
    iu = np.triu_indices(count_ref, k=1)
    order = np.argsort(-dist[iu])
    pair_count = min(len(order), 14)
    ref_pairs = np.stack([iu[0][order[:pair_count]], iu[1][order[:pair_count]]], axis=1)

    ci, cj = np.meshgrid(np.arange(count_cand), np.arange(count_cand), indexing="ij")
    mask = ci != cj
    cand_pairs = np.stack([ci[mask], cj[mask]], axis=1)

    best_score = -1.0
    best_matrix: np.ndarray | None = None
    max_rotation = math.radians(max_rotation_deg)

    for ra, rb in ref_pairs:
        src_a = reference_points[ra]
        src_b = reference_points[rb]
        dst_a = candidates[cand_pairs[:, 0]]
        dst_b = candidates[cand_pairs[:, 1]]

        scale, angle = _similarity_from_pairs(
            src_a[None, :], src_b[None, :], dst_a, dst_b
        )
        angle = (angle + math.pi) % (2 * math.pi) - math.pi
        rot_ok = np.abs(angle) <= max_rotation
        if allow_flip_180:
            rot_ok |= np.abs(np.abs(angle) - math.pi) <= max_rotation
        valid = (scale >= scale_range[0]) & (scale <= scale_range[1]) & rot_ok
        if not np.any(valid):
            continue

        idx = np.where(valid)[0]
        sc = scale[idx]
        an = angle[idx]
        cos_a = sc * np.cos(an)
        sin_a = sc * np.sin(an)
        tx = dst_a[idx, 0] - (cos_a * src_a[0] - sin_a * src_a[1])
        ty = dst_a[idx, 1] - (sin_a * src_a[0] + cos_a * src_a[1])

        # Transforma todos los puntos de referencia para cada hipotesis.
        px = cos_a[:, None] * reference_points[None, :, 0] - sin_a[:, None] * reference_points[None, :, 1] + tx[:, None]
        py = sin_a[:, None] * reference_points[None, :, 0] + cos_a[:, None] * reference_points[None, :, 1] + ty[:, None]

        # Distancia al candidato mas cercano (por bloques para acotar memoria).
        scores = np.zeros(len(idx), dtype=np.float32)
        block = 4000
        for start in range(0, len(idx), block):
            sl = slice(start, start + block)
            dx = px[sl, :, None] - candidates[None, None, :, 0]
            dy = py[sl, :, None] - candidates[None, None, :, 1]
            nearest = np.sqrt(dx * dx + dy * dy).min(axis=2)
            scores[sl] = np.clip(1.0 - nearest / tolerance, 0.0, 1.0).sum(axis=1)

        local = int(np.argmax(scores))
        if scores[local] > best_score:
            best_score = float(scores[local])
            best_matrix = np.array(
                [
                    [cos_a[local], -sin_a[local], tx[local]],
                    [sin_a[local], cos_a[local], ty[local]],
                ],
                dtype=np.float64,
            )

    if best_matrix is None:
        return None, 0
    return best_matrix, int(round(best_score))


# ============================================================
# REFINAMIENTO
# ============================================================

def _apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    if matrix.shape == (2, 3):
        return points @ matrix[:, :2].T + matrix[:, 2]
    homogeneous = np.hstack([points, np.ones((len(points), 1))])
    projected = homogeneous @ matrix.T
    return projected[:, :2] / projected[:, 2:3]


def _match_nearest(
    projected: np.ndarray, candidates: np.ndarray, tolerance: float
) -> list[tuple[int, int, float]]:
    """Asignacion uno-a-uno voraz (menor distancia primero)."""
    dx = projected[:, None, 0] - candidates[None, :, 0]
    dy = projected[:, None, 1] - candidates[None, :, 1]
    distance = np.hypot(dx, dy)
    pairs = []
    used_ref: set[int] = set()
    used_cand: set[int] = set()
    flat = np.argsort(distance, axis=None)
    for flat_index in flat:
        r, c = divmod(int(flat_index), distance.shape[1])
        if distance[r, c] > tolerance:
            break
        if r in used_ref or c in used_cand:
            continue
        used_ref.add(r)
        used_cand.add(c)
        pairs.append((r, c, float(distance[r, c])))
    return pairs


def refine_registration(
    reference_points: np.ndarray,
    candidates: np.ndarray,
    initial: np.ndarray,
    tolerance: float,
) -> dict[str, Any]:
    """Similitud -> afin -> homografia con tolerancia decreciente."""
    matrix: np.ndarray = initial
    method = "SIMILITUD"
    pairs: list[tuple[int, int, float]] = []

    for tol_factor in (1.0, 0.7, 0.5, 0.4):
        pairs = _match_nearest(_apply(matrix, reference_points), candidates, tolerance * tol_factor)
        if len(pairs) < 4:
            break
        src = np.float32([reference_points[r] for r, _, _ in pairs])
        dst = np.float32([candidates[c] for _, c, _ in pairs])

        if len(pairs) >= 8 and tol_factor <= 0.5:
            homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, tolerance * 0.35)
            if homography is not None and mask is not None and int(mask.sum()) >= 8:
                matrix = homography
                method = "HOMOGRAFIA"
                continue

        affine, mask = cv2.estimateAffine2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=tolerance * 0.5,
            maxIters=2000, confidence=0.99, refineIters=10,
        )
        if affine is not None:
            matrix = affine
            method = "AFIN"

    pairs = _match_nearest(_apply(matrix, reference_points), candidates, tolerance * 0.5)
    full = matrix if matrix.shape == (3, 3) else np.vstack([matrix, [0.0, 0.0, 1.0]])
    return {
        "matrix": full,
        "method": method,
        "inliers": len(pairs),
        "pairs": pairs,
    }


def register(
    reference_points: np.ndarray,
    candidates: list[dict[str, float]],
    image_size: tuple[int, int],
    reference_extent: tuple[float, float],
    min_fill: float = 0.25,
    max_fill: float = 1.0,
) -> dict[str, Any] | None:
    """Registro completo. Devuelve homografia referencia->imagen (3x3).

    reference_extent: (ancho, alto) del conjunto de referencia, usado para
    acotar la escala posible respecto al tamano de la imagen.
    """
    if len(candidates) < 4 or len(reference_points) < 4:
        return None

    image_width, image_height = image_size
    cand = np.float64([[c["x"], c["y"]] for c in candidates])
    ref = np.asarray(reference_points, dtype=np.float64)

    # El lado MAYOR de la referencia contra el lado mayor de la imagen: una tira angosta y alta
    # (ancho 13 % de la foto) tendria con el ancho una escala "imposible".
    ref_long = max(reference_extent[0], reference_extent[1], 1e-9)
    image_long = max(image_width, image_height)
    scale_range = (
        min_fill * image_long / ref_long,
        max_fill * image_long / ref_long * 1.05,
    )
    tolerance = max(6.0, 0.014 * image_width)

    initial, score = find_similarity_ransac(ref, cand, scale_range, tolerance)
    if initial is None:
        return None

    result = refine_registration(ref, cand, initial, tolerance)
    result["initial_score"] = score
    result["tolerance"] = tolerance
    return result
