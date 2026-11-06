"""API de lectura (`/api/reading`): estado de los motores y prueba de lectura sobre una foto."""
from __future__ import annotations

import json
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from . import service

router = APIRouter(prefix="/reading", tags=["Lectura"])


@router.get("/capabilities")
def capabilities(refresh: bool = False) -> dict[str, Any]:
    return service.capabilities(refresh)


@router.post("/test")
async def test_read(
    photo: UploadFile = File(...),
    crop: str | None = Form(None),
    text: bool = Form(False),
) -> dict[str, Any]:
    """Lee codigos (y texto si `text`) de una foto o de un recorte: para probar la configuracion."""
    data = await photo.read()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    if image is None:
        raise HTTPException(status_code=400, detail="No se pudo leer la foto.")
    if crop:
        try:
            box = json.loads(crop)
            x, y, w, h = (int(round(float(box[k]))) for k in ("x", "y", "w", "h"))
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(status_code=400, detail="Recorte invalido.") from error
        image = image[max(0, y):max(0, y) + h, max(0, x):max(0, x) + w]
        if image.size == 0:
            raise HTTPException(status_code=400, detail="El recorte queda fuera de la foto.")
    return service.read_anything(image, text)
