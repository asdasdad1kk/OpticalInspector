"""Base de datos del comparador (SQLite propia, separada de la de paneles).

Se usa `sqlite3` directamente en vez del motor SQLModel de `core.db`: asi estas tablas nunca se mezclan con
las de paneles ni se crean en su archivo, aunque ambos modulos vivan en el mismo proceso.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from core import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS part (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    crop_json TEXT,
    quality_json TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS part_name ON part (name, revision);
CREATE TABLE IF NOT EXISTS inspection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id INTEGER NOT NULL,
    part_name TEXT NOT NULL,
    part_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    score REAL,
    sensitivity TEXT NOT NULL,
    regions INTEGER NOT NULL DEFAULT 0,
    report_json TEXT NOT NULL,
    files_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inspection_part ON inspection (part_name, id);
"""

_lock = threading.Lock()
_ready = False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(config.COMPARE_DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init() -> None:
    """Crea las tablas si faltan (idempotente)."""
    global _ready
    with _lock:
        config.COMPARE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.COMPARE_DIR.mkdir(parents=True, exist_ok=True)
        connection = _connect()
        try:
            with connection:
                connection.executescript(_SCHEMA)
        finally:
            connection.close()
        _ready = True


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    if not _ready:
        init()
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Piezas patron
# ---------------------------------------------------------------------------

def add_part(
    name: str, description: str, image_path: str, width: int, height: int,
    crop: dict[str, int] | None, quality: dict[str, float],
) -> dict[str, Any]:
    """Guarda una pieza patron. Si ya existe el nombre, es una nueva revision (la anterior se conserva)."""
    with session() as db:
        last = db.execute("SELECT MAX(revision) FROM part WHERE name = ? COLLATE NOCASE", (name,)).fetchone()[0]
        revision = (last or 0) + 1
        db.execute("UPDATE part SET active = 0 WHERE name = ? COLLATE NOCASE", (name,))
        cursor = db.execute(
            "INSERT INTO part (name, revision, description, image_path, width, height, crop_json, quality_json, active, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (name, revision, description, image_path, width, height, json.dumps(crop), json.dumps(quality), _now()),
        )
        return get_part(cursor.lastrowid, db)  # type: ignore[arg-type,return-value]


def get_part(part_id: int, db: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    if db is None:
        with session() as connection:
            return get_part(part_id, connection)
    return _row(db.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone())


def active_part_by_name(name: str) -> dict[str, Any] | None:
    with session() as db:
        return _row(db.execute(
            "SELECT * FROM part WHERE name = ? COLLATE NOCASE AND active = 1 ORDER BY revision DESC LIMIT 1", (name,)
        ).fetchone())


def list_parts() -> list[dict[str, Any]]:
    """Una fila por nombre: la revision activa, con cuantas revisiones e inspecciones tiene."""
    with session() as db:
        rows = db.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM part q WHERE q.name = p.name COLLATE NOCASE) AS revisions,"
            " (SELECT COUNT(*) FROM inspection i WHERE i.part_name = p.name COLLATE NOCASE) AS inspections,"
            " (SELECT status FROM inspection i WHERE i.part_name = p.name COLLATE NOCASE ORDER BY id DESC LIMIT 1) AS last_status"
            " FROM part p WHERE p.active = 1 ORDER BY p.name COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]


def list_revisions(name: str) -> list[dict[str, Any]]:
    with session() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM part WHERE name = ? COLLATE NOCASE ORDER BY revision DESC", (name,)
        ).fetchall()]


def delete_part(part_id: int) -> list[str]:
    """Borra una revision. Devuelve las rutas de archivos que ya no se usan. Si era la activa, activa la anterior."""
    with session() as db:
        part = get_part(part_id, db)
        if part is None:
            return []
        db.execute("DELETE FROM part WHERE id = ?", (part_id,))
        if part["active"]:
            db.execute(
                "UPDATE part SET active = 1 WHERE id = (SELECT id FROM part WHERE name = ? COLLATE NOCASE"
                " ORDER BY revision DESC LIMIT 1)", (part["name"],),
            )
        return [part["image_path"]]


def update_part(part_id: int, name: str | None, description: str | None) -> dict[str, Any] | None:
    with session() as db:
        part = get_part(part_id, db)
        if part is None:
            return None
        if description is not None:
            db.execute("UPDATE part SET description = ? WHERE id = ?", (description, part_id))
        if name and name.lower() != part["name"].lower():
            db.execute("UPDATE part SET name = ? WHERE name = ? COLLATE NOCASE", (name, part["name"]))
            db.execute("UPDATE inspection SET part_name = ? WHERE part_name = ? COLLATE NOCASE", (name, part["name"]))
        return get_part(part_id, db)


# ---------------------------------------------------------------------------
# Inspecciones
# ---------------------------------------------------------------------------

def add_inspection(part: dict[str, Any], report: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    with session() as db:
        cursor = db.execute(
            "INSERT INTO inspection (part_id, part_name, part_revision, status, score, sensitivity, regions,"
            " report_json, files_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (part["id"], part["name"], part["revision"], report["status"], report["score"], report["sensitivity"],
             len(report["regions"]), json.dumps(report), json.dumps(files), _now()),
        )
        return get_inspection(cursor.lastrowid, db)  # type: ignore[arg-type,return-value]


def _inspection(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["report"] = json.loads(data.pop("report_json"))
    data["files"] = json.loads(data.pop("files_json"))
    return data


def get_inspection(inspection_id: int, db: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    if db is None:
        with session() as connection:
            return get_inspection(inspection_id, connection)
    return _inspection(db.execute("SELECT * FROM inspection WHERE id = ?", (inspection_id,)).fetchone())


def list_inspections(part_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Resumen (sin el informe completo) de las ultimas inspecciones."""
    with session() as db:
        query = (
            "SELECT id, part_id, part_name, part_revision, status, score, sensitivity, regions, created_at FROM inspection"
        )
        args: tuple[Any, ...] = ()
        if part_name:
            query += " WHERE part_name = ? COLLATE NOCASE"
            args = (part_name,)
        rows = db.execute(f"{query} ORDER BY id DESC LIMIT ?", (*args, max(1, min(limit, 500)))).fetchall()
        return [dict(row) for row in rows]


def delete_inspection(inspection_id: int) -> list[str]:
    with session() as db:
        inspection = get_inspection(inspection_id, db)
        if inspection is None:
            return []
        db.execute("DELETE FROM inspection WHERE id = ?", (inspection_id,))
        return list(inspection["files"].values())
