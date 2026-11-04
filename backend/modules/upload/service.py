from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any

import cv2
import ezdxf
import numpy as np
from fastapi import HTTPException, UploadFile
from sqlmodel import Session, select

# Ajusta estas rutas a la estructura real de tu proyecto.
from models import Drawing, Part


# ============================================================
# CONFIGURACION DEL PROCESAMIENTO DXF
# ============================================================

CENTER_TOLERANCE_MM = 0.75
ROI_MARGIN_MM = 0.0
CAD_PADDING_MM = 0.0
ROW_TOLERANCE_MM = 20.0
ARC_SEARCH_FACTOR = 1.15
MAX_FEATURE_RADIUS_MM: float | None = None
MIN_FEATURE_RADIUS_MM = 0.01
MIN_CONCENTRIC_CIRCLES = 1

WINDOW_WIDTH = 1800
WINDOW_HEIGHT = 1200
DRAW_ROWS = True

COLOR_CONTOUR = (120, 120, 120)
COLOR_ORIGINAL_CIRCLE = (180, 180, 180)
COLOR_ORIGINAL_ARC = (150, 150, 150)
COLOR_INSERT = (0, 165, 255)
COLOR_DELRON_CONICO = (255, 0, 255)
COLOR_DELRON_PLANO = (0, 200, 0)
COLOR_UNKNOWN = (0, 0, 255)
COLOR_ROW = (255, 100, 0)

TYPE_COLORS = {
    "INSERTO": COLOR_INSERT,
    "DELRON_CONICO": COLOR_DELRON_CONICO,
    "DELRON_PLANO": COLOR_DELRON_PLANO,
    "DESCONOCIDO": COLOR_UNKNOWN,
}


# ============================================================
# UTILIDADES
# ============================================================

def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def almost_same_center(
    entity_1: dict[str, Any],
    entity_2: dict[str, Any],
    tolerance: float,
) -> bool:
    return distance(
        entity_1["x"],
        entity_1["y"],
        entity_2["x"],
        entity_2["y"],
    ) <= tolerance


def cad_to_pixel(
    x: float,
    y: float,
    xmin: float,
    ymax: float,
    scale: float,
    offset_x: float,
    offset_y: float,
) -> tuple[int, int]:
    px = int(round((x - xmin) * scale + offset_x))
    py = int(round((ymax - y) * scale + offset_y))
    return px, py


def circle_point(
    cx: float,
    cy: float,
    radius: float,
    angle_degrees: float,
) -> tuple[float, float]:
    angle_radians = math.radians(angle_degrees)
    return (
        cx + radius * math.cos(angle_radians),
        cy + radius * math.sin(angle_radians),
    )


def normalize_angle(angle: float) -> float:
    return float(angle) % 360.0


def arc_span_degrees(start_angle: float, end_angle: float) -> float:
    start = float(start_angle)
    end = float(end_angle)
    raw_span = end - start

    if abs(raw_span) >= 359.999:
        return 360.0

    span = raw_span % 360.0
    return 360.0 if abs(span) < 0.001 else span


def angular_coverage(
    arcs: list[dict[str, Any]],
    angular_tolerance: float = 1.0,
) -> float:
    intervals: list[tuple[float, float]] = []

    for arc in arcs:
        start = normalize_angle(arc["start_angle"])
        span = arc_span_degrees(arc["start_angle"], arc["end_angle"])

        if span >= 359:
            return 360.0

        end = start + span
        if end <= 360:
            intervals.append((start, end))
        else:
            intervals.append((start, 360))
            intervals.append((0, end - 360))

    if not intervals:
        return 0.0

    intervals.sort()
    merged = [list(intervals[0])]

    for start, end in intervals[1:]:
        previous = merged[-1]
        if start <= previous[1] + angular_tolerance:
            previous[1] = max(previous[1], end)
        else:
            merged.append([start, end])

    return sum(end - start for start, end in merged)


# ============================================================
# LECTURA Y CLASIFICACION DEL DXF
# ============================================================

def convert_complete_arcs_to_circles(
    arcs: list[dict[str, Any]],
    center_tolerance: float = 0.25,
    radius_tolerance: float = 0.05,
    minimum_coverage: float = 350,
) -> tuple[list[dict[str, float]], list[dict[str, Any]]]:
    groups: list[dict[str, Any]] = []

    for arc in arcs:
        found = None

        for group in groups:
            center_distance = math.hypot(
                arc["x"] - group["x"],
                arc["y"] - group["y"],
            )

            if (
                center_distance <= center_tolerance
                and abs(arc["r"] - group["r"]) <= radius_tolerance
            ):
                found = group
                break

        if found is None:
            groups.append({
                "x": arc["x"],
                "y": arc["y"],
                "r": arc["r"],
                "arcs": [arc],
            })
        else:
            found["arcs"].append(arc)

    virtual_circles: list[dict[str, float]] = []
    detail_arcs: list[dict[str, Any]] = []

    for group in groups:
        coverage = angular_coverage(group["arcs"])

        if coverage >= minimum_coverage:
            virtual_circles.append({
                "x": group["x"],
                "y": group["y"],
                "r": group["r"],
            })
        else:
            detail_arcs.extend(group["arcs"])

    return virtual_circles, detail_arcs


def read_dxf_entities(modelspace: Any) -> tuple[list[dict[str, float]], list[dict[str, Any]]]:
    real_circles: list[dict[str, float]] = []
    arcs: list[dict[str, Any]] = []

    for entity in modelspace:
        entity_type = entity.dxftype()

        if entity_type == "CIRCLE":
            center = entity.dxf.center
            real_circles.append({
                "x": float(center.x),
                "y": float(center.y),
                "r": float(entity.dxf.radius),
            })

        elif entity_type == "ARC":
            center = entity.dxf.center
            arcs.append({
                "x": float(center.x),
                "y": float(center.y),
                "r": float(entity.dxf.radius),
                "start_angle": float(entity.dxf.start_angle),
                "end_angle": float(entity.dxf.end_angle),
            })

    virtual_circles, detail_arcs = convert_complete_arcs_to_circles(arcs)
    return real_circles + virtual_circles, detail_arcs


def group_concentric_circles(
    circles: list[dict[str, float]],
    tolerance: float,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []

    for circle in sorted(circles, key=lambda item: item["r"], reverse=True):
        assigned_group = None

        for group in groups:
            if almost_same_center(
                circle,
                {"x": group["x"], "y": group["y"]},
                tolerance,
            ):
                assigned_group = group
                break

        if assigned_group is None:
            groups.append({
                "x": circle["x"],
                "y": circle["y"],
                "circles": [circle],
            })
        else:
            assigned_group["circles"].append(circle)
            assigned_group["x"] = sum(
                item["x"] for item in assigned_group["circles"]
            ) / len(assigned_group["circles"])
            assigned_group["y"] = sum(
                item["y"] for item in assigned_group["circles"]
            ) / len(assigned_group["circles"])

    for group in groups:
        group["circles"].sort(key=lambda item: item["r"], reverse=True)
        unique_circles: list[dict[str, float]] = []

        for circle in group["circles"]:
            duplicated_radius = any(
                abs(circle["r"] - existing["r"]) <= 0.05
                for existing in unique_circles
            )
            if not duplicated_radius:
                unique_circles.append(circle)

        group["circles"] = unique_circles
        group["outer_radius"] = max(item["r"] for item in unique_circles)

    return groups


def assign_arcs_to_groups(
    groups: list[dict[str, Any]],
    arcs: list[dict[str, Any]],
) -> None:
    for group in groups:
        group["arcs"] = []
        search_radius = (
            group["outer_radius"] * ARC_SEARCH_FACTOR
            + CENTER_TOLERANCE_MM
        )

        for arc in arcs:
            center_distance = distance(
                group["x"],
                group["y"],
                arc["x"],
                arc["y"],
            )
            if center_distance <= search_radius:
                group["arcs"].append(arc)


def classify_group(group: dict[str, Any]) -> str:
    circle_count = len(group["circles"])
    detail_arc_count = len(group.get("arcs", []))

    if circle_count >= 2 and detail_arc_count >= 2:
        return "INSERTO"
    if circle_count >= 3:
        return "DELRON_CONICO"
    if circle_count in (1, 2):
        return "DELRON_PLANO"
    return "DESCONOCIDO"


def filter_feature_groups(
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    filtered_groups: list[dict[str, Any]] = []

    for group in groups:
        outer_radius = group["outer_radius"]
        circle_count = len(group["circles"])

        if outer_radius < MIN_FEATURE_RADIUS_MM:
            continue
        if MAX_FEATURE_RADIUS_MM is not None and outer_radius > MAX_FEATURE_RADIUS_MM:
            continue
        if circle_count < MIN_CONCENTRIC_CIRCLES:
            continue

        filtered_groups.append(group)

    return filtered_groups


def create_rows(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_groups = sorted(groups, key=lambda group: (-group["y"], group["x"]))
    rows: list[dict[str, Any]] = []

    for group in sorted_groups:
        assigned_row = None

        for row in rows:
            if abs(row["y"] - group["y"]) <= ROW_TOLERANCE_MM:
                assigned_row = row
                break

        if assigned_row is None:
            rows.append({"y": group["y"], "items": [group]})
        else:
            assigned_row["items"].append(group)
            assigned_row["y"] = sum(
                item["y"] for item in assigned_row["items"]
            ) / len(assigned_row["items"])

    rows.sort(key=lambda row: -row["y"])
    for row in rows:
        row["items"].sort(key=lambda group: group["x"])

    return rows


def create_rois(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rois: list[dict[str, Any]] = []
    roi_id = 1

    for row_number, row in enumerate(rows, start=1):
        for group in row["items"]:
            outer_radius = group["outer_radius"]
            roi_radius = outer_radius + ROI_MARGIN_MM
            component_type = classify_group(group)

            roi = {
                "id": roi_id,
                "part": row_number,
                "type": component_type,
                "center_x_mm": round(group["x"], 4),
                "center_y_mm": round(group["y"], 4),
                "outer_radius_mm": round(outer_radius, 4),
                "roi_margin_mm": ROI_MARGIN_MM,
                "xmin_mm": round(group["x"] - roi_radius, 4),
                "ymin_mm": round(group["y"] - roi_radius, 4),
                "xmax_mm": round(group["x"] + roi_radius, 4),
                "ymax_mm": round(group["y"] + roi_radius, 4),
                "circle_count": len(group["circles"]),
                "arc_count": len(group.get("arcs", [])),
                "circle_radii_mm": [
                    round(circle["r"], 4)
                    for circle in group["circles"]
                ],
            }

            rois.append(roi)
            group["roi_id"] = roi_id
            group["part"] = row_number
            group["type"] = component_type
            roi_id += 1

    return rois


# ============================================================
# LIMITES Y RENDER DEL DXF
# ============================================================

def get_dxf_bounds(
    modelspace: Any,
    feature_groups: list[dict[str, Any]],
) -> tuple[float, float, float, float]:
    all_x: list[float] = []
    all_y: list[float] = []

    for entity in modelspace:
        entity_type = entity.dxftype()

        try:
            if entity_type == "LINE":
                all_x.extend([
                    float(entity.dxf.start.x),
                    float(entity.dxf.end.x),
                ])
                all_y.extend([
                    float(entity.dxf.start.y),
                    float(entity.dxf.end.y),
                ])

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
                tool = entity.construction_tool()
                for point in tool.approximate(segments=100):
                    all_x.append(float(point.x))
                    all_y.append(float(point.y))

        except (AttributeError, TypeError, ValueError):
            continue

    for group in feature_groups:
        roi_radius = group["outer_radius"] + ROI_MARGIN_MM
        all_x.extend([group["x"] - roi_radius, group["x"] + roi_radius])
        all_y.extend([group["y"] - roi_radius, group["y"] + roi_radius])

    if not all_x or not all_y:
        raise ValueError("No se encontraron entidades utilizables en el DXF.")

    return (
        min(all_x) - CAD_PADDING_MM,
        max(all_x) + CAD_PADDING_MM,
        min(all_y) - CAD_PADDING_MM,
        max(all_y) + CAD_PADDING_MM,
    )


def draw_dxf_arc(
    image: np.ndarray,
    arc: dict[str, Any],
    xmin: float,
    ymax: float,
    scale: float,
    offset_x: float,
    offset_y: float,
    color: tuple[int, int, int],
    thickness: int = 1,
) -> None:
    start_angle = arc["start_angle"]
    end_angle = arc["end_angle"]

    if end_angle < start_angle:
        end_angle += 360.0

    angle_span = end_angle - start_angle
    number_of_points = max(12, int(angle_span / 3.0))
    pixel_points = []

    for angle in np.linspace(start_angle, end_angle, number_of_points):
        x, y = circle_point(arc["x"], arc["y"], arc["r"], angle)
        pixel_points.append(
            cad_to_pixel(x, y, xmin, ymax, scale, offset_x, offset_y)
        )

    if len(pixel_points) >= 2:
        cv2.polylines(
            image,
            [np.array(pixel_points, dtype=np.int32)],
            False,
            color,
            thickness,
            cv2.LINE_AA,
        )


def draw_dxf_geometry(
    image: np.ndarray,
    modelspace: Any,
    xmin: float,
    ymax: float,
    scale: float,
    offset_x: float,
    offset_y: float,
) -> None:
    for entity in modelspace:
        entity_type = entity.dxftype()

        try:
            if entity_type == "LINE":
                p1 = cad_to_pixel(
                    entity.dxf.start.x,
                    entity.dxf.start.y,
                    xmin,
                    ymax,
                    scale,
                    offset_x,
                    offset_y,
                )
                p2 = cad_to_pixel(
                    entity.dxf.end.x,
                    entity.dxf.end.y,
                    xmin,
                    ymax,
                    scale,
                    offset_x,
                    offset_y,
                )
                cv2.line(image, p1, p2, COLOR_CONTOUR, 1, cv2.LINE_AA)

            elif entity_type == "LWPOLYLINE":
                try:
                    for segment in entity.virtual_entities():
                        segment_type = segment.dxftype()

                        if segment_type == "LINE":
                            p1 = cad_to_pixel(
                                segment.dxf.start.x,
                                segment.dxf.start.y,
                                xmin,
                                ymax,
                                scale,
                                offset_x,
                                offset_y,
                            )
                            p2 = cad_to_pixel(
                                segment.dxf.end.x,
                                segment.dxf.end.y,
                                xmin,
                                ymax,
                                scale,
                                offset_x,
                                offset_y,
                            )
                            cv2.line(image, p1, p2, COLOR_CONTOUR, 1, cv2.LINE_AA)

                        elif segment_type == "ARC":
                            center = segment.dxf.center
                            draw_dxf_arc(
                                image,
                                {
                                    "x": float(center.x),
                                    "y": float(center.y),
                                    "r": float(segment.dxf.radius),
                                    "start_angle": float(segment.dxf.start_angle),
                                    "end_angle": float(segment.dxf.end_angle),
                                },
                                xmin,
                                ymax,
                                scale,
                                offset_x,
                                offset_y,
                                COLOR_CONTOUR,
                            )
                except Exception:
                    points = [
                        cad_to_pixel(
                            point[0],
                            point[1],
                            xmin,
                            ymax,
                            scale,
                            offset_x,
                            offset_y,
                        )
                        for point in entity.get_points()
                    ]
                    if len(points) >= 2:
                        cv2.polylines(
                            image,
                            [np.array(points, dtype=np.int32)],
                            bool(entity.closed),
                            COLOR_CONTOUR,
                            1,
                            cv2.LINE_AA,
                        )

            elif entity_type == "POLYLINE":
                points = [
                    cad_to_pixel(
                        vertex.dxf.location.x,
                        vertex.dxf.location.y,
                        xmin,
                        ymax,
                        scale,
                        offset_x,
                        offset_y,
                    )
                    for vertex in entity.vertices
                ]
                if len(points) >= 2:
                    cv2.polylines(
                        image,
                        [np.array(points, dtype=np.int32)],
                        bool(entity.is_closed),
                        COLOR_CONTOUR,
                        1,
                        cv2.LINE_AA,
                    )

            elif entity_type == "CIRCLE":
                center = entity.dxf.center
                point = cad_to_pixel(
                    center.x,
                    center.y,
                    xmin,
                    ymax,
                    scale,
                    offset_x,
                    offset_y,
                )
                radius_px = max(1, int(round(entity.dxf.radius * scale)))
                cv2.circle(
                    image,
                    point,
                    radius_px,
                    COLOR_ORIGINAL_CIRCLE,
                    1,
                    cv2.LINE_AA,
                )

            elif entity_type == "ARC":
                center = entity.dxf.center
                draw_dxf_arc(
                    image,
                    {
                        "x": float(center.x),
                        "y": float(center.y),
                        "r": float(entity.dxf.radius),
                        "start_angle": float(entity.dxf.start_angle),
                        "end_angle": float(entity.dxf.end_angle),
                    },
                    xmin,
                    ymax,
                    scale,
                    offset_x,
                    offset_y,
                    COLOR_ORIGINAL_ARC,
                )

            elif entity_type == "SPLINE":
                tool = entity.construction_tool()
                points = [
                    cad_to_pixel(
                        point.x,
                        point.y,
                        xmin,
                        ymax,
                        scale,
                        offset_x,
                        offset_y,
                    )
                    for point in tool.approximate(segments=100)
                ]
                if len(points) >= 2:
                    cv2.polylines(
                        image,
                        [np.array(points, dtype=np.int32)],
                        False,
                        COLOR_CONTOUR,
                        1,
                        cv2.LINE_AA,
                    )
        except Exception as error:
            print(f"No fue posible dibujar {entity_type}: {error}")


def draw_classified_rois(
    image: np.ndarray,
    groups: list[dict[str, Any]],
    xmin: float,
    ymax: float,
    scale: float,
    offset_x: float,
    offset_y: float,
) -> None:
    for group in groups:
        component_type = group["type"]
        color = TYPE_COLORS.get(component_type, COLOR_UNKNOWN)
        roi_radius = group["outer_radius"] + ROI_MARGIN_MM

        x1, y1 = cad_to_pixel(
            group["x"] - roi_radius,
            group["y"] + roi_radius,
            xmin,
            ymax,
            scale,
            offset_x,
            offset_y,
        )
        x2, y2 = cad_to_pixel(
            group["x"] + roi_radius,
            group["y"] - roi_radius,
            xmin,
            ymax,
            scale,
            offset_x,
            offset_y,
        )
        center_x, center_y = cad_to_pixel(
            group["x"],
            group["y"],
            xmin,
            ymax,
            scale,
            offset_x,
            offset_y,
        )

        cv2.rectangle(image, (x1, y1), (x2, y2), color, 3, cv2.LINE_AA)

        label = f'{group["roi_id"]} {component_type}'
        font_scale = max(0.45, min(0.75, scale / 10.0))
        text_size, baseline = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            2,
        )
        label_y = max(text_size[1] + 5, y1 - 7)

        cv2.rectangle(
            image,
            (x1, label_y - text_size[1] - 5),
            (x1 + text_size[0] + 8, label_y + baseline + 3),
            color,
            -1,
        )
        cv2.putText(
            image,
            label,
            (x1 + 4, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.drawMarker(
            image,
            (center_x, center_y),
            color,
            cv2.MARKER_CROSS,
            max(8, int(scale)),
            2,
            cv2.LINE_AA,
        )


def render_preview(
    modelspace: Any,
    feature_groups: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
) -> bytes:
    drawing_width = xmax - xmin
    drawing_height = ymax - ymin

    if drawing_width <= 0 or drawing_height <= 0:
        raise ValueError("Los limites calculados del DXF no son validos.")

    scale = min(WINDOW_WIDTH / drawing_width, WINDOW_HEIGHT / drawing_height)
    rendered_width = drawing_width * scale
    rendered_height = drawing_height * scale
    offset_x = (WINDOW_WIDTH - rendered_width) / 2.0
    offset_y = (WINDOW_HEIGHT - rendered_height) / 2.0

    image = np.full((WINDOW_HEIGHT, WINDOW_WIDTH, 3), 245, dtype=np.uint8)

    draw_dxf_geometry(
        image,
        modelspace,
        xmin,
        ymax,
        scale,
        offset_x,
        offset_y,
    )

    if DRAW_ROWS:
        for row_number, row in enumerate(rows, start=1):
            _, row_y_pixel = cad_to_pixel(
                xmin,
                row["y"],
                xmin,
                ymax,
                scale,
                offset_x,
                offset_y,
            )
            cv2.line(
                image,
                (0, row_y_pixel),
                (WINDOW_WIDTH, row_y_pixel),
                COLOR_ROW,
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                image,
                f"PARTE {row_number}",
                (15, max(30, row_y_pixel - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                COLOR_ROW,
                2,
                cv2.LINE_AA,
            )

    draw_classified_rois(
        image,
        feature_groups,
        xmin,
        ymax,
        scale,
        offset_x,
        offset_y,
    )

    legend_items = [
        ("INSERTO", COLOR_INSERT),
        ("DELRON CONICO", COLOR_DELRON_CONICO),
        ("DELRON PLANO", COLOR_DELRON_PLANO),
        ("DESCONOCIDO", COLOR_UNKNOWN),
    ]
    legend_x = 20
    legend_y = WINDOW_HEIGHT - 30

    for label, color in reversed(legend_items):
        cv2.rectangle(
            image,
            (legend_x, legend_y - 15),
            (legend_x + 22, legend_y + 5),
            color,
            -1,
        )
        cv2.putText(
            image,
            label,
            (legend_x + 32, legend_y + 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (30, 30, 30),
            2,
            cv2.LINE_AA,
        )
        legend_y -= 30

    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise ValueError("No fue posible generar el preview PNG.")

    return encoded.tobytes()


# ============================================================
# SERVICIO DE CARGA
# ============================================================

# Codigo $INSUNITS de AutoCAD -> milimetros por unidad.
INSUNITS_TO_MM = {1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 13: 0.001, 14: 100.0}
UNIT_NAME_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
_MM_KEYS = ("center_x_mm", "center_y_mm", "outer_radius_mm", "roi_margin_mm",
            "xmin_mm", "ymin_mm", "xmax_mm", "ymax_mm")


def dxf_unit_factor(dxf_bytes: bytes) -> tuple[float, str]:
    """Factor a mm segun $INSUNITS; si no esta declarada usa el ajuste del panel."""
    from core.settings import settings_store  # import perezoso

    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as handle:
            handle.write(dxf_bytes)
            temporary_path = handle.name
        code = int(ezdxf.readfile(temporary_path).header.get("$INSUNITS", 0) or 0)
    except Exception:
        code = 0
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
    if code in INSUNITS_TO_MM:
        return INSUNITS_TO_MM[code], f"INSUNITS={code}"
    unit = settings_store.get()["dxf"]["default_unit"]
    return UNIT_NAME_TO_MM[unit], f"sin unidad declarada; se asume {unit}"


def scale_processed_to_mm(processed: dict[str, Any], factor: float) -> None:
    """Convierte ROIs, limites y dimensiones del DXF a milimetros (in situ)."""
    if factor == 1.0:
        return
    for roi in processed["rois"]:
        for key in _MM_KEYS:
            if key in roi:
                roi[key] = round(float(roi[key]) * factor, 4)
        if "circle_radii_mm" in roi:
            roi["circle_radii_mm"] = [round(float(r) * factor, 4) for r in roi["circle_radii_mm"]]
    processed["bounds"] = {k: float(v) * factor for k, v in processed["bounds"].items()}
    processed["width_mm"] = float(processed["width_mm"]) * factor
    processed["height_mm"] = float(processed["height_mm"]) * factor


class UploadService:
    @staticmethod
    def process_dxf_bytes(dxf_bytes: bytes) -> dict[str, Any]:
        """
        Procesa el DXF recibido en memoria y retorna ROIs, limites y preview.
        ezdxf.readfile necesita una ruta, por eso se usa un archivo temporal.
        """
        if not dxf_bytes:
            raise ValueError("El archivo DXF esta vacio.")

        temporary_path: str | None = None

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".dxf",
                delete=False,
            ) as temporary_file:
                temporary_file.write(dxf_bytes)
                temporary_path = temporary_file.name

            document = ezdxf.readfile(temporary_path)
            modelspace = document.modelspace()

            circles, arcs = read_dxf_entities(modelspace)
            if not circles:
                raise ValueError("El DXF no contiene circulos utilizables.")

            groups = group_concentric_circles(circles, CENTER_TOLERANCE_MM)
            assign_arcs_to_groups(groups, arcs)
            feature_groups = filter_feature_groups(groups)

            if not feature_groups:
                raise ValueError("No se encontraron componentes circulares validos.")

            for group in feature_groups:
                group["type"] = classify_group(group)

            rows = create_rows(feature_groups)
            rois = create_rois(rows)
            xmin, xmax, ymin, ymax = get_dxf_bounds(modelspace, feature_groups)

            width_mm = xmax - xmin
            height_mm = ymax - ymin
            preview_png = render_preview(
                modelspace,
                feature_groups,
                rows,
                xmin,
                xmax,
                ymin,
                ymax,
            )

            type_summary: dict[str, int] = {}
            for roi in rois:
                roi_type = roi["type"]
                type_summary[roi_type] = type_summary.get(roi_type, 0) + 1

            return {
                "rois": rois,
                "preview_png": preview_png,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "bounds": {
                    "xmin": xmin,
                    "xmax": xmax,
                    "ymin": ymin,
                    "ymax": ymax,
                },
                "summary": type_summary,
            }

        except ezdxf.DXFError as error:
            raise ValueError(f"DXF invalido o corrupto: {error}") from error
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.remove(temporary_path)

    @staticmethod
    async def upload_drawing(
        session: Session,
        dxf: UploadFile,
        part_no: str,
        revision: str,
        description: str | None,
    ) -> Drawing:
        """
        Recibe el multipart/form-data, procesa el DXF y guarda:
        - Part
        - Drawing
        - DXF original como bytes
        - Preview PNG como bytes
        - ROIs y metadatos dentro del JSON
        """
        clean_part_no = part_no.strip()
        clean_revision = revision.strip()
        clean_description = description.strip() if description else None
        file_name = Path(dxf.filename or "drawing.dxf").name

        if not clean_part_no:
            raise HTTPException(status_code=422, detail="part_no es requerido.")
        if not clean_revision:
            raise HTTPException(status_code=422, detail="revision es requerida.")
        if Path(file_name).suffix.lower() != ".dxf":
            raise HTTPException(status_code=400, detail="El archivo debe ser .dxf.")

        dxf_bytes = await dxf.read()

        if not dxf_bytes:
            raise HTTPException(status_code=400, detail="El archivo DXF esta vacio.")

        try:
            processed = UploadService.process_dxf_bytes(dxf_bytes)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=f"Error procesando el DXF: {error}",
            ) from error

        unit_factor, unit_note = dxf_unit_factor(dxf_bytes)
        scale_processed_to_mm(processed, unit_factor)

        try:
            statement = select(Part).where(
                Part.part_number == clean_part_no,
                Part.revision == clean_revision,
            )
            part = session.exec(statement).first()

            if part is None:
                part = Part(
                    part_number=clean_part_no,
                    revision=clean_revision,
                    description=clean_description,
                )
                session.add(part)
                session.flush()
            elif clean_description is not None:
                part.description = clean_description
                session.add(part)

            drawing = Drawing(
                part_id=part.id,
                file_name=file_name,
                dxf_file=dxf_bytes,
                preview_png=processed["preview_png"],
                width_mm=processed["width_mm"],
                height_mm=processed["height_mm"],
                # Tu modelo declara rois como dict, por eso la lista va en "items".
                rois={
                    "items": processed["rois"],
                    "bounds": processed["bounds"],
                    "summary": processed["summary"],
                    # Los valores de arriba ya estan en mm; el DXF conserva sus unidades.
                    "unit_factor": unit_factor,
                    "unit_note": unit_note,
                },
            )

            session.add(drawing)
            session.commit()
            session.refresh(drawing)
            return drawing

        except HTTPException:
            session.rollback()
            raise
        except Exception as error:
            session.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Error guardando el drawing: {error}",
            ) from error
