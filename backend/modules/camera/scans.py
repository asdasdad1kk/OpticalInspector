"""Almacen temporal de escaneos 3D (color + profundidad) entre la captura y el alta de la pieza.

Un escaneo vive en disco bajo SCANS_DIR/<id>/ (color.jpg, depth.npz, meta.json) hasta que el
analisis y el guardado lo consumen; los viejos se purgan solos.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from typing import Any

import cv2
import numpy as np

from core import config
from modules.reference.relief import pack_depth, unpack_depth

_ID = re.compile(r"^[0-9a-f]{12}$")
MAX_AGE_SECONDS = 2 * 3600


class ScanNotFound(LookupError):
    pass


def _folder(scan_id: str):
    if not _ID.match(scan_id or ""):
        raise ScanNotFound("Escaneo inválido.")
    folder = config.SCANS_DIR / scan_id
    if not folder.is_dir():
        raise ScanNotFound("El escaneo ya no existe; vuelva a escanear la pieza.")
    return folder


def purge(max_age: float = MAX_AGE_SECONDS) -> None:
    if not config.SCANS_DIR.is_dir():
        return
    limit = time.time() - max_age
    for folder in config.SCANS_DIR.iterdir():
        try:
            if folder.is_dir() and folder.stat().st_mtime < limit:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def save_scan(
    color_bgr: np.ndarray, depth_mm: np.ndarray, intrinsics: dict[str, float], extra: dict[str, Any]
) -> str:
    purge()
    scan_id = uuid.uuid4().hex[:12]
    folder = config.SCANS_DIR / scan_id
    folder.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", color_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError("No fue posible codificar la imagen del escaneo.")
    (folder / "color.jpg").write_bytes(encoded.tobytes())
    (folder / "depth.npz").write_bytes(pack_depth(depth_mm))
    (folder / "meta.json").write_text(json.dumps({"intrinsics": intrinsics, **extra}), encoding="utf-8")
    return scan_id


def color_jpeg(scan_id: str) -> bytes:
    return (_folder(scan_id) / "color.jpg").read_bytes()


def load_scan(scan_id: str) -> dict[str, Any]:
    folder = _folder(scan_id)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    color = cv2.imdecode(np.frombuffer((folder / "color.jpg").read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    depth = unpack_depth((folder / "depth.npz").read_bytes())
    if color is None or color.shape[:2] != depth.shape:
        raise ScanNotFound("El escaneo está dañado; vuelva a escanear la pieza.")
    return {"color": color, "depth_mm": depth, "depth_raw": (folder / "depth.npz").read_bytes(), **meta}
