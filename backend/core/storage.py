"""Almacenamiento de archivos en disco (rutas definidas en el .env)."""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

from core import config


def _sniff_extension(data: bytes, default: str) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    return default


def save_bytes(root: Path, data: bytes, suffix: str, default_ext: str = ".png", subdir: str | None = None) -> str:
    """Guarda `data` bajo `root` y devuelve la ruta relativa a `root` (con /)."""
    now = datetime.now()
    folder = Path(subdir) if subdir else Path(f"{now:%Y}") / f"{now:%m}"
    name = f"{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{suffix}{_sniff_extension(data, default_ext)}"
    relative = folder / name
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, target)  # escritura atomica
    return relative.as_posix()


def read_bytes(root: Path, relative: str) -> bytes:
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise ValueError("Ruta fuera del almacenamiento.")
    return target.read_bytes()


def delete(root: Path, relative: str) -> None:
    try:
        target = (root / relative).resolve()
        if root.resolve() in target.parents:
            target.unlink(missing_ok=True)
    except OSError:
        pass


def inspection_image(inspection, field: str) -> bytes | None:
    """Imagen de una inspeccion: del disco si hay ruta; si no, del BLOB heredado."""
    path = getattr(inspection, f"{field}_path", None)
    if path:
        try:
            return read_bytes(config.IMAGES_DIR, path)
        except (OSError, ValueError):
            return None
    return getattr(inspection, f"{field}_image", None) or None
