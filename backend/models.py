from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field
from sqlalchemy import Column
from sqlalchemy.types import JSON


# ============================================================
# USUARIOS
# ============================================================

class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    username: str = Field(index=True, unique=True)
    full_name: str

    role: str = "operator"

    created_at: datetime = Field(
        default_factory=datetime.utcnow
    )


# ============================================================
# PARTES
# ============================================================

class Part(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    part_number: str = Field(index=True)
    revision: str

    description: Optional[str] = None

    created_at: datetime = Field(
        default_factory=datetime.utcnow
    )


# ============================================================
# DRAWING
# DXF + ROIS + PREVIEW
# ============================================================

class Drawing(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    part_id: int = Field(
        foreign_key="part.id"
    )

    file_name: str

    dxf_file: bytes

    preview_png: Optional[bytes] = None

    width_mm: float
    height_mm: float

    rois: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )

    created_at: datetime = Field(
            default_factory=datetime.utcnow
        )

class Inspection(SQLModel, table=True):
    id: int | None = Field(
        default=None,
        primary_key=True
    )

    drawing_id: int = Field(
        foreign_key="drawing.id"
    )

    inspector: str

    original_image: bytes

    rectified_image: Optional[bytes] = None

    overlay_image: Optional[bytes] = None

    score: float = 0

    status: str = "pending"

    results: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )

    approved: bool = False

    approved_by: Optional[str] = None

    approved_at: Optional[datetime] = None

    created_at: datetime = Field(
        default_factory=datetime.utcnow
    )