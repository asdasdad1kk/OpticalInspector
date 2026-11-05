"""Rutas y parametros de arranque, leidos de un archivo .env.

Todas las rutas de archivos salen de aqui, de modo que se puedan mover a un
servidor de archivos dedicado (por ejemplo una ruta UNC) cambiando solo el
.env. Las rutas relativas se resuelven contra la carpeta del programa.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

if getattr(sys, "frozen", False):
    # Instalado: configuracion y datos por usuario, fuera de la carpeta del
    # programa (asi sobreviven a actualizaciones y no requieren permisos de admin).
    _local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    BASE_DIR = Path(_local) / "OpticalInspector"
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    _DEFAULT_DATA_DIR = BASE_DIR / "data"
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    _DEFAULT_DATA_DIR = BASE_DIR

load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")


def _path(name: str, default: Path) -> Path:
    raw = (os.getenv(name) or "").strip().strip('"')
    path = Path(raw).expanduser() if raw else default
    return path if path.is_absolute() or str(path).startswith("\\\\") else BASE_DIR / path


DATA_DIR = _path("OI_DATA_DIR", _DEFAULT_DATA_DIR)
DB_PATH = _path("OI_DB_PATH", DATA_DIR / "optical_inspector.db")
IMAGES_DIR = _path("OI_IMAGES_DIR", DATA_DIR / "storage" / "inspections")
GOLDEN_DIR = _path("OI_GOLDEN_DIR", DATA_DIR / "storage" / "golden")
SCANS_DIR = _path("OI_SCANS_DIR", DATA_DIR / "storage" / "scans")
SCANS3D_DIR = _path("OI_SCANS3D_DIR", DATA_DIR / "storage" / "scans3d")
VALIDATION_DIR = _path("OI_VALIDATION_DIR", DATA_DIR / "storage" / "validation")
CALIBRATION_DIR = _path("OI_CALIBRATION_DIR", DATA_DIR / "storage" / "calibration")
BACKUP_DIR = _path("OI_BACKUP_DIR", DATA_DIR / "backups")
LOG_DIR = _path("OI_LOG_DIR", DATA_DIR / "logs")
SETTINGS_FILE = _path("OI_SETTINGS_FILE", DATA_DIR / "settings.json")

HOST = (os.getenv("OI_HOST") or "127.0.0.1").strip()
PORT = int(os.getenv("OI_PORT") or 8000)
INITIAL_PASSWORD = os.getenv("OI_CONFIG_PASSWORD") or ""

DATABASE_URL = URL.create("sqlite", database=str(DB_PATH))

PATHS = {
    "OI_DATA_DIR": DATA_DIR,
    "OI_DB_PATH": DB_PATH,
    "OI_IMAGES_DIR": IMAGES_DIR,
    "OI_GOLDEN_DIR": GOLDEN_DIR,
    "OI_SCANS_DIR": SCANS_DIR,
    "OI_SCANS3D_DIR": SCANS3D_DIR,
    "OI_VALIDATION_DIR": VALIDATION_DIR,
    "OI_CALIBRATION_DIR": CALIBRATION_DIR,
    "OI_BACKUP_DIR": BACKUP_DIR,
    "OI_LOG_DIR": LOG_DIR,
    "OI_SETTINGS_FILE": SETTINGS_FILE,
}


def ensure_dirs() -> None:
    for directory in (
        DATA_DIR, DB_PATH.parent, IMAGES_DIR, GOLDEN_DIR, SCANS_DIR, SCANS3D_DIR, VALIDATION_DIR,
        CALIBRATION_DIR, BACKUP_DIR, LOG_DIR, SETTINGS_FILE.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
