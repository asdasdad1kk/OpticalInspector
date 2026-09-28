from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from pydantic import BaseModel, Field

from sqlmodel import Session

from core.db import (
    get_session,
)

from .service import (
    DatabaseService,
)


router = APIRouter(
    prefix="/database",
    tags=["Database"],
)


# ============================================================
# SCHEMAS
# ============================================================

class PartResponse(BaseModel):
    id: int
    part_number: str
    revision: str
    description: str | None = None
    created_at: datetime


class DrawingResponse(BaseModel):
    id: int
    part_id: int

    part_number: str
    revision: str

    description: str | None = None

    file_name: str

    width_mm: float
    height_mm: float

    roi_count: int

    summary: dict[str, int] = Field(
        default_factory=dict
    )

    bounds: dict[str, Any] = Field(
        default_factory=dict
    )

    created_at: datetime


class InspectionSummaryResponse(
    BaseModel
):
    OK: int = 0
    REVISAR: int = 0
    FALLO: int = 0


class InspectionResponse(BaseModel):
    id: int

    drawing_id: int
    part_id: int

    part_number: str
    revision: str

    inspector: str

    score: float
    status: str

    roi_count: int

    summary: InspectionSummaryResponse

    alignment_method: str
    alignment_inliers: int

    approved: bool
    approved_by: str | None = None
    approved_at: datetime | None = None

    created_at: datetime


class PartsDatabaseResponse(
    BaseModel
):
    items: list[PartResponse]
    total: int


class DrawingsDatabaseResponse(
    BaseModel
):
    items: list[DrawingResponse]
    total: int


class InspectionsDatabaseResponse(
    BaseModel
):
    items: list[InspectionResponse]
    total: int


class PartDetailResponse(
    PartResponse
):
    drawings: list[dict[str, Any]]


class DrawingDetailResponse(
    DrawingResponse
):
    rois: list[dict[str, Any]]


class InspectionDetailResponse(
    BaseModel
):
    id: int

    drawing_id: int
    part_id: int

    part_number: str
    revision: str

    description: str | None = None

    inspector: str

    score: float
    status: str

    roi_count: int

    items: list[dict[str, Any]]

    summary: InspectionSummaryResponse

    alignment: dict[str, Any] = Field(
        default_factory=dict
    )

    approved: bool
    approved_by: str | None = None
    approved_at: datetime | None = None

    created_at: datetime


# ============================================================
# PARTES
# ============================================================

@router.get(
    "/parts",
    response_model=PartsDatabaseResponse,
)
def get_parts(
    session: Session = Depends(
        get_session
    ),
) -> PartsDatabaseResponse:
    data = DatabaseService.get_parts(
        session=session
    )

    return PartsDatabaseResponse(
        **data
    )


@router.get(
    "/parts/{part_id}",
    response_model=PartDetailResponse,
)
def get_part(
    part_id: int,
    session: Session = Depends(
        get_session
    ),
) -> PartDetailResponse:
    part = (
        DatabaseService.get_part_by_id(
            session=session,
            part_id=part_id,
        )
    )

    if part is None:
        raise HTTPException(
            status_code=404,
            detail="Parte no encontrada.",
        )

    return PartDetailResponse(
        **part
    )


# ============================================================
# DRAWINGS
# ============================================================

@router.get(
    "/drawings",
    response_model=DrawingsDatabaseResponse,
)
def get_drawings(
    session: Session = Depends(
        get_session
    ),
) -> DrawingsDatabaseResponse:
    data = (
        DatabaseService.get_drawings(
            session=session
        )
    )

    return DrawingsDatabaseResponse(
        **data
    )


@router.get(
    "/drawings/{drawing_id}",
    response_model=DrawingDetailResponse,
)
def get_drawing(
    drawing_id: int,
    session: Session = Depends(
        get_session
    ),
) -> DrawingDetailResponse:
    drawing = (
        DatabaseService
        .get_drawing_by_id(
            session=session,
            drawing_id=drawing_id,
        )
    )

    if drawing is None:
        raise HTTPException(
            status_code=404,
            detail="Drawing no encontrado.",
        )

    return DrawingDetailResponse(
        **drawing
    )


# ============================================================
# INSPECCIONES
# ============================================================

@router.get(
    "/inspections",
    response_model=(
        InspectionsDatabaseResponse
    ),
)
def get_inspections(
    session: Session = Depends(
        get_session
    ),
) -> InspectionsDatabaseResponse:
    data = (
        DatabaseService.get_inspections(
            session=session
        )
    )

    return (
        InspectionsDatabaseResponse(
            **data
        )
    )


@router.get(
    "/inspections/{inspection_id}",
    response_model=(
        InspectionDetailResponse
    ),
)
def get_inspection(
    inspection_id: int,
    session: Session = Depends(
        get_session
    ),
) -> InspectionDetailResponse:
    inspection = (
        DatabaseService
        .get_inspection_by_id(
            session=session,
            inspection_id=inspection_id,
        )
    )

    if inspection is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Inspección no encontrada."
            ),
        )

    return InspectionDetailResponse(
        **inspection
    )