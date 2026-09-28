from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session

# Ajusta estas rutas a la estructura real de tu proyecto.
from core.db import get_session
from .service import UploadService
from models import Drawing

router = APIRouter(
    prefix="/upload",
    tags=["Upload"],
)


class UploadDrawingResponse(BaseModel):
    drawing_id: int
    part_id: int
    part_no: str
    revision: str
    description: str | None
    file_name: str
    width_mm: float
    height_mm: float
    roi_count: int
    summary: dict[str, int]
    message: str


@router.post(
    "/dxf",
    response_model=UploadDrawingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_dxf(
    dxf: UploadFile = File(...),
    part_no: str = Form(...),
    revision: str = Form(...),
    description: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> UploadDrawingResponse:
    """
    Carga un DXF, genera los ROIs y guarda todo en SQLite.

    multipart/form-data:
    - dxf: archivo .dxf
    - part_no: numero de parte
    - revision: revision del drawing
    - description: descripcion opcional
    """
    drawing = await UploadService.upload_drawing(
        session=session,
        dxf=dxf,
        part_no=part_no,
        revision=revision,
        description=description,
    )

    roi_data = drawing.rois or {}
    roi_items = roi_data.get("items", [])
    summary = roi_data.get("summary", {})

    return UploadDrawingResponse(
        drawing_id=drawing.id,
        part_id=drawing.part_id,
        part_no=part_no.strip(),
        revision=revision.strip(),
        description=description.strip() if description else None,
        file_name=drawing.file_name,
        width_mm=drawing.width_mm,
        height_mm=drawing.height_mm,
        roi_count=len(roi_items),
        summary=summary,
        message="DXF procesado y guardado correctamente.",
    )


@router.get("/{drawing_id}/processed-image")
def get_processed_image(
    drawing_id: int,
    session: Session = Depends(get_session),
):
    drawing = session.get(
        Drawing,
        drawing_id
    )

    if not drawing:
        raise HTTPException(
            status_code=404,
            detail="drawing not found"
        )

    if not drawing.preview_png:
        raise HTTPException(
            status_code=404,
            detail="Rectified image not available"
        )

    return Response(
        content=drawing.preview_png,
        media_type="image/png"
    )

