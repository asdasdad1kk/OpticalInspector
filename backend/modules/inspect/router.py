from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session, select
from modules.camera.service import camera_service
# Ajusta estos imports a tu estructura real.
from models import Inspection,Part,Drawing
from core.db import get_session
from .service import InspectService


def parse_options(raw: str | None) -> dict | None:
    """Opciones de inspeccion (features activos, tolerancias) como JSON."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail=f"options no es JSON valido: {error}") from error
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="options debe ser un objeto JSON.")
    return value


router = APIRouter(
    prefix="/inspect",
    tags=["Inspect"],
)


class InspectionResponse(BaseModel):
    inspection_id: int
    drawing_id: int
    inspector: str
    score: float
    status: str
    roi_count: int
    summary: dict[str, int]
    alignment_method: str
    alignment_inliers: int
    message: str


@router.post(
    "",
    response_model=InspectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def inspect_panel(
    part_no: str = Form(...),
    inspector: str = Form(...),
    options: str | None = Form(default=None),
    photo: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> InspectionResponse:
    """
    Inspecciona una fotografia contra el DXF y ROIs guardados.

    multipart/form-data:
    - drawing_id: ID del Drawing previamente cargado
    - inspector: nombre o identificador del inspector
    - photo: fotografia JPG o PNG del panel
    """
    part_id=session.exec(select(Part.id).where(Part.part_number==part_no)).first()
    if not part_id:
        return InspectionResponse(
             message="Pieza no dada de alta."
        )
    drawing_id=session.exec(select(Drawing.id).where(Drawing.part_id==part_id)).first()
    inspection = await InspectService.inspect(
        session=session,
        drawing_id=drawing_id,
        inspector=inspector,
        photo=photo,
        options=parse_options(options),
    )

    result_data = inspection.results or {}
    items = result_data.get("items", [])
    summary = result_data.get("summary", {})
    alignment = result_data.get("alignment", {})

    return InspectionResponse(
        inspection_id=inspection.id,
        drawing_id=inspection.drawing_id,
        inspector=inspection.inspector,
        score=inspection.score,
        status=inspection.status,
        roi_count=len(items),
        summary=summary,
        alignment_method=alignment.get("method", "IDENTIDAD"),
        alignment_inliers=alignment.get("inliers", 0),
        message="Inspeccion procesada y guardada correctamente.",
    )



@router.post(
    "/capture",
    response_model=InspectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def inspect_capture(
    part_no: str = Form(...),
    inspector: str = Form(...),
    options: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> InspectionResponse:
    """
    Captura el último frame disponible de la RealSense
    y ejecuta la inspección automáticamente.
    """

    # ==========================================
    # BUSCAR PARTE
    # ==========================================

    part = session.exec(
        select(Part)
        .where(
            Part.part_number == part_no
        )
    ).first()

    if not part:
        return InspectionResponse(
            message="Pieza no dada de alta."
        )

    # ==========================================
    # BUSCAR DRAWING MAS RECIENTE
    # ==========================================

    drawing = session.exec(
        select(Drawing)
        .where(
            Drawing.part_id == part.id
        )
        .order_by(
            Drawing.id.desc()
        )
    ).first()

    if not drawing:
        return InspectionResponse(
            message="No existe Drawing para esta pieza."
        )

    # ==========================================
    # CAPTURAR IMAGEN DE REALSENSE
    # ==========================================

    try:

        photo_bytes = (
            camera_service.capture_png()
        )

    except Exception as error:

        raise HTTPException(
            status_code=409,
            detail=(
                "No existe una imagen disponible "
                f"de la cámara: {error}"
            ),
        ) from error

    # ==========================================
    # PROCESAR INSPECCION
    # ==========================================

    try:

        processed = (
            InspectService.process_inspection(
                drawing=drawing,
                photo_bytes=photo_bytes,
                options=parse_options(options),
            )
        )

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Error procesando inspección: "
                f"{error}"
            ),
        ) from error

    # ==========================================
    # GUARDAR INSPECCION
    # ==========================================

    try:

        inspection = Inspection(
            drawing_id=drawing.id,
            inspector=inspector,

            original_image=photo_bytes,

            rectified_image=processed[
                "rectified_image"
            ],

            overlay_image=processed[
                "overlay_image"
            ],

            score=processed[
                "score"
            ],

            status=processed[
                "status"
            ],

            results=processed[
                "results"
            ],
        )

        session.add(inspection)

        session.commit()

        session.refresh(
            inspection
        )

    except Exception as error:

        session.rollback()

        raise HTTPException(
            status_code=500,
            detail=(
                f"Error guardando inspección: "
                f"{error}"
            ),
        ) from error

    # ==========================================
    # RESPONSE
    # ==========================================

    result_data = (
        inspection.results or {}
    )

    items = result_data.get(
        "items",
        []
    )

    summary = result_data.get(
        "summary",
        {}
    )

    alignment = result_data.get(
        "alignment",
        {}
    )

    return InspectionResponse(
        inspection_id=inspection.id,

        drawing_id=inspection.drawing_id,

        inspector=inspection.inspector,

        score=inspection.score,

        status=inspection.status,

        roi_count=len(items),

        summary=summary,

        alignment_method=alignment.get(
            "method",
            "IDENTIDAD",
        ),

        alignment_inliers=alignment.get(
            "inliers",
            0,
        ),

        message=(
            "Captura e inspección "
            "completadas correctamente."
        ),
    )
@router.get("/{inspection_id}/original-image")
def get_original_image(
    inspection_id: int,
    session: Session = Depends(get_session),
):
    inspection = session.get(
        Inspection,
        inspection_id
    )

    if not inspection:
        raise HTTPException(
            status_code=404,
            detail="Inspection not found"
        )

    return Response(
        content=inspection.original_image,
        media_type="image/jpeg"
    )


@router.get("/{inspection_id}/rectified-image")
def get_rectified_image(
    inspection_id: int,
    session: Session = Depends(get_session),
):
    inspection = session.get(
        Inspection,
        inspection_id
    )

    if not inspection:
        raise HTTPException(
            status_code=404,
            detail="Inspection not found"
        )

    if not inspection.rectified_image:
        raise HTTPException(
            status_code=404,
            detail="Rectified image not available"
        )

    return Response(
        content=inspection.rectified_image,
        media_type="image/png"
    )


@router.get("/{inspection_id}/overlay-image")
def get_overlay_image(
    inspection_id: int,
    session: Session = Depends(get_session),
):
    inspection = session.get(
        Inspection,
        inspection_id
    )

    if not inspection:
        raise HTTPException(
            status_code=404,
            detail="Inspection not found"
        )

    if not inspection.overlay_image:
        raise HTTPException(
            status_code=404,
            detail="Overlay image not available"
        )

    return Response(
        content=inspection.overlay_image,
        media_type="image/png"
    )