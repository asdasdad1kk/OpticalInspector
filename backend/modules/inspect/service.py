from __future__ import annotations

import math
import os
import tempfile
from typing import Any

import cv2
import ezdxf
import numpy as np
from fastapi import HTTPException, UploadFile
from sqlmodel import Session

# Ajusta este import a tu estructura real.
from models import Drawing, Inspection


# ============================================================
# CONFIGURACION
# ============================================================

RECTIFIED_WIDTH = 1800
ROI_SEARCH_MARGIN_PX = 22

OK_SCORE = 68.0
REVIEW_SCORE = 48.0

MIN_PANEL_AREA_FACTOR = 0.10
MIN_PANEL_WIDTH_FACTOR = 0.45
MIN_PANEL_HEIGHT_FACTOR = 0.25
IMAGE_BORDER_MARGIN = 5

CANNY_LOW = 40
CANNY_HIGH = 120
MAX_CHAMFER_DISTANCE = 12.0
LOCAL_ALIGNMENT_SHIFT_PX = 18

REFINE_ALIGNMENT_WITH_INSERTS = True
CENTER_SEARCH_RADIUS_PX = 55
REAL_MIN_RADIUS_PX = 8
REAL_MAX_RADIUS_PX = 30
MAX_CENTER_DISTANCE_PX = 50
MIN_ALIGNMENT_POINTS = 6
RANSAC_THRESHOLD_PX = 5.0
USE_HOMOGRAPHY_REFINEMENT = False


# ============================================================
# UTILIDADES
# ============================================================

def encode_png(image: np.ndarray) -> bytes:
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise ValueError("No fue posible codificar la imagen PNG.")
    return encoded.tobytes()


def decode_image(image_bytes: bytes) -> np.ndarray:
    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("OpenCV no pudo leer la imagen recibida.")
    return image


def order_points(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32)
    ordered = np.zeros((4, 2), dtype=np.float32)
    point_sum = points.sum(axis=1)
    point_diff = np.diff(points, axis=1).reshape(-1)

    ordered[0] = points[np.argmin(point_sum)]
    ordered[2] = points[np.argmax(point_sum)]
    ordered[1] = points[np.argmin(point_diff)]
    ordered[3] = points[np.argmax(point_diff)]
    return ordered


def cad_to_pixel(
    x: float,
    y: float,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    width: int,
    height: int,
) -> tuple[int, int]:
    cad_width = xmax - xmin
    cad_height = ymax - ymin

    if cad_width <= 0 or cad_height <= 0:
        raise ValueError("Los limites CAD no son validos.")

    px = (x - xmin) / cad_width
    py = (ymax - y) / cad_height

    return (
        int(round(px * (width - 1))),
        int(round(py * (height - 1))),
    )


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def transform_point(
    x: float,
    y: float,
    transformation: np.ndarray,
) -> tuple[float, float]:
    point = np.array([[[float(x), float(y)]]], dtype=np.float32)
    transformed = cv2.perspectiveTransform(point, transformation)[0, 0]
    return float(transformed[0]), float(transformed[1])


# ============================================================
# DETECCION Y RECTIFICACION DEL PANEL
# ============================================================

def detect_panel_corners(
    image: np.ndarray,
    expected_panel_aspect: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if image is None or image.size == 0:
        raise ValueError("La imagen de inspeccion esta vacia.")

    image_height, image_width = image.shape[:2]
    image_area = image_width * image_height
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    filtered = cv2.bilateralFilter(gray, 9, 50, 50)
    enhanced = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    ).apply(filtered)

    median_intensity = float(np.median(enhanced))
    canny_low = int(max(10, 0.55 * median_intensity))
    canny_high = int(min(255, 1.35 * median_intensity))

    if canny_high <= canny_low:
        canny_low, canny_high = 30, 110

    edges_canny = cv2.Canny(
        enhanced,
        canny_low,
        canny_high,
        apertureSize=3,
        L2gradient=True,
    )

    gradient = cv2.morphologyEx(
        enhanced,
        cv2.MORPH_GRADIENT,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
    )
    _, gradient_binary = cv2.threshold(
        gradient,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )

    combined = cv2.bitwise_or(edges_canny, gradient_binary)
    horizontal = cv2.morphologyEx(
        combined,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (31, 7)),
        iterations=2,
    )
    vertical = cv2.morphologyEx(
        combined,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (7, 31)),
        iterations=2,
    )
    closed = cv2.bitwise_or(horizontal, vertical)
    closed = cv2.morphologyEx(
        closed,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)),
        iterations=2,
    )
    closed = cv2.dilate(
        closed,
        np.ones((3, 3), dtype=np.uint8),
        iterations=1,
    )

    contours, _ = cv2.findContours(
        closed,
        cv2.RETR_LIST,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    minimum_area = image_area * MIN_PANEL_AREA_FACTOR
    minimum_width = image_width * MIN_PANEL_WIDTH_FACTOR
    minimum_height = image_height * MIN_PANEL_HEIGHT_FACTOR
    candidates: list[dict[str, Any]] = []

    for contour in contours:
        contour_area = cv2.contourArea(contour)
        if contour_area < minimum_area:
            continue

        rotated_rectangle = cv2.minAreaRect(contour)
        (_, _), (rect_width, rect_height), _ = rotated_rectangle

        if rect_width <= 0 or rect_height <= 0:
            continue

        long_side = max(rect_width, rect_height)
        short_side = min(rect_width, rect_height)

        if long_side < minimum_width or short_side < minimum_height:
            continue

        rectangle_area = rect_width * rect_height
        aspect_ratio = long_side / short_side
        rectangularity = min(1.0, contour_area / rectangle_area)
        box = order_points(cv2.boxPoints(rotated_rectangle))

        box_xmin = float(np.min(box[:, 0]))
        box_xmax = float(np.max(box[:, 0]))
        box_ymin = float(np.min(box[:, 1]))
        box_ymax = float(np.max(box[:, 1]))

        covers_complete_image = (
            box_xmin <= IMAGE_BORDER_MARGIN
            and box_ymin <= IMAGE_BORDER_MARGIN
            and box_xmax >= image_width - IMAGE_BORDER_MARGIN
            and box_ymax >= image_height - IMAGE_BORDER_MARGIN
        )
        if covers_complete_image:
            continue

        aspect_error = abs(aspect_ratio - expected_panel_aspect) / expected_panel_aspect
        aspect_score = max(0.0, 1.0 - aspect_error)
        area_factor = min(1.0, rectangle_area / image_area)
        score = (
            aspect_score * 0.55
            + rectangularity * 0.20
            + area_factor * 0.25
        )

        candidates.append({
            "contour": contour,
            "box": box,
            "score": score,
        })

    if not candidates:
        useful_points = []
        minimum_perimeter = min(image_width, image_height) * 0.08

        for contour in contours:
            perimeter = cv2.arcLength(contour, False)
            contour_area = cv2.contourArea(contour)
            if perimeter >= minimum_perimeter or contour_area >= image_area * 0.005:
                useful_points.append(contour.reshape(-1, 2))

        if not useful_points:
            raise ValueError("No fue posible detectar el contorno del panel.")

        all_points = np.vstack(useful_points).astype(np.float32)
        valid = (
            (all_points[:, 0] > IMAGE_BORDER_MARGIN)
            & (all_points[:, 0] < image_width - IMAGE_BORDER_MARGIN)
            & (all_points[:, 1] > IMAGE_BORDER_MARGIN)
            & (all_points[:, 1] < image_height - IMAGE_BORDER_MARGIN)
        )
        filtered_points = all_points[valid]
        if len(filtered_points) < 20:
            filtered_points = all_points

        rotated_rectangle = cv2.minAreaRect(filtered_points)
        candidates.append({
            "contour": cv2.convexHull(filtered_points.astype(np.int32)),
            "box": order_points(cv2.boxPoints(rotated_rectangle)),
            "score": 0.0,
        })

    selected = max(candidates, key=lambda item: item["score"])
    corners = order_points(selected["box"])
    panel_contour = selected["contour"]
    panel_polygon = np.round(corners).astype(np.int32)

    panel_mask = np.zeros((image_height, image_width), dtype=np.uint8)
    cv2.fillConvexPoly(panel_mask, panel_polygon, 255)

    debug = image.copy()
    cv2.drawContours(debug, [panel_contour], -1, (255, 0, 0), 2, cv2.LINE_AA)
    cv2.polylines(debug, [panel_polygon], True, (0, 255, 0), 3, cv2.LINE_AA)

    for name, point in zip(("TL", "TR", "BR", "BL"), corners):
        x = int(round(point[0]))
        y = int(round(point[1]))
        cv2.circle(debug, (x, y), 8, (0, 0, 255), -1)
        cv2.putText(
            debug,
            name,
            (x + 10, y - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )

    return corners, debug, panel_mask


def rectify_panel(
    image: np.ndarray,
    corners: np.ndarray,
    rectified_width: int,
    rectified_height: int,
) -> tuple[np.ndarray, np.ndarray]:
    corners = order_points(corners)
    destination = np.array(
        [
            [0, 0],
            [rectified_width - 1, 0],
            [rectified_width - 1, rectified_height - 1],
            [0, rectified_height - 1],
        ],
        dtype=np.float32,
    )
    homography = cv2.getPerspectiveTransform(corners, destination)
    rectified = cv2.warpPerspective(
        image,
        homography,
        (rectified_width, rectified_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    return rectified, homography


# ============================================================
# DXF Y PLANTILLA CAD
# ============================================================

def open_dxf_from_bytes(dxf_bytes: bytes) -> tuple[Any, str]:
    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as temporary_file:
            temporary_file.write(dxf_bytes)
            temporary_path = temporary_file.name
        document = ezdxf.readfile(temporary_path)
        return document, temporary_path
    except Exception:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
        raise


def get_dxf_bounds(modelspace: Any) -> tuple[float, float, float, float]:
    all_x: list[float] = []
    all_y: list[float] = []

    for entity in modelspace:
        entity_type = entity.dxftype()
        try:
            if entity_type == "LINE":
                all_x.extend([float(entity.dxf.start.x), float(entity.dxf.end.x)])
                all_y.extend([float(entity.dxf.start.y), float(entity.dxf.end.y)])
            elif entity_type in ("CIRCLE", "ARC"):
                center = entity.dxf.center
                radius = float(entity.dxf.radius)
                all_x.extend([float(center.x) - radius, float(center.x) + radius])
                all_y.extend([float(center.y) - radius, float(center.y) + radius])
            elif entity_type == "LWPOLYLINE":
                for point in entity.get_points():
                    all_x.append(float(point[0]))
                    all_y.append(float(point[1]))
            elif entity_type == "POLYLINE":
                for vertex in entity.vertices:
                    location = vertex.dxf.location
                    all_x.append(float(location.x))
                    all_y.append(float(location.y))
            elif entity_type == "SPLINE":
                for point in entity.construction_tool().approximate(segments=200):
                    all_x.append(float(point.x))
                    all_y.append(float(point.y))
        except Exception:
            continue

    if not all_x or not all_y:
        raise ValueError("No se pudieron calcular los limites del DXF.")

    return min(all_x), max(all_x), min(all_y), max(all_y)


def draw_arc_on_template(
    template: np.ndarray,
    center_x: float,
    center_y: float,
    radius: float,
    start_angle: float,
    end_angle: float,
    convert: Any,
) -> None:
    if end_angle < start_angle:
        end_angle += 360.0

    points = []
    for angle in np.linspace(start_angle, end_angle, max(12, int(end_angle - start_angle))):
        angle_rad = math.radians(angle)
        x = center_x + radius * math.cos(angle_rad)
        y = center_y + radius * math.sin(angle_rad)
        points.append(convert(x, y))

    if len(points) >= 2:
        cv2.polylines(
            template,
            [np.asarray(points, dtype=np.int32)],
            False,
            255,
            2,
            cv2.LINE_AA,
        )


def draw_dxf_template(
    modelspace: Any,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    width: int,
    height: int,
) -> np.ndarray:
    template = np.zeros((height, width), dtype=np.uint8)

    def convert(x: float, y: float) -> tuple[int, int]:
        return cad_to_pixel(x, y, xmin, xmax, ymin, ymax, width, height)

    radius_scale = ((width / (xmax - xmin)) + (height / (ymax - ymin))) / 2.0

    for entity in modelspace:
        entity_type = entity.dxftype()
        try:
            if entity_type == "LINE":
                cv2.line(
                    template,
                    convert(entity.dxf.start.x, entity.dxf.start.y),
                    convert(entity.dxf.end.x, entity.dxf.end.y),
                    255,
                    2,
                    cv2.LINE_AA,
                )
            elif entity_type == "CIRCLE":
                center = convert(entity.dxf.center.x, entity.dxf.center.y)
                radius = max(1, int(round(float(entity.dxf.radius) * radius_scale)))
                cv2.circle(template, center, radius, 255, 2, cv2.LINE_AA)
            elif entity_type == "ARC":
                center = entity.dxf.center
                draw_arc_on_template(
                    template,
                    float(center.x),
                    float(center.y),
                    float(entity.dxf.radius),
                    float(entity.dxf.start_angle),
                    float(entity.dxf.end_angle),
                    convert,
                )
            elif entity_type == "LWPOLYLINE":
                try:
                    for segment in entity.virtual_entities():
                        if segment.dxftype() == "LINE":
                            cv2.line(
                                template,
                                convert(segment.dxf.start.x, segment.dxf.start.y),
                                convert(segment.dxf.end.x, segment.dxf.end.y),
                                255,
                                2,
                                cv2.LINE_AA,
                            )
                        elif segment.dxftype() == "ARC":
                            center = segment.dxf.center
                            draw_arc_on_template(
                                template,
                                float(center.x),
                                float(center.y),
                                float(segment.dxf.radius),
                                float(segment.dxf.start_angle),
                                float(segment.dxf.end_angle),
                                convert,
                            )
                except Exception:
                    points = [convert(point[0], point[1]) for point in entity.get_points()]
                    if len(points) >= 2:
                        cv2.polylines(
                            template,
                            [np.asarray(points, dtype=np.int32)],
                            bool(entity.closed),
                            255,
                            2,
                            cv2.LINE_AA,
                        )
        except Exception as error:
            print(f"No se pudo dibujar {entity_type}: {error}")

    _, template = cv2.threshold(template, 80, 255, cv2.THRESH_BINARY)
    return template


# ============================================================
# BORDES Y COMPARACION
# ============================================================

def create_real_edge_image(rectified: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    ).apply(gray)
    enhanced = cv2.bilateralFilter(enhanced, 7, 40, 40)
    edges = cv2.Canny(enhanced, CANNY_LOW, CANNY_HIGH)
    edges = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        iterations=1,
    )
    return gray, edges


def find_best_local_alignment(
    real_edges: np.ndarray,
    cad_edges: np.ndarray,
    maximum_shift: int = LOCAL_ALIGNMENT_SHIFT_PX,
) -> tuple[np.ndarray, int, int]:
    if real_edges.size == 0 or cad_edges.size == 0:
        return cad_edges, 0, 0

    best_score = -1.0
    best_dx = 0
    best_dy = 0
    best_template = cad_edges.copy()

    real_binary = (real_edges > 0).astype(np.uint8)
    real_dilated = cv2.dilate(
        real_binary,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        iterations=1,
    )

    for dy in range(-maximum_shift, maximum_shift + 1):
        for dx in range(-maximum_shift, maximum_shift + 1):
            shifted = cv2.warpAffine(
                cad_edges,
                np.float32([[1, 0, dx], [0, 1, dy]]),
                (cad_edges.shape[1], cad_edges.shape[0]),
                flags=cv2.INTER_NEAREST,
                borderValue=0,
            )
            cad_binary = (shifted > 0).astype(np.uint8)
            cad_count = np.count_nonzero(cad_binary)
            if cad_count == 0:
                continue

            matched = np.logical_and(cad_binary > 0, real_dilated > 0)
            score = np.count_nonzero(matched) / cad_count

            if score > best_score:
                best_score = score
                best_dx = dx
                best_dy = dy
                best_template = shifted

    return best_template, best_dx, best_dy


def chamfer_similarity(real_edges: np.ndarray, cad_edges: np.ndarray) -> float:
    real_binary = (real_edges > 0).astype(np.uint8)
    cad_binary = (cad_edges > 0).astype(np.uint8)
    expected_points = np.argwhere(cad_binary > 0)

    if len(expected_points) < 5 or np.count_nonzero(real_binary) < 5:
        return 0.0

    distance_input = np.where(real_binary > 0, 0, 255).astype(np.uint8)
    distance_map = cv2.distanceTransform(distance_input, cv2.DIST_L2, 3)
    distances = distance_map[expected_points[:, 0], expected_points[:, 1]]
    distances = np.clip(distances, 0, MAX_CHAMFER_DISTANCE)
    similarity = (1.0 - float(np.mean(distances)) / MAX_CHAMFER_DISTANCE) * 100.0
    return float(np.clip(similarity, 0, 100))


def coverage_similarity(real_edges: np.ndarray, cad_edges: np.ndarray) -> float:
    real_binary = (real_edges > 0).astype(np.uint8)
    cad_binary = (cad_edges > 0).astype(np.uint8)
    expected_count = np.count_nonzero(cad_binary)

    if expected_count == 0:
        return 0.0

    expanded_real = cv2.dilate(
        real_binary,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
        iterations=1,
    )
    matched = np.logical_and(expanded_real > 0, cad_binary > 0)
    return np.count_nonzero(matched) / expected_count * 100.0


def compare_roi(real_edges: np.ndarray, cad_edges: np.ndarray) -> dict[str, Any]:
    aligned_cad, dx, dy = find_best_local_alignment(real_edges, cad_edges)
    chamfer_score = chamfer_similarity(real_edges, aligned_cad)
    coverage_score = coverage_similarity(real_edges, aligned_cad)
    final_score = chamfer_score * 0.55 + coverage_score * 0.45

    return {
        "score": final_score,
        "chamfer": chamfer_score,
        "coverage": coverage_score,
        "dx": dx,
        "dy": dy,
    }


# ============================================================
# REFINAMIENTO CON INSERTOS REALES
# ============================================================

def detect_real_center_near_expected(
    gray: np.ndarray,
    expected_x: int,
    expected_y: int,
) -> tuple[tuple[float, float] | None, float | None, float]:
    image_height, image_width = gray.shape[:2]
    x1 = max(0, int(round(expected_x - CENTER_SEARCH_RADIUS_PX)))
    y1 = max(0, int(round(expected_y - CENTER_SEARCH_RADIUS_PX)))
    x2 = min(image_width, int(round(expected_x + CENTER_SEARCH_RADIUS_PX + 1)))
    y2 = min(image_height, int(round(expected_y + CENTER_SEARCH_RADIUS_PX + 1)))
    roi = gray[y1:y2, x1:x2]

    if roi.size == 0:
        return None, None, 0.0

    enhanced = cv2.createCLAHE(
        clipLimit=2.5,
        tileGridSize=(4, 4),
    ).apply(roi)
    enhanced = cv2.GaussianBlur(enhanced, (7, 7), 1.5)
    circles = cv2.HoughCircles(
        enhanced,
        cv2.HOUGH_GRADIENT,
        dp=1.1,
        minDist=max(15, CENTER_SEARCH_RADIUS_PX // 2),
        param1=100,
        param2=13,
        minRadius=REAL_MIN_RADIUS_PX,
        maxRadius=REAL_MAX_RADIUS_PX,
    )

    if circles is None:
        return None, None, 0.0

    expected_local_x = expected_x - x1
    expected_local_y = expected_y - y1
    candidates = []

    for local_x, local_y, radius in np.round(circles[0]).astype(int):
        if not (0 <= local_x < roi.shape[1] and 0 <= local_y < roi.shape[0]):
            continue

        center_distance = math.hypot(
            local_x - expected_local_x,
            local_y - expected_local_y,
        )
        if center_distance > MAX_CENTER_DISTANCE_PX:
            continue

        center_mask = np.zeros(roi.shape, dtype=np.uint8)
        cv2.circle(
            center_mask,
            (local_x, local_y),
            max(2, int(round(radius * 0.30))),
            255,
            -1,
        )
        center_intensity = cv2.mean(roi, mask=center_mask)[0]

        outer_mask = np.zeros(roi.shape, dtype=np.uint8)
        inner_mask = np.zeros(roi.shape, dtype=np.uint8)
        cv2.circle(outer_mask, (local_x, local_y), max(2, int(radius * 0.90)), 255, -1)
        cv2.circle(inner_mask, (local_x, local_y), max(1, int(radius * 0.40)), 255, -1)
        ring_intensity = cv2.mean(roi, mask=cv2.subtract(outer_mask, inner_mask))[0]
        contrast = ring_intensity - center_intensity

        distance_score = max(0.0, 1.0 - center_distance / MAX_CENTER_DISTANCE_PX)
        dark_center_score = max(0.0, min(1.0, (190.0 - center_intensity) / 120.0))
        contrast_score = max(0.0, min(1.0, contrast / 60.0))
        confidence = (
            distance_score * 0.45
            + dark_center_score * 0.25
            + contrast_score * 0.30
        )
        candidates.append((confidence, local_x + x1, local_y + y1, radius))

    if not candidates:
        return None, None, 0.0

    confidence, global_x, global_y, radius = max(candidates, key=lambda item: item[0])
    if confidence < 0.34:
        return None, None, confidence

    return (float(global_x), float(global_y)), float(radius), float(confidence)


def collect_alignment_points(
    rectified: np.ndarray,
    rois: list[dict[str, Any]],
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    width: int,
    height: int,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray]:
    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY)
    expected_points = []
    detected_points = []
    matches = []
    debug = rectified.copy()

    for roi in rois:
        expected_x, expected_y = cad_to_pixel(
            roi["center_x_mm"],
            roi["center_y_mm"],
            xmin,
            xmax,
            ymin,
            ymax,
            width,
            height,
        )
        detected_center, radius, confidence = detect_real_center_near_expected(
            gray,
            expected_x,
            expected_y,
        )
        cv2.drawMarker(
            debug,
            (expected_x, expected_y),
            (255, 0, 0),
            cv2.MARKER_CROSS,
            14,
            2,
            cv2.LINE_AA,
        )

        if detected_center is None or radius is None:
            continue

        detected_x = int(round(detected_center[0]))
        detected_y = int(round(detected_center[1]))
        expected_points.append([expected_x, expected_y])
        detected_points.append([detected_center[0], detected_center[1]])
        matches.append({
            "roi_id": roi["id"],
            "expected_x": expected_x,
            "expected_y": expected_y,
            "detected_x": detected_center[0],
            "detected_y": detected_center[1],
            "radius": radius,
            "confidence": round(confidence, 4),
            "displacement": round(
                math.hypot(detected_center[0] - expected_x, detected_center[1] - expected_y),
                4,
            ),
        })
        cv2.circle(debug, (detected_x, detected_y), int(round(radius)), (0, 255, 0), 2)
        cv2.line(debug, (expected_x, expected_y), (detected_x, detected_y), (0, 255, 255), 2)

    return (
        np.asarray(expected_points, dtype=np.float32),
        np.asarray(detected_points, dtype=np.float32),
        matches,
        debug,
    )


def calculate_insert_refinement(
    expected_points: np.ndarray,
    detected_points: np.ndarray,
) -> tuple[np.ndarray, str, int]:
    point_count = len(expected_points)
    identity = np.eye(3, dtype=np.float64)

    if point_count < MIN_ALIGNMENT_POINTS:
        return identity, "IDENTIDAD", 0

    if USE_HOMOGRAPHY_REFINEMENT and point_count >= 6:
        refinement, inlier_mask = cv2.findHomography(
            expected_points,
            detected_points,
            cv2.RANSAC,
            RANSAC_THRESHOLD_PX,
        )
        if refinement is not None and inlier_mask is not None:
            inlier_count = int(np.count_nonzero(inlier_mask))
            if inlier_count >= MIN_ALIGNMENT_POINTS:
                return refinement, "HOMOGRAFIA", inlier_count

    affine, inlier_mask = cv2.estimateAffine2D(
        expected_points,
        detected_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=RANSAC_THRESHOLD_PX,
        maxIters=3000,
        confidence=0.995,
        refineIters=20,
    )
    if affine is None:
        return identity, "IDENTIDAD", 0

    refinement = identity.copy()
    refinement[:2, :] = affine
    inlier_count = int(np.count_nonzero(inlier_mask)) if inlier_mask is not None else 0
    return refinement, "AFIN", inlier_count


def validate_refinement_matrix(
    refinement: np.ndarray,
    width: int,
    height: int,
) -> bool:
    corners = np.array(
        [[
            [0, 0],
            [width - 1, 0],
            [width - 1, height - 1],
            [0, height - 1],
        ]],
        dtype=np.float32,
    )
    transformed = cv2.perspectiveTransform(corners, refinement)[0]
    transformed_area = abs(cv2.contourArea(transformed.astype(np.float32)))
    area_ratio = transformed_area / float(width * height)
    maximum_displacement = float(np.max(np.linalg.norm(transformed - corners[0], axis=1)))
    return 0.85 <= area_ratio <= 1.15 and maximum_displacement <= 100


# ============================================================
# INSPECCION DE ROIS
# ============================================================

# def inspect_rois(
#     rectified: np.ndarray,
#     real_edges: np.ndarray,
#     cad_template: np.ndarray,
#     rois: list[dict[str, Any]],
#     xmin: float,
#     xmax: float,
#     ymin: float,
#     ymax: float,
#     refinement: np.ndarray,
#     width: int,
#     height: int,
# ) -> tuple[np.ndarray, list[dict[str, Any]]]:
#     result = rectified.copy()
#     inspection_results = []
#     pixels_per_mm_x = width / (xmax - xmin)
#     pixels_per_mm_y = height / (ymax - ymin)

#     for roi in rois:
#         roi_id = roi["id"]
#         component_type = roi["type"]
#         radius_mm = max(float(roi["outer_radius_mm"]), 0.5)

#         original_px, original_py = cad_to_pixel(
#             roi["center_x_mm"],
#             roi["center_y_mm"],
#             xmin,
#             xmax,
#             ymin,
#             ymax,
#             width,
#             height,
#         )
#         refined_px, refined_py = transform_point(original_px, original_py, refinement)
#         px = int(round(refined_px))
#         py = int(round(refined_py))

#         original_radius_x = int(round(radius_mm * pixels_per_mm_x))
#         original_radius_y = int(round(radius_mm * pixels_per_mm_y))
#         left = transform_point(original_px - original_radius_x, original_py, refinement)
#         right = transform_point(original_px + original_radius_x, original_py, refinement)
#         top = transform_point(original_px, original_py - original_radius_y, refinement)
#         bottom = transform_point(original_px, original_py + original_radius_y, refinement)

#         radius_x = max(2, int(round(math.hypot(right[0] - left[0], right[1] - left[1]) / 2.0)))
#         radius_y = max(2, int(round(math.hypot(bottom[0] - top[0], bottom[1] - top[1]) / 2.0)))

#         x1 = clamp(px - radius_x - ROI_SEARCH_MARGIN_PX, 0, width - 1)
#         y1 = clamp(py - radius_y - ROI_SEARCH_MARGIN_PX, 0, height - 1)
#         x2 = clamp(px + radius_x + ROI_SEARCH_MARGIN_PX + 1, 1, width)
#         y2 = clamp(py + radius_y + ROI_SEARCH_MARGIN_PX + 1, 1, height)

#         real_roi = real_edges[y1:y2, x1:x2]
#         cad_roi = cad_template[y1:y2, x1:x2]
#         comparison = compare_roi(real_roi, cad_roi)
#         score = comparison["score"]

#         if score >= OK_SCORE:
#             item_status = "OK"
#             color = (0, 200, 0)
#         elif score >= REVIEW_SCORE:
#             item_status = "REVISAR"
#             color = (0, 165, 255)
#         else:
#             item_status = "FALLO"
#             color = (0, 0, 255)

#         cv2.rectangle(result, (x1, y1), (x2, y2), color, 3, cv2.LINE_AA)
#         cv2.drawMarker(result, (px, py), color, cv2.MARKER_CROSS, 12, 2, cv2.LINE_AA)

#         label = f"{roi_id} {component_type} {item_status} {score:.1f}"
#         text_size, baseline = cv2.getTextSize(
#             label,
#             cv2.FONT_HERSHEY_SIMPLEX,
#             0.45,
#             1,
#         )
#         label_y = max(text_size[1] + 5, y1 - 5)
#         cv2.rectangle(
#             result,
#             (x1, label_y - text_size[1] - 5),
#             (x1 + text_size[0] + 8, label_y + baseline + 3),
#             color,
#             -1,
#         )
#         cv2.putText(
#             result,
#             label,
#             (x1 + 4, label_y),
#             cv2.FONT_HERSHEY_SIMPLEX,
#             0.45,
#             (255, 255, 255),
#             1,
#             cv2.LINE_AA,
#         )

#         inspection_results.append({
#             "id": roi_id,
#             "type": component_type,
#             "status": item_status,
#             "score": round(score, 2),
#             "chamfer": round(comparison["chamfer"], 2),
#             "coverage": round(comparison["coverage"], 2),
#             "local_shift_x": comparison["dx"],
#             "local_shift_y": comparison["dy"],
#             "center_x_px": px,
#             "center_y_px": py,
#         })

#     return result, inspection_results

def inspect_rois(
    rectified: np.ndarray,
    real_edges: np.ndarray,
    cad_template: np.ndarray,
    rois: list[dict[str, Any]],
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    refinement: np.ndarray,
    width: int,
    height: int,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    result = rectified.copy()
    inspection_results: list[dict[str, Any]] = []

    pixels_per_mm_x = width / (xmax - xmin)
    pixels_per_mm_y = height / (ymax - ymin)

    for roi in rois:
        roi_id = roi["id"]
        component_type = roi["type"]
        radius_mm = max(float(roi["outer_radius_mm"]), 0.5)

        # =====================================================
        # 1. Centro original obtenido desde el CAD
        # =====================================================

        original_px, original_py = cad_to_pixel(
            float(roi["center_x_mm"]),
            float(roi["center_y_mm"]),
            xmin,
            xmax,
            ymin,
            ymax,
            width,
            height,
        )

        # =====================================================
        # 2. Centro después del refinamiento global
        # =====================================================

        refined_px, refined_py = transform_point(
            original_px,
            original_py,
            refinement,
        )

        global_px = int(round(refined_px))
        global_py = int(round(refined_py))

        # =====================================================
        # 3. Tamaño transformado del ROI
        # =====================================================

        original_radius_x = max(
            2,
            int(round(radius_mm * pixels_per_mm_x)),
        )
        original_radius_y = max(
            2,
            int(round(radius_mm * pixels_per_mm_y)),
        )

        left_point = transform_point(
            original_px - original_radius_x,
            original_py,
            refinement,
        )
        right_point = transform_point(
            original_px + original_radius_x,
            original_py,
            refinement,
        )
        top_point = transform_point(
            original_px,
            original_py - original_radius_y,
            refinement,
        )
        bottom_point = transform_point(
            original_px,
            original_py + original_radius_y,
            refinement,
        )

        radius_x = max(
            2,
            int(
                round(
                    math.hypot(
                        right_point[0] - left_point[0],
                        right_point[1] - left_point[1],
                    )
                    / 2.0
                )
            ),
        )

        radius_y = max(
            2,
            int(
                round(
                    math.hypot(
                        bottom_point[0] - top_point[0],
                        bottom_point[1] - top_point[1],
                    )
                    / 2.0
                )
            ),
        )

        # =====================================================
        # 4. Ventana ampliada para alineación local
        # =====================================================

        search_margin = max(
            ROI_SEARCH_MARGIN_PX,
            LOCAL_ALIGNMENT_SHIFT_PX + 4,
        )

        search_x1 = clamp(
            global_px - radius_x - search_margin,
            0,
            width - 1,
        )
        search_y1 = clamp(
            global_py - radius_y - search_margin,
            0,
            height - 1,
        )
        search_x2 = clamp(
            global_px + radius_x + search_margin + 1,
            1,
            width,
        )
        search_y2 = clamp(
            global_py + radius_y + search_margin + 1,
            1,
            height,
        )

        real_roi = real_edges[
            search_y1:search_y2,
            search_x1:search_x2,
        ]

        cad_roi = cad_template[
            search_y1:search_y2,
            search_x1:search_x2,
        ]

        if real_roi.size == 0 or cad_roi.size == 0:
            comparison = {
                "score": 0.0,
                "chamfer": 0.0,
                "coverage": 0.0,
                "dx": 0,
                "dy": 0,
            }
        else:
            comparison = compare_roi(real_roi, cad_roi)

        local_dx = int(comparison["dx"])
        local_dy = int(comparison["dy"])

        # =====================================================
        # 5. Aplicar realmente el desplazamiento local
        # =====================================================

        final_px = clamp(
            global_px + local_dx,
            0,
            width - 1,
        )
        final_py = clamp(
            global_py + local_dy,
            0,
            height - 1,
        )

        # El cuadro final usa el centro global + ajuste local.
        x1 = clamp(
            final_px - radius_x,
            0,
            width - 1,
        )
        y1 = clamp(
            final_py - radius_y,
            0,
            height - 1,
        )
        x2 = clamp(
            final_px + radius_x + 1,
            1,
            width,
        )
        y2 = clamp(
            final_py + radius_y + 1,
            1,
            height,
        )

        score = float(comparison["score"])

        if score >= OK_SCORE:
            item_status = "OK"
            color = (0, 200, 0)
        elif score >= REVIEW_SCORE:
            item_status = "REVISAR"
            color = (0, 165, 255)
        else:
            item_status = "FALLO"
            color = (0, 0, 255)

        # =====================================================
        # 6. Dibujar resultado
        # =====================================================

        cv2.rectangle(
            result,
            (x1, y1),
            (x2, y2),
            color,
            3,
            cv2.LINE_AA,
        )

        cv2.drawMarker(
            result,
            (final_px, final_py),
            color,
            cv2.MARKER_CROSS,
            12,
            2,
            cv2.LINE_AA,
        )

        # Línea de depuración para observar el ajuste local.
        if local_dx != 0 or local_dy != 0:
            cv2.line(
                result,
                (global_px, global_py),
                (final_px, final_py),
                (255, 255, 0),
                2,
                cv2.LINE_AA,
            )

            cv2.circle(
                result,
                (global_px, global_py),
                3,
                (255, 0, 255),
                -1,
                cv2.LINE_AA,
            )

        label = (
            f"{roi_id} {component_type} "
            f"{item_status} {score:.1f} "
            f"({local_dx:+d},{local_dy:+d})"
        )

        text_size, baseline = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            1,
        )

        label_y = max(
            text_size[1] + 5,
            y1 - 5,
        )

        label_right = min(
            width - 1,
            x1 + text_size[0] + 8,
        )

        cv2.rectangle(
            result,
            (x1, label_y - text_size[1] - 5),
            (label_right, label_y + baseline + 3),
            color,
            -1,
        )

        cv2.putText(
            result,
            label,
            (x1 + 4, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

        inspection_results.append({
            "id": roi_id,
            "type": component_type,
            "status": item_status,
            "score": round(score, 2),
            "chamfer": round(float(comparison["chamfer"]), 2),
            "coverage": round(float(comparison["coverage"]), 2),

            "original_center_x_px": original_px,
            "original_center_y_px": original_py,

            "global_center_x_px": global_px,
            "global_center_y_px": global_py,

            "local_shift_x": local_dx,
            "local_shift_y": local_dy,

            "center_x_px": final_px,
            "center_y_px": final_py,
        })

    return result, inspection_results
def create_overlay(rectified: np.ndarray, cad_template: np.ndarray) -> np.ndarray:
    cad_color = np.zeros_like(rectified)
    cad_color[:, :, 2] = cad_template
    return cv2.addWeighted(rectified, 0.80, cad_color, 0.85, 0)


def calculate_overall_result(results: list[dict[str, Any]]) -> tuple[float, str, dict[str, int]]:
    if not results:
        return 0.0, "FALLO", {"OK": 0, "REVISAR": 0, "FALLO": 0}

    summary = {"OK": 0, "REVISAR": 0, "FALLO": 0}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1

    overall_score = sum(float(item["score"]) for item in results) / len(results)

    if summary["FALLO"] > 0:
        overall_status = "FALLO"
    elif summary["REVISAR"] > 0:
        overall_status = "REVISAR"
    else:
        overall_status = "OK"

    return round(overall_score, 2), overall_status, summary


# ============================================================
# SERVICIO
# ============================================================

class InspectService:
    @staticmethod
    def process_inspection(
        drawing: Drawing,
        photo_bytes: bytes,
    ) -> dict[str, Any]:
        roi_data = drawing.rois or {}
        rois = roi_data.get("items", [])

        if not rois:
            raise ValueError("El drawing no contiene ROIs guardados.")
        if not drawing.dxf_file:
            raise ValueError("El drawing no contiene el DXF guardado.")

        image = decode_image(photo_bytes)

        bounds = roi_data.get("bounds") or {}
        temporary_path = ""

        try:
            document, temporary_path = open_dxf_from_bytes(drawing.dxf_file)
            modelspace = document.modelspace()

            if all(key in bounds for key in ("xmin", "xmax", "ymin", "ymax")):
                xmin = float(bounds["xmin"])
                xmax = float(bounds["xmax"])
                ymin = float(bounds["ymin"])
                ymax = float(bounds["ymax"])
            else:
                xmin, xmax, ymin, ymax = get_dxf_bounds(modelspace)

            cad_width = xmax - xmin
            cad_height = ymax - ymin
            if cad_width <= 0 or cad_height <= 0:
                raise ValueError("Las dimensiones CAD no son validas.")

            expected_aspect = cad_width / cad_height
            rectified_height = int(round(RECTIFIED_WIDTH / expected_aspect))

            corners, panel_debug, _ = detect_panel_corners(image, expected_aspect)
            rectified, _ = rectify_panel(
                image,
                corners,
                RECTIFIED_WIDTH,
                rectified_height,
            )
            cad_template = draw_dxf_template(
                modelspace,
                xmin,
                xmax,
                ymin,
                ymax,
                RECTIFIED_WIDTH,
                rectified_height,
            )

            refinement = np.eye(3, dtype=np.float64)
            refinement_method = "IDENTIDAD"
            refinement_inliers = 0
            alignment_matches: list[dict[str, Any]] = []
            alignment_debug = rectified.copy()

            if REFINE_ALIGNMENT_WITH_INSERTS:
                (
                    expected_points,
                    detected_points,
                    alignment_matches,
                    alignment_debug,
                ) = collect_alignment_points(
                    rectified,
                    rois,
                    xmin,
                    xmax,
                    ymin,
                    ymax,
                    RECTIFIED_WIDTH,
                    rectified_height,
                )
                candidate, method, inliers = calculate_insert_refinement(
                    expected_points,
                    detected_points,
                )
                if validate_refinement_matrix(
                    candidate,
                    RECTIFIED_WIDTH,
                    rectified_height,
                ):
                    refinement = candidate
                    refinement_method = method
                    refinement_inliers = inliers

            cad_template_refined = cv2.warpPerspective(
                cad_template,
                refinement,
                (RECTIFIED_WIDTH, rectified_height),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
            _, real_edges = create_real_edge_image(rectified)
            result_image, results = inspect_rois(
                rectified,
                real_edges,
                cad_template_refined,
                rois,
                xmin,
                xmax,
                ymin,
                ymax,
                refinement,
                RECTIFIED_WIDTH,
                rectified_height,
            )
            overlay = create_overlay(rectified, cad_template_refined)
            overall_score, overall_status, summary = calculate_overall_result(results)

            return {
                "rectified_image": encode_png(rectified),
                "overlay_image": encode_png(result_image),
                "cad_overlay_image": encode_png(overlay),
                "panel_debug_image": encode_png(panel_debug),
                "alignment_debug_image": encode_png(alignment_debug),
                "score": overall_score,
                "status": overall_status,
                "results": {
                    "items": results,
                    "summary": summary,
                    "alignment": {
                        "method": refinement_method,
                        "inliers": refinement_inliers,
                        "matches": alignment_matches,
                    },
                    "bounds": {
                        "xmin": xmin,
                        "xmax": xmax,
                        "ymin": ymin,
                        "ymax": ymax,
                    },
                },
            }
        except ezdxf.DXFError as error:
            raise ValueError(f"El DXF guardado es invalido: {error}") from error
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.remove(temporary_path)

    @staticmethod
    async def inspect(
        session: Session,
        drawing_id: int,
        inspector: str,
        photo: UploadFile,
    ) -> Inspection:
        clean_inspector = inspector.strip()
        if not clean_inspector:
            raise HTTPException(status_code=422, detail="inspector es requerido.")

        drawing = session.get(Drawing, drawing_id)
        if drawing is None:
            raise HTTPException(status_code=404, detail="Drawing no encontrado.")

        photo_bytes = await photo.read()
        if not photo_bytes:
            raise HTTPException(status_code=400, detail="La imagen esta vacia.")

        try:
            processed = InspectService.process_inspection(drawing, photo_bytes)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=f"Error procesando la inspeccion: {error}",
            ) from error

        try:
            inspection = Inspection(
                drawing_id=drawing.id,
                inspector=clean_inspector,
                original_image=photo_bytes,
                rectified_image=processed["rectified_image"],
                # Tu modelo solo tiene un campo overlay_image. Aqui se guarda
                # la imagen final con rectangulos, etiquetas y resultados.
                overlay_image=processed["overlay_image"],
                score=processed["score"],
                status=processed["status"],
                results=processed["results"],
            )
            session.add(inspection)
            session.commit()
            session.refresh(inspection)
            return inspection
        except Exception as error:
            session.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Error guardando la inspeccion: {error}",
            ) from error
