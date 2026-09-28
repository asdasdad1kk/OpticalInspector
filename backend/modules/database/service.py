from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from models import (
    Drawing,
    Inspection,
    Part,
)


class DatabaseService:
    # ========================================================
    # PARTES
    # ========================================================

    @staticmethod
    def get_parts(
        session: Session,
    ) -> dict[str, Any]:
        statement = (
            select(Part)
            .order_by(
                Part.created_at.desc(),
                Part.id.desc(),
                
            )
        )

        parts = session.exec(
            statement
        ).all()

        items = [
            {
                "id": part.id,
                "part_number": part.part_number,
                "revision": part.revision,
                "description": part.description,
                "created_at": part.created_at,
            }
            for part in parts
        ]

        return {
            "items": items,
            "total": len(items),
        }

    # ========================================================
    # DRAWINGS
    # ========================================================

    @staticmethod
    def get_drawings(
        session: Session,
    ) -> dict[str, Any]:
        statement = (
            select(
                Drawing,
                Part,
            )
            .join(
                Part,
                Drawing.part_id == Part.id,
            )
            .order_by(
                Drawing.created_at.desc(),
                Drawing.id.desc(),
            )
        )

        rows = session.exec(
            statement
        ).all()

        items: list[dict[str, Any]] = []

        for drawing, part in rows:
            roi_data = drawing.rois or {}

            roi_items = roi_data.get(
                "items",
                [],
            )

            summary = roi_data.get(
                "summary",
                {},
            )

            bounds = roi_data.get(
                "bounds",
                {},
            )

            items.append({
                "id": drawing.id,
                "part_id": part.id,

                "part_number": (
                    part.part_number
                ),

                "revision": part.revision,

                "description": (
                    part.description
                ),

                "file_name": (
                    drawing.file_name
                ),

                "width_mm": (
                    drawing.width_mm
                ),

                "height_mm": (
                    drawing.height_mm
                ),

                "roi_count": len(
                    roi_items
                ),

                "summary": summary,

                "bounds": bounds,

                "created_at": (
                    drawing.created_at
                ),
            })

        return {
            "items": items,
            "total": len(items),
        }

    # ========================================================
    # INSPECCIONES
    # ========================================================

    @staticmethod
    def get_inspections(
        session: Session,
    ) -> dict[str, Any]:
        statement = (
            select(
                Inspection,
                Drawing,
                Part,
            )
            .join(
                Drawing,
                Inspection.drawing_id
                == Drawing.id,
            )
            .join(
                Part,
                Drawing.part_id
                == Part.id,
            )
            .order_by(
                Inspection.created_at.desc(),
                Inspection.id.desc(),
            )
        )

        rows = session.exec(
            statement
        ).all()

        items: list[dict[str, Any]] = []

        for inspection, drawing, part in rows:
            result_data = (
                inspection.results or {}
            )

            result_items = result_data.get(
                "items",
                [],
            )

            summary = result_data.get(
                "summary",
                {},
            )

            alignment = result_data.get(
                "alignment",
                {},
            )

            items.append({
                "id": inspection.id,

                "drawing_id": drawing.id,

                "part_id": part.id,

                "part_number": (
                    part.part_number
                ),

                "revision": (
                    part.revision
                ),

                "inspector": (
                    inspection.inspector
                ),

                "score": (
                    inspection.score
                ),

                "status": (
                    inspection.status
                ),

                "roi_count": len(
                    result_items
                ),

                "summary": {
                    "OK": summary.get(
                        "OK",
                        0,
                    ),

                    "REVISAR": summary.get(
                        "REVISAR",
                        0,
                    ),

                    "FALLO": summary.get(
                        "FALLO",
                        0,
                    ),
                },

                "alignment_method": (
                    alignment.get(
                        "method",
                        "IDENTIDAD",
                    )
                ),

                "alignment_inliers": (
                    alignment.get(
                        "inliers",
                        0,
                    )
                ),

                "approved": (
                    inspection.approved
                ),

                "approved_by": (
                    inspection.approved_by
                ),

                "approved_at": (
                    inspection.approved_at
                ),

                "created_at": (
                    inspection.created_at
                ),
            })

        return {
            "items": items,
            "total": len(items),
        }

    # ========================================================
    # DETALLE DE PARTE
    # ========================================================

    @staticmethod
    def get_part_by_id(
        session: Session,
        part_id: int,
    ) -> dict[str, Any] | None:
        part = session.get(
            Part,
            part_id,
        )

        if part is None:
            return None

        drawings = session.exec(
            select(Drawing)
            .where(
                Drawing.part_id
                == part.id
            )
            .order_by(
                Drawing.created_at.desc(),
                Drawing.id.desc(),
            )
        ).all()

        drawing_items = []

        for drawing in drawings:
            roi_data = (
                drawing.rois or {}
            )

            drawing_items.append({
                "id": drawing.id,

                "file_name": (
                    drawing.file_name
                ),

                "width_mm": (
                    drawing.width_mm
                ),

                "height_mm": (
                    drawing.height_mm
                ),

                "roi_count": len(
                    roi_data.get(
                        "items",
                        [],
                    )
                ),

                "summary": (
                    roi_data.get(
                        "summary",
                        {},
                    )
                ),

                "created_at": (
                    drawing.created_at
                ),
            })

        return {
            "id": part.id,

            "part_number": (
                part.part_number
            ),

            "revision": part.revision,

            "description": (
                part.description
            ),

            "created_at": (
                part.created_at
            ),

            "drawings": drawing_items,
        }

    # ========================================================
    # DETALLE DE DRAWING
    # ========================================================

    @staticmethod
    def get_drawing_by_id(
        session: Session,
        drawing_id: int,
    ) -> dict[str, Any] | None:
        statement = (
            select(
                Drawing,
                Part,
            )
            .join(
                Part,
                Drawing.part_id == Part.id,
            )
            .where(
                Drawing.id == drawing_id
            )
        )

        row = session.exec(
            statement
        ).first()

        if row is None:
            return None

        drawing, part = row

        roi_data = drawing.rois or {}

        return {
            "id": drawing.id,

            "part_id": part.id,

            "part_number": (
                part.part_number
            ),

            "revision": part.revision,

            "description": (
                part.description
            ),

            "file_name": (
                drawing.file_name
            ),

            "width_mm": (
                drawing.width_mm
            ),

            "height_mm": (
                drawing.height_mm
            ),

            "roi_count": len(
                roi_data.get(
                    "items",
                    [],
                )
            ),

            "rois": roi_data.get(
                "items",
                [],
            ),

            "summary": roi_data.get(
                "summary",
                {},
            ),

            "bounds": roi_data.get(
                "bounds",
                {},
            ),

            "created_at": (
                drawing.created_at
            ),
        }

    # ========================================================
    # DETALLE DE INSPECCION
    # ========================================================

    @staticmethod
    def get_inspection_by_id(
        session: Session,
        inspection_id: int,
    ) -> dict[str, Any] | None:
        statement = (
            select(
                Inspection,
                Drawing,
                Part,
            )
            .join(
                Drawing,
                Inspection.drawing_id
                == Drawing.id,
            )
            .join(
                Part,
                Drawing.part_id
                == Part.id,
            )
            .where(
                Inspection.id
                == inspection_id
            )
        )

        row = session.exec(
            statement
        ).first()

        if row is None:
            return None

        inspection, drawing, part = row

        result_data = (
            inspection.results or {}
        )

        items = result_data.get(
            "items",
            [],
        )

        summary = result_data.get(
            "summary",
            {},
        )

        alignment = result_data.get(
            "alignment",
            {},
        )

        return {
            "id": inspection.id,

            "drawing_id": drawing.id,

            "part_id": part.id,

            "part_number": (
                part.part_number
            ),

            "revision": part.revision,

            "description": (
                part.description
            ),

            "inspector": (
                inspection.inspector
            ),

            "score": (
                inspection.score
            ),

            "status": (
                inspection.status
            ),

            "roi_count": len(items),

            "items": items,

            "summary": {
                "OK": summary.get(
                    "OK",
                    0,
                ),

                "REVISAR": summary.get(
                    "REVISAR",
                    0,
                ),

                "FALLO": summary.get(
                    "FALLO",
                    0,
                ),
            },

            "alignment": alignment,

            "approved": (
                inspection.approved
            ),

            "approved_by": (
                inspection.approved_by
            ),

            "approved_at": (
                inspection.approved_at
            ),

            "created_at": (
                inspection.created_at
            ),
        }