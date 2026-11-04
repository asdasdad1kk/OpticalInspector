"""Panel de configuracion (protegido con clave)."""
from __future__ import annotations

import shutil
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from core import config, storage
from core.db import get_session
from core.runtime import backup_database, list_backups
from core.settings import DXF_UNITS, require_config, settings_store
from models import Drawing, Inspection, Part
from modules.camera.service import camera_service
from modules.inspect.calibration import board_png, calibration_service
from modules.inspect.service import decode_image
from modules.reference import samples

router = APIRouter(prefix="/config", tags=["Config"])
protected = [Depends(require_config)]


# ============================================================
# SESION
# ============================================================

class LoginBody(BaseModel):
    password: str


class PasswordBody(BaseModel):
    current: str
    new: str


@router.post("/login")
def login(body: LoginBody) -> dict[str, Any]:
    token = settings_store.login(body.password)
    if token is None:
        raise HTTPException(status_code=401, detail="Clave incorrecta.")
    return {"token": token, "default_password": settings_store.is_default_password()}


@router.post("/logout")
def logout(token: str = Depends(require_config)) -> dict[str, bool]:
    settings_store.logout(token)
    return {"ok": True}


@router.get("/session", dependencies=protected)
def session_status() -> dict[str, Any]:
    return {"ok": True, "default_password": settings_store.is_default_password()}


@router.post("/password", dependencies=protected)
def change_password(body: PasswordBody) -> dict[str, bool]:
    if not settings_store.verify_password(body.current):
        raise HTTPException(status_code=403, detail="La clave actual no coincide.")
    try:
        settings_store.change_password(body.new)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"ok": True}


# ============================================================
# AJUSTES
# ============================================================

@router.get("/settings", dependencies=protected)
def get_settings() -> dict[str, Any]:
    data = settings_store.get()
    data.pop("password_is_default", None)
    data["dxf_units"] = list(DXF_UNITS)
    return data


@router.put("/settings/{section}", dependencies=protected)
def put_settings(section: str, values: dict[str, Any]) -> dict[str, Any]:
    try:
        settings_store.update_section(section, values)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return get_settings()


# ============================================================
# PIEZAS: ajustes propios y piezas buenas
# ============================================================

def _latest_drawings(session: Session) -> list[tuple[Part, Drawing]]:
    result = []
    for part in session.exec(select(Part).order_by(Part.part_number)).all():
        drawing = session.exec(
            select(Drawing).where(Drawing.part_id == part.id).order_by(Drawing.id.desc())
        ).first()
        if drawing is not None:
            result.append((part, drawing))
    return result


@router.get("/parts", dependencies=protected)
def list_parts(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    overrides = settings_store.get()["parts"]
    rows = []
    for part, drawing in _latest_drawings(session):
        rois = drawing.rois or {}
        golden = rois.get("golden", {})
        rows.append({
            "part_number": part.part_number,
            "revision": part.revision,
            "drawing_id": drawing.id,
            "source": rois.get("source", "dxf"),
            "features": len(rois.get("items", [])),
            "sample_count": golden.get("sample_count", 1) if rois.get("source") == "golden" else None,
            "samples": samples.sample_summary(drawing) if rois.get("source") == "golden" else None,
            "overrides": overrides.get(part.part_number, {}),
            "validation_images": len(samples.list_validation(part.part_number)),
        })
    return rows


@router.put("/parts/{part_number}/overrides", dependencies=protected)
def put_overrides(part_number: str, values: dict[str, Any]) -> dict[str, Any]:
    try:
        settings_store.set_part_overrides(part_number, values)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"part_number": part_number, "overrides": settings_store.get()["parts"].get(part_number, {})}


@router.post("/drawings/{drawing_id}/samples", dependencies=protected)
async def add_sample(
    drawing_id: int,
    photo: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    data = await photo.read()
    if not data:
        raise HTTPException(status_code=400, detail="La imagen esta vacia.")
    return samples.add_good_sample(session, drawing_id, data, settings_store.inspection_options())


@router.delete("/drawings/{drawing_id}/samples/{sample_id}", dependencies=protected)
def remove_sample(drawing_id: int, sample_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    return samples.delete_good_sample(session, drawing_id, sample_id)


# ============================================================
# SETS DE VALIDACION
# ============================================================

@router.get("/validation/{part_number}", dependencies=protected)
def validation_list(part_number: str) -> list[dict[str, Any]]:
    return samples.list_validation(part_number)


@router.post("/validation/{part_number}", dependencies=protected)
async def validation_add(
    part_number: str,
    expected: str = Form(...),
    note: str = Form(default=""),
    photo: UploadFile = File(...),
) -> dict[str, Any]:
    data = await photo.read()
    try:
        decode_image(data)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return samples.add_validation(part_number, expected, data, note)


@router.delete("/validation/{part_number}/{entry_id}", dependencies=protected)
def validation_delete(part_number: str, entry_id: str) -> dict[str, bool]:
    samples.delete_validation(part_number, entry_id)
    return {"ok": True}


@router.get("/validation/{part_number}/{entry_id}/image", dependencies=protected)
def validation_image(part_number: str, entry_id: str) -> Response:
    return Response(content=samples.validation_image(part_number, entry_id), media_type="image/jpeg")


@router.post("/validation/{part_number}/run", dependencies=protected)
def validation_run(part_number: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    return samples.run_validation(session, part_number)


# ============================================================
# CALIBRACION DE CAMARA
# ============================================================

def _camera_frame() -> np.ndarray:
    try:
        return camera_service.capture_bgr()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/calibration", dependencies=protected)
def calibration_status() -> dict[str, Any]:
    data = calibration_service.load()
    if data:
        data = {k: v for k, v in data.items() if k not in ("camera_matrix", "per_view_error")}
    return {"calibration": data, "session": calibration_service.session_info()}


@router.get("/calibration/board.png", dependencies=protected)
def calibration_board() -> Response:
    square = float(settings_store.get()["calibration"]["square_mm"])
    return Response(content=board_png(square), media_type="image/png")


@router.post("/calibration/frame", dependencies=protected)
def calibration_frame() -> dict[str, Any]:
    return calibration_service.add_frame(_camera_frame())


@router.post("/calibration/reset-session", dependencies=protected)
def calibration_reset() -> dict[str, Any]:
    calibration_service.reset_session()
    return calibration_service.session_info()


@router.post("/calibration/compute", dependencies=protected)
def calibration_compute() -> dict[str, Any]:
    try:
        result = calibration_service.compute()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    calibration_service.reset_session()
    return {k: v for k, v in result.items() if k not in ("camera_matrix", "per_view_error")}


@router.post("/calibration/plane-scale", dependencies=protected)
def calibration_plane_scale() -> dict[str, Any]:
    try:
        return calibration_service.measure_plane_scale(_camera_frame())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/calibration", dependencies=protected)
def calibration_delete() -> dict[str, bool]:
    calibration_service.delete()
    return {"ok": True}


# ============================================================
# ALMACENAMIENTO
# ============================================================

def _folder_stats(path) -> dict[str, Any]:
    exists = path.exists()
    files = size = 0
    if exists and path.is_dir():
        for item in path.rglob("*"):
            if item.is_file():
                files += 1
                size += item.stat().st_size
    free = None
    try:
        anchor = path if exists else path.parent
        free = shutil.disk_usage(anchor).free
    except OSError:
        pass
    return {"exists": exists, "files": files, "bytes": size, "free_bytes": free}


@router.get("/storage", dependencies=protected)
def storage_status(session: Session = Depends(get_session)) -> dict[str, Any]:
    legacy = session.exec(
        select(Inspection.id).where(Inspection.original_path.is_(None))  # type: ignore[union-attr]
    ).all()
    paths = []
    for key, path in config.PATHS.items():
        info = _folder_stats(path) if key not in ("OI_DB_PATH", "OI_SETTINGS_FILE") else {
            "exists": path.exists(),
            "bytes": path.stat().st_size if path.exists() else 0,
        }
        paths.append({"key": key, "path": str(path), **info})
    return {
        "env_file": str(config.BASE_DIR / ".env"),
        "paths": paths,
        "host": config.HOST,
        "port": config.PORT,
        "legacy_inspections": len(legacy),
        "backups": list_backups(),
    }


@router.post("/storage/backup", dependencies=protected)
def storage_backup() -> dict[str, Any]:
    return {"path": backup_database(), "backups": list_backups()}


@router.post("/storage/migrate-images", dependencies=protected)
def storage_migrate(session: Session = Depends(get_session)) -> dict[str, int]:
    """Mueve a disco las imagenes guardadas como BLOB en registros antiguos."""
    migrated = 0
    for inspection in session.exec(select(Inspection).where(Inspection.original_path.is_(None))).all():  # type: ignore[union-attr]
        if not inspection.original_image:
            continue
        inspection.original_path = storage.save_bytes(config.IMAGES_DIR, inspection.original_image, "original", ".jpg")
        if inspection.rectified_image:
            inspection.rectified_path = storage.save_bytes(config.IMAGES_DIR, inspection.rectified_image, "rectified", ".png")
        if inspection.overlay_image:
            inspection.overlay_path = storage.save_bytes(config.IMAGES_DIR, inspection.overlay_image, "overlay", ".png")
        inspection.original_image = b""
        inspection.rectified_image = b""
        inspection.overlay_image = b""
        session.add(inspection)
        migrated += 1
        if migrated % 25 == 0:
            session.commit()
    session.commit()
    return {"migrated": migrated}
