"""Piezas buenas adicionales (por numero de parte) y sets de validacion.

Piezas buenas: cada captura adicional se registra contra la pieza patron
original; con ellas se promedia la posicion de cada feature y se mide su
dispersion real. Con 3 o mas piezas las tolerancias nunca bajan de 3-5
sigmas observadas (ver pipeline.evaluate_feature y golden.check_contour).

Sets de validacion: imagenes etiquetadas OK / FALLO por numero de parte para
comprobar, tras cualquier cambio de ajustes, que el sistema decide bien.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime
from typing import Any

import numpy as np
from fastapi import HTTPException
from sqlmodel import Session, select

from core import config, storage
from models import Drawing, Part
from modules.inspect.golden import DEFAULT_ENABLED
from modules.inspect.pipeline import InconclusiveError, run_pipeline
from modules.inspect.service import InspectService, prepare_image


# ============================================================
# PIEZAS BUENAS
# ============================================================

def _golden_drawing(session: Session, drawing_id: int) -> Drawing:
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing no encontrado.")
    if (drawing.rois or {}).get("source") != "golden":
        raise HTTPException(
            status_code=400,
            detail="Solo las piezas dadas de alta con pieza real aceptan piezas buenas adicionales.",
        )
    return drawing


def _recompute(rois: dict[str, Any]) -> None:
    """Promedio y dispersion por feature a partir de la base + muestras."""
    golden = rois["golden"]
    samples = golden.get("samples", [])
    for item in rois["items"]:
        points = [[item["x0"], item["y0"]]] + [
            sample["positions"][str(item["id"])]
            for sample in samples
            if str(item["id"]) in sample["positions"]
        ]
        array = np.asarray(points, dtype=float)
        item["x"], item["y"] = (round(float(v), 4) for v in array.mean(axis=0))
        item["samples"] = len(points)
        if len(points) >= 3:
            variance = array.var(axis=0, ddof=1)
            item["pos_std"] = round(float(np.sqrt(variance.mean())), 4)
        else:
            item.pop("pos_std", None)

    base = golden["base_dimensions"]
    dims = [[base[0], base[1]]] + [sample["dims"] for sample in samples if sample.get("dims")]
    golden["dim_samples"] = dims
    golden["sample_count"] = 1 + len(samples)


def add_good_sample(session: Session, drawing_id: int, photo_bytes: bytes, options: dict[str, Any] | None) -> dict[str, Any]:
    drawing = _golden_drawing(session, drawing_id)
    rois = copy.deepcopy(drawing.rois)
    golden = rois["golden"]

    for item in rois["items"]:
        item.setdefault("x0", item["x"])
        item.setdefault("y0", item["y"])
    golden.setdefault("base_dimensions", [golden["dimensions"]["length"], golden["dimensions"]["width"]])
    golden.setdefault("samples", [])

    # Se registra contra la referencia ORIGINAL para no sesgar con el promedio.
    base_items = [
        {**item, "x": item["x0"], "y": item["y0"], "pos_std": 0.0, "enabled": item.get("enabled", True)}
        for item in rois["items"]
    ]
    probe_golden = {k: v for k, v in golden.items() if k != "dim_samples"}
    probe_golden["enabled"] = {**DEFAULT_ENABLED, "distances": False}
    bounds = rois["bounds"]

    try:
        run = run_pipeline(
            prepare_image(photo_bytes),
            base_items,
            (bounds["xmin"], bounds["xmax"], bounds["ymin"], bounds["ymax"]),
            options,
            None,
            probe_golden,
        )
    except InconclusiveError as error:
        raise HTTPException(status_code=422, detail=f"NO CONCLUYENTE: {error}") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    bad = [r for r in run["results"] if r["status"] == "FALLO"]
    if bad:
        reasons = "; ".join(f"{r['id']}: {(r.get('reasons') or ['fallo'])[0]}" for r in bad[:4])
        raise HTTPException(
            status_code=409,
            detail=f"La pieza no se acepta como buena ({len(bad)} verificaciones fallan): {reasons}",
        )

    canvas = run["canvas"]
    c_xmin, _, _, c_ymax = canvas["bounds"]
    positions = {
        str(r["id"]): [
            round(c_xmin + r["center_x_subpx"] / canvas["scale"], 4),
            round(c_ymax - r["center_y_subpx"] / canvas["scale"], 4),
        ]
        for r in run["results"]
        if r.get("kind") is None and r.get("found")
    }
    contour = next((r for r in run["results"] if r.get("kind") == "contour"), None)
    dims = None
    if contour and contour.get("dimensions"):
        dims = [contour["dimensions"]["largo"]["measured"], contour["dimensions"]["ancho"]["measured"]]

    relative = storage.save_bytes(
        config.GOLDEN_DIR, photo_bytes, "good", ".jpg", subdir=f"drawing_{drawing_id}"
    )
    golden["samples"].append({
        "id": uuid.uuid4().hex[:8],
        "added": datetime.now().isoformat(timespec="seconds"),
        "path": relative,
        "positions": positions,
        "dims": dims,
        "score": run["score"],
    })
    _recompute(rois)
    try:
        drawing.rois = rois
        session.add(drawing)
        session.commit()
    except Exception:
        session.rollback()
        storage.delete(config.GOLDEN_DIR, relative)
        raise
    return sample_summary(drawing)


def delete_good_sample(session: Session, drawing_id: int, sample_id: str) -> dict[str, Any]:
    drawing = _golden_drawing(session, drawing_id)
    rois = copy.deepcopy(drawing.rois)
    golden = rois["golden"]
    kept = [s for s in golden.get("samples", []) if s["id"] != sample_id]
    if len(kept) == len(golden.get("samples", [])):
        raise HTTPException(status_code=404, detail="Muestra no encontrada.")
    removed = next(s for s in golden["samples"] if s["id"] == sample_id)
    golden["samples"] = kept
    _recompute(rois)
    drawing.rois = rois
    session.add(drawing)
    session.commit()
    storage.delete(config.GOLDEN_DIR, removed["path"])
    return sample_summary(drawing)


def sample_summary(drawing: Drawing) -> dict[str, Any]:
    rois = drawing.rois or {}
    golden = rois.get("golden", {})
    stds = [i["pos_std"] for i in rois.get("items", []) if i.get("pos_std")]
    return {
        "drawing_id": drawing.id,
        "sample_count": golden.get("sample_count", 1),
        "samples": [
            {"id": s["id"], "added": s["added"], "score": s.get("score")}
            for s in golden.get("samples", [])
        ],
        "max_position_std": max(stds) if stds else None,
        "statistics_active": golden.get("sample_count", 1) >= 3,
    }


# ============================================================
# SETS DE VALIDACION
# ============================================================

def _folder(part_number: str):
    safe = re.sub(r"[^\w.-]", "_", part_number.strip()) or "sin_numero"
    return config.VALIDATION_DIR / safe


def _index(part_number: str) -> list[dict[str, Any]]:
    try:
        return json.loads((_folder(part_number) / "index.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _write_index(part_number: str, entries: list[dict[str, Any]]) -> None:
    folder = _folder(part_number)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")


def list_validation(part_number: str) -> list[dict[str, Any]]:
    return _index(part_number)


def add_validation(part_number: str, expected: str, data: bytes, note: str = "") -> dict[str, Any]:
    if expected not in ("OK", "FALLO"):
        raise HTTPException(status_code=422, detail="expected debe ser OK o FALLO.")
    folder = _folder(part_number)
    file_name = f"{expected.lower()}_{uuid.uuid4().hex[:8]}.jpg"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / file_name).write_bytes(data)
    entry = {
        "id": uuid.uuid4().hex[:8],
        "file": file_name,
        "expected": expected,
        "note": note,
        "added": datetime.now().isoformat(timespec="seconds"),
    }
    entries = _index(part_number)
    entries.append(entry)
    _write_index(part_number, entries)
    return entry


def delete_validation(part_number: str, entry_id: str) -> None:
    entries = _index(part_number)
    target = next((e for e in entries if e["id"] == entry_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Imagen no encontrada.")
    (_folder(part_number) / target["file"]).unlink(missing_ok=True)
    _write_index(part_number, [e for e in entries if e["id"] != entry_id])


def validation_image(part_number: str, entry_id: str) -> bytes:
    target = next((e for e in _index(part_number) if e["id"] == entry_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Imagen no encontrada.")
    return (_folder(part_number) / target["file"]).read_bytes()


def run_validation(session: Session, part_number: str) -> dict[str, Any]:
    part = session.exec(select(Part).where(Part.part_number == part_number)).first()
    drawing = (
        session.exec(select(Drawing).where(Drawing.part_id == part.id).order_by(Drawing.id.desc())).first()
        if part else None
    )
    if drawing is None:
        raise HTTPException(status_code=404, detail="La pieza no esta dada de alta.")

    options = InspectService.resolve_options(session, drawing, None)
    rows = []
    for entry in _index(part_number):
        row = {"id": entry["id"], "expected": entry["expected"], "note": entry.get("note", "")}
        try:
            data = (_folder(part_number) / entry["file"]).read_bytes()
            processed = InspectService.process_inspection(drawing, data, options)
            row.update(status=processed["status"], score=processed["score"])
            if entry["expected"] == "OK":
                row["outcome"] = "CORRECTO" if processed["status"] == "OK" else "FALSO_RECHAZO"
            else:
                row["outcome"] = "CORRECTO" if processed["status"] != "OK" else "FALSO_OK"
        except InconclusiveError as error:
            row.update(status="NO_CONCLUYENTE", score=None, outcome="NO_CONCLUYENTE", detail=str(error))
        except Exception as error:
            row.update(status="ERROR", score=None, outcome="ERROR", detail=str(error))
        rows.append(row)

    count = lambda name: sum(1 for r in rows if r["outcome"] == name)  # noqa: E731
    return {
        "part_number": part_number,
        "total": len(rows),
        "correct": count("CORRECTO"),
        "false_ok": count("FALSO_OK"),
        "false_reject": count("FALSO_RECHAZO"),
        "inconclusive": count("NO_CONCLUYENTE"),
        "errors": count("ERROR"),
        "rows": rows,
        "passed": bool(rows) and count("CORRECTO") == len(rows),
    }
