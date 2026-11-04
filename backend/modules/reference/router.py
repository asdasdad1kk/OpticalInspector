from __future__ import annotations

import base64
import json
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlmodel import Session, select

from core.db import get_session
from models import Drawing, Part
from modules.inspect.golden import DEFAULT_ENABLED, analyze_reference, draw_reference_preview
from modules.inspect.calibration import calibration_service
from modules.inspect.service import decode_image, encode_png, prepare_image


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


@router.post("/analyze")
async def analyze(
    photo: UploadFile = File(...),
    known_length: float | None = Form(default=None),
    known_axis: str = Form(default="long"),
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

    try:
        raw = decode_image(data)
        image = calibration_service.undistort(raw)
        undistorted = image is not raw
        plane_scale = None
        if not known_length:
            plane_scale = calibration_service.plane_scale_for((image.shape[1], image.shape[0]))
        analysis = analyze_reference(image, known_length, known_axis, plane_scale)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Error analizando la pieza: {error}") from error
    payload = _serialize(analysis)
    if undistorted:
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
    if enabled["features"] and len(active) < 4:
        raise HTTPException(status_code=422, detail="Se necesitan al menos 4 features activos.")

    photo_bytes = await photo.read()
    try:
        image = prepare_image(photo_bytes)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    scale = float(payload.get("scale", 1.0)) or 1.0
    contour_px = np.float32([[x / scale, -y / scale] for x, y in payload.get("contour", [])])
    preview = draw_reference_preview(
        image,
        features,
        contour_px if len(contour_px) else np.zeros((0, 2), np.float32),
        None,
        scale,
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
