"""API del comparador optico general (`/api/compare`)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from . import service
from .engine import DEFAULT_PRESET, PRESETS

router = APIRouter(prefix="/compare", tags=["Comparador"])

_NO_CACHE = {"Cache-Control": "no-store"}


def _http(error: Exception) -> HTTPException:
    if isinstance(error, service.NotFound):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, service.CompareInconclusive):
        return HTTPException(status_code=422, detail=f"NO CONCLUYENTE: {error}")
    return HTTPException(status_code=400, detail=str(error))


_ERRORS = (service.CompareError, service.NotFound, service.CompareInconclusive)


@router.get("/options")
def options() -> dict[str, Any]:
    return {
        "default_sensitivity": DEFAULT_PRESET,
        "sensitivities": [{"id": key, **value} for key, value in PRESETS.items()],
    }


# --- Piezas patron -----------------------------------------------------------------------------

@router.get("/parts")
def list_parts() -> list[dict[str, Any]]:
    return service.list_parts()


@router.post("/parts")
async def create_part(
    name: str = Form(...),
    description: str = Form(""),
    crop: str | None = Form(None),
    readings: str | None = Form(None),
    photo: UploadFile = File(...),
) -> dict[str, Any]:
    try:
        return service.create_part(name, description, await photo.read(), crop, readings)
    except _ERRORS as error:
        raise _http(error) from error


@router.get("/parts/{part_id}/revisions")
def revisions(part_id: int) -> list[dict[str, Any]]:
    try:
        return service.part_revisions(service.resolve_part(part_id, None)["name"])
    except _ERRORS as error:
        raise _http(error) from error


@router.get("/parts/{part_id}/image")
def part_image(part_id: int, max_side: int | None = Query(None, ge=64, le=4096)) -> Response:
    try:
        return Response(service.part_image(part_id, max_side), media_type="image/jpeg", headers=_NO_CACHE)
    except _ERRORS as error:
        raise _http(error) from error


class PartPatch(BaseModel):
    name: str | None = None
    description: str | None = None


@router.patch("/parts/{part_id}")
def patch_part(part_id: int, body: PartPatch) -> dict[str, Any]:
    try:
        return service.rename_part(part_id, body.name, body.description)
    except _ERRORS as error:
        raise _http(error) from error


@router.put("/parts/{part_id}/readings")
def put_readings(part_id: int, zones: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        return service.set_part_readings(part_id, zones)
    except _ERRORS as error:
        raise _http(error) from error


@router.delete("/parts/{part_id}")
def delete_part(part_id: int) -> dict[str, str]:
    try:
        service.delete_part(part_id)
    except _ERRORS as error:
        raise _http(error) from error
    return {"message": "Pieza patron eliminada."}


# --- Inspeccion --------------------------------------------------------------------------------

@router.post("/inspect")
async def inspect(
    part_id: int | None = Form(None),
    part_name: str | None = Form(None),
    sensitivity: str = Form(DEFAULT_PRESET),
    crop: str | None = Form(None),
    photo: UploadFile = File(...),
) -> dict[str, Any]:
    try:
        data = await photo.read()
        return service.inspect(part_id, part_name, data, crop, sensitivity)
    except _ERRORS as error:
        raise _http(error) from error


@router.get("/inspections")
def list_inspections(part: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    return service.list_inspections(part, limit)


@router.get("/inspections/{inspection_id}")
def get_inspection(inspection_id: int) -> dict[str, Any]:
    try:
        return service.get_inspection(inspection_id)
    except _ERRORS as error:
        raise _http(error) from error


@router.get("/inspections/{inspection_id}/image/{kind}")
def inspection_image(inspection_id: int, kind: str) -> Response:
    try:
        return Response(service.inspection_image(inspection_id, kind), media_type="image/jpeg", headers=_NO_CACHE)
    except _ERRORS as error:
        raise _http(error) from error


@router.delete("/inspections/{inspection_id}")
def delete_inspection(inspection_id: int) -> dict[str, str]:
    try:
        service.delete_inspection(inspection_id)
    except _ERRORS as error:
        raise _http(error) from error
    return {"message": "Inspeccion eliminada."}
