from __future__ import annotations

import base64
import json
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlmodel import Session, select

from core import config, storage
from core.db import get_session
from models import Drawing, Part
from modules.camera import scans
from modules.inspect.pipeline import LARGE_HOLE_FRACTION, MIN_ANCHORS
from modules.reading import service as reading_service
from modules.inspect.golden import DEFAULT_ENABLED, analyze_reference, draw_reference_preview, rescale_analysis
from modules.reference import relief as relief_lib
from modules.inspect.calibration import calibration_service
from modules.inspect.service import decode_image, encode_png, prepare_image


PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47])

router = APIRouter(
    prefix="/reference",
    tags=["Reference"],
)


def _serialize(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "features": analysis["features"],
        "contour": analysis["contour"],
        "bounds": analysis["bounds"],
        "label": analysis["label"],
        "dimensions": analysis["dimensions"],
        "scale": analysis["scale"],
        "relative": analysis["relative"],
        "enabled": analysis["enabled"],
        "scale_source": analysis.get("scale_source", "dimension"),
        "preview_png": base64.b64encode(encode_png(analysis["preview"])).decode("ascii"),
    }


def _parse_crop(raw: str | None, width: int, height: int) -> tuple[int, int, int, int] | None:
    """Recorte [x, y, ancho, alto] en pixeles de la imagen; ValueError si no es usable."""
    if not raw:
        return None
    try:
        x, y, w, h = (int(round(float(value))) for value in json.loads(raw))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("El recorte es inválido.") from error
    x, y = max(0, x), max(0, y)
    w, h = min(w, width - x), min(h, height - y)
    if w < 160 or h < 120 or w * h < 0.03 * width * height:
        raise ValueError("El recorte es demasiado pequeño: debe contener varios features y al menos 3 % de la imagen.")
    return x, y, w, h


def _crop_scan(scan: dict[str, Any], rect: tuple[int, int, int, int]) -> dict[str, Any]:
    """Recorta color y profundidad a la vez y mueve el punto principal de los intrinsecos."""
    x, y, w, h = rect
    depth = scan["depth_mm"][y:y + h, x:x + w]
    intrinsics = {**scan["intrinsics"], "ppx": scan["intrinsics"]["ppx"] - x, "ppy": scan["intrinsics"]["ppy"] - y,
                  "width": w, "height": h}
    return {**scan, "color": scan["color"][y:y + h, x:x + w], "depth_mm": depth, "intrinsics": intrinsics,
            "depth_raw": relief_lib.pack_depth(depth)}


def _measure_relief(scan: dict[str, Any], analysis: dict[str, Any], use_depth_scale: bool) -> dict[str, Any]:
    """Mide el relieve del escaneo con el analisis actual; con `use_depth_scale` pasa el
    analisis (en pixeles) a milimetros reales y le agrega la altura de cada feature."""
    scale = float(analysis["scale"]) or 1.0
    result = relief_lib.build_relief(
        scan["depth_mm"], scan["intrinsics"],
        relief_lib.contour_to_px(analysis["contour"], scale),
        relief_lib.features_to_px(analysis["features"], scale),
        noise_mm=scan.get("noise_mm"),
    )
    if not result["available"]:
        return result
    if use_depth_scale and analysis.get("relative"):
        rescale_analysis(analysis, result["measures"]["scale_mm_per_px"], "profundidad")
    for feature in analysis["features"]:
        measured = result["feature_heights"].get(str(feature["id"]))
        if measured:
            feature["height_mm"] = measured["height_mm"]
            feature["height_reliable"] = measured["reliable"]
    return relief_lib.to_model(result, analysis["scale"])


@router.post("/analyze")
async def analyze(
    photo: UploadFile = File(...),
    known_length: float | None = Form(default=None),
    known_axis: str = Form(default="long"),
    scan_id: str | None = Form(default=None),
    crop: str | None = Form(default=None, description="JSON [x, y, ancho, alto] de la zona a inspeccionar"),
) -> dict[str, Any]:
    """Analiza una captura de la pieza real y devuelve todos sus features.

    No guarda nada: el cliente revisa/edita y luego llama a /save.
    known_length: dimension real (mm) del lado largo (o corto) de la pieza;
    sin ella las unidades quedan en pixeles (modo relativo).
    """
    data = await photo.read()
    if not data:
        raise HTTPException(status_code=400, detail="La imagen esta vacia.")
    if known_axis not in ("long", "short"):
        raise HTTPException(status_code=422, detail="known_axis debe ser 'long' o 'short'.")
    if known_length is not None and known_length <= 0:
        raise HTTPException(status_code=422, detail="known_length debe ser mayor que cero.")

    scan = None
    if scan_id:
        try:
            scan = scans.load_scan(scan_id)
        except scans.ScanNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    rect: tuple[int, int, int, int] | None = None
    try:
        if scan is not None:
            # Con escaneo 3D se usan los intrinsecos de fabrica de la RealSense: no se aplica
            # la calibracion de lente del usuario, para que color y profundidad sigan alineados.
            rect = _parse_crop(crop, scan["color"].shape[1], scan["color"].shape[0])
            if rect:
                scan = _crop_scan(scan, rect)
            raw = image = scan["color"]
            undistorted = False
            if rect and known_length:
                raise ValueError("Con recorte no se puede indicar una dimensión conocida de la pieza completa.")
            analysis = analyze_reference(image, known_length, known_axis, None)
        else:
            raw = decode_image(data)
            image = calibration_service.undistort(raw)
            undistorted = image is not raw
            plane_scale = None
            if not known_length:
                plane_scale = calibration_service.plane_scale_for((image.shape[1], image.shape[0]))
            rect = _parse_crop(crop, image.shape[1], image.shape[0])
            if rect:
                if known_length:
                    raise ValueError("Con recorte no se puede indicar una dimensión conocida de la pieza completa.")
                x, y, w, h = rect
                image = image[y:y + h, x:x + w]
            analysis = analyze_reference(image, known_length, known_axis, plane_scale)
    except ValueError as error:
        hint = " Amplíe la zona recortada." if rect and "recorte" not in str(error).lower() else ""
        raise HTTPException(status_code=400, detail=f"{error}{hint}") from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Error analizando la pieza: {error}") from error
    relief_payload: dict[str, Any] | None = None
    if scan is not None:
        try:
            relief_payload = _measure_relief(scan, analysis, use_depth_scale=not known_length)
        except Exception as error:  # la medicion de profundidad nunca debe impedir el alta
            relief_payload = {"available": False, "reason": f"No se pudo medir el relieve: {error}"}
    if rect:
        # El contorno y la etiqueta de una pieza recortada no son los de la pieza completa:
        # arrancan desactivados (el usuario puede volver a activarlos).
        analysis["enabled"] = {**analysis["enabled"], "contour": False, "label": False}
    payload = _serialize(analysis)
    if rect:
        payload["crop"] = list(rect)
    if relief_payload is not None:
        payload["relief"] = relief_payload
        payload["scan_id"] = scan_id
    if undistorted or rect:
        # Las coordenadas estan en la imagen corregida: el cliente debe mostrarla.
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        payload["image_jpeg"] = base64.b64encode(encoded.tobytes()).decode("ascii") if ok else None
    return payload


@router.post("/save", status_code=status.HTTP_201_CREATED)
async def save(
    part_no: str = Form(...),
    revision: str = Form(...),
    description: str | None = Form(default=None),
    reference: str = Form(..., description="JSON editado devuelto por /analyze"),
    photo: UploadFile = File(...),
    scan_id: str | None = Form(default=None),
    crop: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Guarda la pieza patron (features editados) como un Drawing de origen real."""
    clean_part = part_no.strip()
    clean_revision = revision.strip()
    if not clean_part or not clean_revision:
        raise HTTPException(status_code=422, detail="part_no y revision son requeridos.")

    try:
        payload = json.loads(reference)
        features = payload["features"]
        bounds = payload["bounds"]
        dimensions = payload["dimensions"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise HTTPException(status_code=422, detail=f"reference invalido: {error}") from error

    active = [f for f in features if f.get("enabled", True)]
    enabled = {**DEFAULT_ENABLED, **(payload.get("enabled") or {})}
    try:
        # Zonas de lectura (codigos / texto) en unidades de la referencia, como los features.
        readings = reading_service.normalize_zones(payload.get("readings") or [], min_size=0.0)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if enabled["features"] and len(active) < MIN_ANCHORS:
        raise HTTPException(status_code=422, detail="Se necesitan al menos 4 features activos.")

    scan = None
    if scan_id:
        try:
            scan = scans.load_scan(scan_id)
        except scans.ScanNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    photo_bytes = await photo.read()
    try:
        image = scan["color"] if scan is not None else prepare_image(photo_bytes)
        rect = _parse_crop(crop, image.shape[1], image.shape[0])
        if rect:
            if scan is not None:
                scan = _crop_scan(scan, rect)
                image = scan["color"]
            else:
                x, y, w, h = rect
                image = image[y:y + h, x:x + w]
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    # Los huecos grandes no alinean: hacen falta al menos MIN_ANCHORS features pequenos activos.
    anchor_scale = float(payload.get("scale", 1.0)) or 1.0
    long_side_px = max(image.shape[:2])
    anchors = [
        f for f in active
        if f["type"] != "BARRENO" or f["radius"] / anchor_scale < LARGE_HOLE_FRACTION * long_side_px
    ]
    if enabled["features"] and len(anchors) < MIN_ANCHORS:
        raise HTTPException(
            status_code=422,
            detail=f"Solo hay {len(anchors)} features pequeños activos; se necesitan al menos {MIN_ANCHORS} para "
                   "alinear (un hueco grande no sirve para alinear).",
        )

    scale = float(payload.get("scale", 1.0)) or 1.0
    # Foto limpia (sin anotaciones) como textura del modelo 3D; sus coordenadas son las del analisis.
    photo_path: str | None = None
    ok, encoded_photo = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if ok:
        photo_path = storage.save_bytes(config.IMAGES_DIR, encoded_photo.tobytes(), "reference", ".jpg")
    # Relieve medido: se recalcula con los features ya editados por el usuario.
    relief_json: dict[str, Any] | None = None
    depth_path: str | None = None
    if scan is not None:
        try:
            measured = relief_lib.build_relief(
                scan["depth_mm"], scan["intrinsics"],
                relief_lib.contour_to_px(payload.get("contour", []), scale),
                relief_lib.features_to_px(features, scale),
                noise_mm=scan.get("noise_mm"),
            )
        except Exception:
            measured = {"available": False}
        if measured.get("available"):
            for item in features:
                height = measured["feature_heights"].get(str(item["id"]))
                if height:
                    item["height_mm"], item["height_reliable"] = height["height_mm"], height["reliable"]
            relief_json = relief_lib.to_model(measured, scale)
            relief_json.pop("feature_heights", None)
            depth_path = storage.save_bytes(config.IMAGES_DIR, scan["depth_raw"], "depth", ".npz")
    contour_px = np.float32([[x / scale, -y / scale] for x, y in payload.get("contour", [])])
    preview = draw_reference_preview(
        image,
        features,
        contour_px if len(contour_px) else np.zeros((0, 2), np.float32),
        None,
        scale,
    )

    relief_path: str | None = None
    if relief_json is not None:
        relief_path = storage.save_bytes(
            config.IMAGES_DIR, json.dumps(relief_json).encode("utf-8"), "relief", ".json"
        )

    try:
        part = session.exec(
            select(Part).where(Part.part_number == clean_part, Part.revision == clean_revision)
        ).first()
        if part is None:
            part = Part(part_number=clean_part, revision=clean_revision,
                        description=description.strip() if description else None)
            session.add(part)
            session.flush()

        summary: dict[str, int] = {}
        for item in features:
            summary[item["type"]] = summary.get(item["type"], 0) + (1 if item.get("enabled", True) else 0)

        drawing = Drawing(
            part_id=part.id,
            file_name=f"pieza_real_{clean_part}_{clean_revision}.jpg",
            dxf_file=b"",
            preview_png=encode_png(preview),
            width_mm=float(dimensions["length"]),
            height_mm=float(dimensions["width"]),
            rois={
                "source": "golden",
                "items": features,
                "bounds": bounds,
                "summary": summary,
                "golden": {
                    "contour": payload.get("contour", []),
                    "label": payload.get("label"),
                    "dimensions": dimensions,
                    "scale": scale,
                    "relative": bool(payload.get("relative", False)),
                    "enabled": enabled,
                    "readings": readings,
                    "photo_path": photo_path,
                    "crop": list(rect) if rect else None,
                    "image_size": [int(image.shape[1]), int(image.shape[0])],
                    "relief_path": relief_path,
                    "depth_path": depth_path,
                    "scan_intrinsics": scan["intrinsics"] if scan is not None and relief_json else None,
                },
            },
        )
        # La captura original queda disponible como referencia visual.
        session.add(drawing)
        session.commit()
        session.refresh(drawing)
    except Exception as error:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Error guardando la pieza patron: {error}") from error

    return {
        "drawing_id": drawing.id,
        "part_id": part.id,
        "part_no": clean_part,
        "revision": clean_revision,
        "description": part.description,
        "file_name": drawing.file_name,
        "width_mm": drawing.width_mm,
        "height_mm": drawing.height_mm,
        "roi_count": len(active),
        "summary": summary,
        "message": "Pieza patron guardada correctamente.",
    }


# ============================================================
# MODELO 3D
# ============================================================

@router.get("/{drawing_id}/model")
def get_model(drawing_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Datos para reconstruir la pieza en 3D (contorno, features, foto de textura).

    Piezas de pieza real: contorno medido y foto limpia. Piezas de DXF: panel
    rectangular con los features del CAD y sin textura. Coordenadas con Y hacia arriba.
    """
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing no encontrado.")
    part = session.get(Part, drawing.part_id)
    rois = drawing.rois or {}
    items = [item for item in rois.get("items", []) if item.get("enabled", True)]
    bounds = rois.get("bounds") or {}
    golden = rois.get("golden")

    if golden:
        contour = golden.get("contour") or []
        dims = golden.get("dimensions") or {}
        unit = dims.get("unit", "mm")
        scale = float(golden.get("scale", 1.0)) or 1.0
        texture = None
        if golden.get("photo_path") and golden.get("image_size"):
            texture = {
                "url": f"/api/reference/{drawing_id}/photo",
                "width": golden["image_size"][0],
                "height": golden["image_size"][1],
                "scale": scale,
            }
    else:
        if not bounds:
            raise HTTPException(status_code=409, detail="La pieza no tiene dimensiones para reconstruirla.")
        contour = [
            [bounds["xmin"], bounds["ymin"]], [bounds["xmax"], bounds["ymin"]],
            [bounds["xmax"], bounds["ymax"]], [bounds["xmin"], bounds["ymax"]],
        ]
        unit, texture = "mm", None

    relief = None
    if golden and golden.get("relief_path"):
        try:
            relief = json.loads(storage.read_bytes(config.IMAGES_DIR, golden["relief_path"]))
        except (OSError, ValueError):
            relief = None

    return {
        "drawing_id": drawing_id,
        "part_number": part.part_number if part else "",
        "revision": part.revision if part else "",
        "source": "golden" if golden else "dxf",
        "unit": unit,
        "contour": contour,
        "bounds": bounds,
        "features": items,
        "texture": texture,
        "relief": relief,
    }


@router.get("/{drawing_id}/photo")
def get_photo(drawing_id: int, session: Session = Depends(get_session)) -> Response:
    drawing = session.get(Drawing, drawing_id)
    golden = (drawing.rois or {}).get("golden") if drawing else None
    path = golden.get("photo_path") if golden else None
    if not path:
        raise HTTPException(status_code=404, detail="Esta pieza no tiene foto de referencia.")
    try:
        content = storage.read_bytes(config.IMAGES_DIR, path)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Foto de referencia no disponible.") from error
    return Response(content=content, media_type="image/png" if content[:4] == PNG_MAGIC else "image/jpeg")
