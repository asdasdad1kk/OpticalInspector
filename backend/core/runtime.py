"""Log con rotacion y respaldo automatico de la base de datos."""
from __future__ import annotations

import faulthandler
import logging
import sqlite3
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler

from core import config
from core.settings import settings_store

log = logging.getLogger("optical")
_crash_file = None


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        config.LOG_DIR / "optical_inspector.log", maxBytes=2_000_000, backupCount=10, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Las caidas nativas (OpenCV/DirectShow, RealSense) no pasan por logging:
    # faulthandler deja la traza de todos los hilos en crash.log.
    global _crash_file
    if _crash_file is None:
        _crash_file = open(config.LOG_DIR / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)


def backup_database() -> str | None:
    """Copia consistente (API de backup de SQLite) y poda de las antiguas."""
    if not config.DB_PATH.exists():
        return None
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = config.BACKUP_DIR / f"optical_inspector_{datetime.now():%Y%m%d_%H%M%S}.db"
    source = sqlite3.connect(config.DB_PATH)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    keep = int(settings_store.get()["backup"]["keep"])
    backups = sorted(config.BACKUP_DIR.glob("optical_inspector_*.db"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)
    log.info("Respaldo creado: %s", target)
    return str(target)


def list_backups() -> list[dict]:
    items = []
    for path in sorted(config.BACKUP_DIR.glob("optical_inspector_*.db"), reverse=True):
        stat = path.stat()
        items.append({
            "name": path.name,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return items


_stop = threading.Event()


def start_backup_scheduler() -> None:
    def loop() -> None:
        while not _stop.is_set():
            cfg = settings_store.get()["backup"]
            if cfg["enabled"]:
                try:
                    latest = max(
                        config.BACKUP_DIR.glob("optical_inspector_*.db"),
                        default=None,
                        key=lambda p: p.stat().st_mtime,
                    )
                    age_h = (datetime.now().timestamp() - latest.stat().st_mtime) / 3600 if latest else 1e9
                    if age_h >= float(cfg["interval_hours"]):
                        backup_database()
                except Exception:
                    log.exception("Fallo el respaldo automatico")
            _stop.wait(600)

    threading.Thread(target=loop, name="db-backup", daemon=True).start()


def stop_backup_scheduler() -> None:
    _stop.set()
