"""Zonas de lectura: leer un codigo o un texto en un rectangulo y validarlo contra lo esperado.

Lo comparten el modo de paneles y el comparador. Una zona se define en coordenadas de la imagen de referencia
(el patron); como ambos modos ya alinean la foto a ese marco, la zona cae siempre en el mismo lugar.

    zona = {"id": "Z1", "name": "Numero de serie", "kind": "code" | "text",
            "x", "y", "w", "h",                      # pixeles de la imagen de referencia
            "rule": "equals" | "regex" | "present" | "record",
            "expected": "PN-1234",                   # para equals
            "pattern": "^PN-[0-9]{4}$"}              # para regex
"""
from __future__ import annotations

import re
import time
from typing import Any

import numpy as np

from . import codes, ocr

RULES = ("equals", "regex", "present", "record")
KINDS = ("code", "text")
STATUSES = ("REVISAR", "FALLO")

DEFAULTS: dict[str, Any] = {
    "codes_enabled": True,
    "text_enabled": True,
    "languages": "spa+eng",
    "psm": 7,
    "min_text_confidence": 60.0,
    "timeout_s": 8.0,
    "code_formats": "comunes",
    "unreadable_status": "REVISAR",   # no se pudo leer
    "mismatch_status": "FALLO",       # se leyo pero no es lo esperado
}


def settings() -> dict[str, Any]:
    try:
        from core.settings import settings_store  # noqa: PLC0415

        return {**DEFAULTS, **(settings_store.get().get("reading") or {})}
    except Exception:  # pragma: no cover - sin configuracion (pruebas aisladas)
        return dict(DEFAULTS)


def capabilities(refresh: bool = False) -> dict[str, Any]:
    config = settings()
    text = ocr.refresh() if refresh else ocr.locate()
    return {
        "codes": {**codes.engine_info(), "enabled": bool(config["codes_enabled"]), "formats": config["code_formats"],
                  "format_groups": {key: list(value) for key, value in codes.FORMAT_GROUPS.items()}},
        "text": {**text, "enabled": bool(config["text_enabled"]), "languages_wanted": config["languages"]},
        "settings": config,
    }


def normalize_zone(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Valida y limpia una zona que llega del frontend."""
    kind = str(raw.get("kind", "code"))
    rule = str(raw.get("rule", "record"))
    if kind not in KINDS:
        raise ValueError(f"Tipo de zona desconocido: {kind}. Use {KINDS}.")
    if rule not in RULES:
        raise ValueError(f"Regla desconocida: {rule}. Use {RULES}.")
    try:
        x, y, w, h = (float(raw[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("La zona de lectura necesita x, y, w, h numericos.") from error
    if w < 8 or h < 8:
        raise ValueError("La zona de lectura es demasiado chica (minimo 8 x 8 px).")
    pattern = str(raw.get("pattern") or "")
    if rule == "regex":
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f"Expresion regular invalida: {error}") from error
    return {
        "id": str(raw.get("id") or f"L{index + 1}"),
        "name": str(raw.get("name") or f"Lectura {index + 1}")[:60],
        "kind": kind, "rule": rule,
        "x": round(x, 2), "y": round(y, 2), "w": round(w, 2), "h": round(h, 2),
        "expected": str(raw.get("expected") or "")[:500],
        "pattern": pattern[:300],
    }


def normalize_zones(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("Las zonas de lectura deben ser una lista.")
    if len(raw) > 30:
        raise ValueError("Maximo 30 zonas de lectura.")
    return [normalize_zone(item, index) for index, item in enumerate(raw)]


def _crop(image: np.ndarray, zone: dict[str, Any], scale: float, margin: float) -> np.ndarray | None:
    height, width = image.shape[:2]
    pad_x, pad_y = margin * zone["w"], margin * zone["h"]
    x0 = int(max(0, (zone["x"] - pad_x) * scale))
    y0 = int(max(0, (zone["y"] - pad_y) * scale))
    x1 = int(min(width, (zone["x"] + zone["w"] + pad_x) * scale))
    y1 = int(min(height, (zone["y"] + zone["h"] + pad_y) * scale))
    if x1 - x0 < 6 or y1 - y0 < 6:
        return None
    return image[y0:y1, x0:x1]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _matches(zone: dict[str, Any], value: str) -> bool:
    if zone["rule"] == "equals":
        expected = _clean(zone["expected"])
        # El texto (OCR) se compara sin distinguir mayusculas ni espacios; los codigos, exactos.
        if zone["kind"] == "text":
            return value.replace(" ", "").upper() == expected.replace(" ", "").upper()
        return value == expected
    if zone["rule"] == "regex":
        return re.fullmatch(zone["pattern"], value) is not None
    return True


def read_zone(image: np.ndarray, zone: dict[str, Any], scale: float = 1.0, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lee y valida una zona. `scale` convierte las coordenadas de la zona a pixeles de `image`."""
    config = config or settings()
    started = time.perf_counter()
    result: dict[str, Any] = {
        "id": zone["id"], "name": zone["name"], "kind": zone["kind"], "rule": zone["rule"],
        "expected": zone["expected"] if zone["rule"] == "equals" else zone["pattern"] if zone["rule"] == "regex" else "",
        "value": "", "format": None, "confidence": None, "status": "OK", "reason": "",
    }

    def done(status: str, reason: str) -> dict[str, Any]:
        result.update(status=status, reason=reason, ms=round(1000 * (time.perf_counter() - started)))
        return result

    enabled = config["codes_enabled"] if zone["kind"] == "code" else config["text_enabled"]
    if not enabled:
        return done("OMITIDO", "Lectura desactivada en la configuracion.")
    crop = _crop(image, zone, scale, 0.15 if zone["kind"] == "code" else 0.04)
    if crop is None:
        return done(config["unreadable_status"], "La zona queda fuera de la imagen.")

    if zone["kind"] == "code":
        found = codes.read_codes(crop, config["code_formats"])
        if found:
            # Con varios codigos en la zona se prefiere el que cumple la regla.
            chosen = next((item for item in found if _matches(zone, item["text"])), found[0])
            result.update(value=chosen["text"], format=chosen["format"], others=[f["text"] for f in found if f is not chosen])
    else:
        read = ocr.read_text(crop, config["languages"], int(config["psm"]), float(config["timeout_s"]))
        if not read["available"]:
            return done("OMITIDO" if zone["rule"] == "record" else config["unreadable_status"],
                        read.get("error") or "OCR no disponible.")
        result.update(value=read["text"], confidence=read["confidence"])
        if read["text"] and read["confidence"] < float(config["min_text_confidence"]):
            return done(config["unreadable_status"],
                        f"Texto poco legible (confianza {read['confidence']:.0f} < {config['min_text_confidence']:.0f}).")

    value = _clean(result["value"])
    result["value"] = value
    if not value:
        if zone["rule"] == "record":
            return done("OK", "Nada que leer en la zona (solo se registra).")
        what = "codigo" if zone["kind"] == "code" else "texto"
        return done(config["unreadable_status"], f"No se pudo leer ningun {what} en la zona.")
    if zone["rule"] in ("present", "record"):
        return done("OK", "Leido.")
    if _matches(zone, value):
        return done("OK", "Coincide con lo esperado.")
    wanted = f"«{zone['expected']}»" if zone["rule"] == "equals" else f"el patron {zone['pattern']}"
    return done(config["mismatch_status"], f"Se leyo «{value}» y se esperaba {wanted}.")


def read_zones(image: np.ndarray, zones: list[dict[str, Any]], scale: float = 1.0) -> list[dict[str, Any]]:
    config = settings()
    return [read_zone(image, zone, scale, config) for zone in zones]


def worst_status(results: list[dict[str, Any]]) -> str:
    order = {"OK": 0, "OMITIDO": 0, "REVISAR": 1, "FALLO": 2}
    worst = max((order.get(r["status"], 0) for r in results), default=0)
    return {0: "OK", 1: "REVISAR", 2: "FALLO"}[worst]


def read_anything(image: np.ndarray, text: bool = False) -> dict[str, Any]:
    """Prueba libre: todos los codigos de la imagen y, si se pide, su texto."""
    config = settings()
    out: dict[str, Any] = {"codes": codes.read_codes(image, config["code_formats"]) if config["codes_enabled"] else []}
    if text:
        out["text"] = ocr.read_text(image, config["languages"], 11, float(config["timeout_s"]) * 2)
    return out
