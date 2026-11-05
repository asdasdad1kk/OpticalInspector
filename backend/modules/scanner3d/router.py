from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .engine import ScannerUnavailable
from .service import EXPORT_FORMATS, ScannerBusy, scanner_service

router = APIRouter(prefix="/scanner", tags=["Scanner3D"])


class StartBody(BaseModel):
    voxel_mm: float = Field(default=2.0, ge=0.5, le=10.0, description="Resolución del modelo")
    max_depth_mm: float = Field(default=1000.0, ge=300.0, le=4000.0, description="Distancia máxima que se fusiona")


class FinishBody(BaseModel):
    isolate: bool = Field(default=False, description="Quitar la mesa y dejar solo la pieza")


class SaveBody(BaseModel):
    name: str = ""


def _conflict(error: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


@router.get("/status")
def status() -> dict[str, Any]:
    return scanner_service.status()


@router.post("/start")
def start(body: StartBody) -> dict[str, Any]:
    try:
        return scanner_service.start(body.voxel_mm, body.max_depth_mm)
    except ScannerUnavailable as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    except (RuntimeError, ScannerBusy) as error:
        raise _conflict(error) from error


@router.post("/pause")
def pause() -> dict[str, Any]:
    return scanner_service.pause()


@router.post("/resume")
def resume() -> dict[str, Any]:
    return scanner_service.resume()


@router.post("/finish")
def finish(body: FinishBody) -> dict[str, Any]:
    try:
        return scanner_service.finish(body.isolate)
    except RuntimeError as error:
        raise _conflict(error) from error


@router.post("/reprocess")
def reprocess(body: FinishBody) -> dict[str, Any]:
    try:
        return scanner_service.reprocess(body.isolate)
    except RuntimeError as error:
        raise _conflict(error) from error


@router.post("/reset")
def reset() -> dict[str, Any]:
    return scanner_service.reset()


@router.get("/preview.bin")
def preview(max_points: int = Query(default=60000, ge=1000, le=300000)) -> Response:
    return Response(
        content=scanner_service.preview_bytes(max_points),
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/mesh.bin")
def mesh(max_triangles: int = Query(default=300000, ge=10000, le=2000000)) -> Response:
    try:
        return Response(
            content=scanner_service.mesh_bytes(max_triangles),
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )
    except RuntimeError as error:
        raise _conflict(error) from error


@router.get("/export.{fmt}")
def export(fmt: str) -> Response:
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=404, detail=f"Formato no soportado. Use: {', '.join(EXPORT_FORMATS)}.")
    try:
        data, media_type, filename = scanner_service.export(fmt)
    except (RuntimeError, ScannerUnavailable) as error:
        raise _conflict(error) from error
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/saved")
def saved() -> list[dict[str, Any]]:
    return scanner_service.list_saved()


@router.post("/save")
def save(body: SaveBody) -> dict[str, Any]:
    try:
        return scanner_service.save(body.name)
    except (RuntimeError, ScannerUnavailable) as error:
        raise _conflict(error) from error


@router.post("/saved/{scan_id}/open")
def open_saved(scan_id: str) -> dict[str, Any]:
    try:
        return scanner_service.load_saved(scan_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, ScannerBusy) as error:
        raise _conflict(error) from error


@router.delete("/saved/{scan_id}")
def delete_saved(scan_id: str) -> dict[str, bool]:
    try:
        scanner_service.delete_saved(scan_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"ok": True}
