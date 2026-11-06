"""Casos de uso del comparador: dar de alta piezas patron e inspeccionar contra ellas."""
from __future__ import annotations

import json
from typing import Any

import cv2
import numpy as np

from core import config, storage

from . import store
from .engine import PRESETS, CompareInconclusive, compare, image_quality

MAX_SIDE = 4096          # fotos mas grandes se reducen al guardar
GOLDEN_JPEG_QUALITY = 96
REPORT_JPEG_QUALITY = 90


class CompareError(ValueError):
    """Peticion invalida (nombre vacio, imagen ilegible, recorte fuera de la foto...)."""


class NotFound(LookupError):
    pass


def decode(data: bytes, what: str = "la foto") -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    if image is None:
        raise CompareError(f"No se pudo leer {what}: use JPG o PNG.")
    return image


def parse_crop(raw: str | dict | None) -> dict[str, int] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return {key: int(round(float(data[key]))) for key in ("x", "y", "w", "h")}
    except (ValueError, KeyError, TypeError) as error:
        raise CompareError("El recorte no es valido (se espera x, y, w, h).") from error


def apply_crop(image: np.ndarray, crop: dict[str, int] | None) -> tuple[np.ndarray, dict[str, int] | None]:
    """Recorta en pixeles de la foto original. Sin recorte devuelve la foto entera."""
    height, width = image.shape[:2]
    if image.shape[1] > MAX_SIDE or image.shape[0] > MAX_SIDE:
        raise CompareError(f"La foto supera {MAX_SIDE} px por lado.")
    if crop is None:
        return image, None
    x0, y0 = max(0, crop["x"]), max(0, crop["y"])
    x1, y1 = min(width, crop["x"] + crop["w"]), min(height, crop["y"] + crop["h"])
    if x1 - x0 < 80 or y1 - y0 < 80:
        raise CompareError("El recorte es demasiado chico (minimo 80 x 80 px). Seleccione un area mayor.")
    return image[y0:y1, x0:x1].copy(), {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _encode(image: np.ndarray, quality: int) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise CompareError("No fue posible codificar la imagen.")
    return encoded.tobytes()


# ---------------------------------------------------------------------------
# Piezas patron
# ---------------------------------------------------------------------------

def create_part(name: str, description: str, photo: bytes, crop_raw: str | None) -> dict[str, Any]:
    name = " ".join((name or "").split())
    if not name:
        raise CompareError("Escriba el nombre de la pieza.")
    if len(name) > 80:
        raise CompareError("El nombre es demasiado largo (maximo 80 caracteres).")
    image, crop = apply_crop(decode(photo), parse_crop(crop_raw))
    quality = image_quality(image)
    if quality["sharpness"] < 12.0 or quality["contrast"] < 6.0:
        raise CompareError(
            f"La foto del patron esta desenfocada o sin contraste (nitidez {quality['sharpness']:.0f}). "
            "Tome una foto nitida: el patron es la referencia de todas las inspecciones."
        )
    path = storage.save_bytes(config.COMPARE_DIR, _encode(image, GOLDEN_JPEG_QUALITY), "golden", ".jpg", subdir="golden")
    part = store.add_part(name, (description or "").strip(), path, image.shape[1], image.shape[0], crop, quality)
    return _public_part(part)


def _public_part(part: dict[str, Any]) -> dict[str, Any]:
    return {key: part[key] for key in ("id", "name", "revision", "description", "width", "height", "created_at") if key in part} | {
        key: part[key] for key in ("revisions", "inspections", "last_status") if key in part
    }


def list_parts() -> list[dict[str, Any]]:
    return [_public_part(part) for part in store.list_parts()]


def part_revisions(name: str) -> list[dict[str, Any]]:
    return [_public_part(part) for part in store.list_revisions(name)]


def resolve_part(part_id: int | None, name: str | None) -> dict[str, Any]:
    part = store.get_part(part_id) if part_id else store.active_part_by_name(name or "")
    if part is None:
        raise NotFound("La pieza patron no existe. Seleccione una de la lista o dela de alta primero.")
    return part


def part_image(part_id: int, max_side: int | None = None) -> bytes:
    part = store.get_part(part_id)
    if part is None:
        raise NotFound("La pieza patron no existe.")
    data = storage.read_bytes(config.COMPARE_DIR, part["image_path"])
    if max_side:
        image = decode(data)
        longest = max(image.shape[:2])
        if longest > max_side:
            scale = max_side / longest
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return _encode(image, 82)
    return data


def delete_part(part_id: int) -> None:
    paths = store.delete_part(part_id)
    if not paths:
        raise NotFound("La pieza patron no existe.")
    for path in paths:
        storage.delete(config.COMPARE_DIR, path)


def rename_part(part_id: int, name: str | None, description: str | None) -> dict[str, Any]:
    name = " ".join((name or "").split()) or None
    part = store.update_part(part_id, name, description)
    if part is None:
        raise NotFound("La pieza patron no existe.")
    return _public_part(part)


# ---------------------------------------------------------------------------
# Inspeccion
# ---------------------------------------------------------------------------

def inspect(
    part_id: int | None, part_name: str | None, photo: bytes, crop_raw: str | None, sensitivity: str,
) -> dict[str, Any]:
    if sensitivity not in PRESETS:
        raise CompareError(f"Sensibilidad desconocida: {sensitivity}. Use {', '.join(PRESETS)}.")
    part = resolve_part(part_id, part_name)
    golden = decode(storage.read_bytes(config.COMPARE_DIR, part["image_path"]), "el patron")
    test, crop = apply_crop(decode(photo), parse_crop(crop_raw))

    result = compare(golden, test, sensitivity)  # CompareInconclusive sube al router (HTTP 422)
    report = result["report"]
    report["crop"] = crop
    report["part"] = {"id": part["id"], "name": part["name"], "revision": part["revision"]}

    files: dict[str, str] = {"test": storage.save_bytes(config.COMPARE_DIR, _encode(test, REPORT_JPEG_QUALITY), "test", ".jpg", subdir=_folder())}
    images = result["images"]
    files["aligned"] = storage.save_bytes(config.COMPARE_DIR, _encode(result["test_aligned"], REPORT_JPEG_QUALITY), "aligned", ".jpg", subdir=_folder())
    files["golden"] = storage.save_bytes(config.COMPARE_DIR, _encode(result["golden_work"], REPORT_JPEG_QUALITY), "golden", ".jpg", subdir=_folder())
    for kind, image in images.items():
        files[kind] = storage.save_bytes(config.COMPARE_DIR, _encode(image, REPORT_JPEG_QUALITY), kind, ".jpg", subdir=_folder())

    record = store.add_inspection(part, report, files)
    return public_inspection(record)


def _folder() -> str:
    from datetime import datetime

    now = datetime.now()
    return f"inspections/{now:%Y}/{now:%m}"


def public_inspection(record: dict[str, Any]) -> dict[str, Any]:
    """Informe + URLs de imagen (relativas a /api/compare)."""
    base = f"/compare/inspections/{record['id']}/image"
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "part": {"id": record["part_id"], "name": record["part_name"], "revision": record["part_revision"]},
        **record["report"],
        "images": {kind: f"{base}/{kind}" for kind in record["files"]},
    }


def get_inspection(inspection_id: int) -> dict[str, Any]:
    record = store.get_inspection(inspection_id)
    if record is None:
        raise NotFound("La inspeccion no existe.")
    return public_inspection(record)


def inspection_image(inspection_id: int, kind: str) -> bytes:
    record = store.get_inspection(inspection_id)
    if record is None or kind not in record["files"]:
        raise NotFound("La imagen no existe.")
    return storage.read_bytes(config.COMPARE_DIR, record["files"][kind])


def list_inspections(part_name: str | None, limit: int) -> list[dict[str, Any]]:
    return store.list_inspections(part_name, limit)


def delete_inspection(inspection_id: int) -> None:
    paths = store.delete_inspection(inspection_id)
    if not paths:
        raise NotFound("La inspeccion no existe.")
    for path in paths:
        storage.delete(config.COMPARE_DIR, path)


__all__ = [
    "CompareError", "CompareInconclusive", "NotFound", "PRESETS", "create_part", "list_parts", "part_revisions",
    "part_image", "delete_part", "rename_part", "inspect", "get_inspection", "inspection_image", "list_inspections",
    "delete_inspection",
]
