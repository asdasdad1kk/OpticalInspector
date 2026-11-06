"""Configuracion editable desde el panel (protegido con clave).

Se guarda en un JSON (ruta OI_SETTINGS_FILE). La clave se almacena con
PBKDF2; el token de sesion vive solo en memoria.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any

from fastapi import Header, HTTPException

from core import config

# clave -> (tipo, minimo, maximo)
INSPECTION_SCHEMA: dict[str, tuple[type, float, float]] = {
    "position_ok_radii": (float, 0.05, 3.0),
    "position_review_radii": (float, 0.1, 5.0),
    "diameter_review_ratio": (float, 0.05, 1.0),
    "brass_saturation": (float, 0, 255),
    "min_presence": (float, 0.0, 1.0),
    "dimension_tolerance_pct": (float, 0.05, 20.0),
    "distance_tolerance_pct": (float, 0.05, 20.0),
    "min_inlier_fraction": (float, 0.2, 1.0),
    "min_sharpness": (float, 0, 5000),
    "min_brightness": (float, 0, 255),
    "max_brightness": (float, 0, 255),
    "max_clipped_fraction": (float, 0.0, 1.0),
    "check_position": (bool, 0, 1),
    "check_type": (bool, 0, 1),
    "check_diameter": (bool, 0, 1),
    "check_edges": (bool, 0, 1),
}

SECTION_SCHEMAS: dict[str, dict[str, tuple[type, float, float]]] = {
    "inspection": INSPECTION_SCHEMA,
    "camera": {
        "lock_enabled": (bool, 0, 1),
        "exposure": (float, -13, 0),
        "wb_temperature": (float, 2000, 10000),
        "autofocus": (bool, 0, 1),
        "focus": (float, 0, 255),
    },
    "calibration": {
        "apply": (bool, 0, 1),
        "square_mm": (float, 2, 200),
    },
    "backup": {
        "enabled": (bool, 0, 1),
        "keep": (int, 1, 365),
        "interval_hours": (float, 1, 720),
    },
    "reading": {
        "codes_enabled": (bool, 0, 1),
        "text_enabled": (bool, 0, 1),
        "psm": (int, 3, 13),
        "min_text_confidence": (float, 0, 100),
        "timeout_s": (float, 1, 60),
    },
}

# Campos de texto con valores permitidos (seccion -> campo -> opciones).
CHOICE_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "reading": {
        "languages": ("spa+eng", "eng", "spa"),
        "code_formats": ("comunes", "2d", "lineales", "todos"),
        "unreadable_status": ("REVISAR", "FALLO"),
        "mismatch_status": ("REVISAR", "FALLO"),
    },
}

DEFAULTS: dict[str, Any] = {
    "inspection": {
        "position_ok_radii": 0.45,
        "position_review_radii": 0.90,
        "diameter_review_ratio": 0.35,
        "brass_saturation": 60,
        "min_presence": 0.30,
        "dimension_tolerance_pct": 1.5,
        "distance_tolerance_pct": 1.5,
        "min_inlier_fraction": 0.6,
        "min_sharpness": 25,
        "min_brightness": 40,
        "max_brightness": 215,
        "max_clipped_fraction": 0.15,
        "check_position": True,
        "check_type": True,
        "check_diameter": True,
        "check_edges": True,
    },
    "camera": {
        "lock_enabled": False,
        "exposure": -6,
        "wb_temperature": 4600,
        "autofocus": False,
        "focus": 0,
    },
    "calibration": {"apply": True, "square_mm": 30.0},
    "dxf": {"default_unit": "mm"},
    "backup": {"enabled": True, "keep": 14, "interval_hours": 24},
    # Lectura de codigos y texto (OCR). El OCR solo se ejecuta si una pieza tiene zonas de texto.
    "reading": {
        "codes_enabled": True,
        "text_enabled": True,
        "languages": "spa+eng",
        "psm": 7,
        "min_text_confidence": 60.0,
        "timeout_s": 8.0,
        "code_formats": "comunes",
        "unreadable_status": "REVISAR",
        "mismatch_status": "FALLO",
    },
    "parts": {},
}

DXF_UNITS = ("mm", "cm", "m", "in", "ft")
DEFAULT_PASSWORD = "admin"
TOKEN_TTL_SECONDS = 8 * 3600


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000).hex()


def _coerce(value: Any, kind: type, low: float, high: float, name: str) -> Any:
    if kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{name} debe ser verdadero/falso.")
        return value
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} debe ser numerico.") from error
    if not (low <= number <= high):
        raise ValueError(f"{name} debe estar entre {low} y {high}.")
    return int(number) if kind is int else number


def _validate_section(values: dict[str, Any], schema: dict[str, tuple[type, float, float]]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if key not in schema:
            raise ValueError(f"Parametro desconocido: {key}.")
        clean[key] = _coerce(value, *schema[key], name=key)
    return clean


class SettingsStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] | None = None
        self._tokens: dict[str, float] = {}

    # ---------- persistencia ----------
    def _load(self) -> dict[str, Any]:
        with self._lock:
            if self._data is None:
                stored: dict[str, Any] = {}
                try:
                    stored = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
                merged = copy.deepcopy(DEFAULTS)
                for section, values in stored.items():
                    if isinstance(values, dict) and isinstance(merged.get(section), dict):
                        merged[section].update(values)
                    else:
                        merged[section] = values
                self._data = merged
                if "password_hash" not in self._data:
                    self._set_password_locked(config.INITIAL_PASSWORD or DEFAULT_PASSWORD, default=not config.INITIAL_PASSWORD)
            return self._data

    def _save(self) -> None:
        config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = config.SETTINGS_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, config.SETTINGS_FILE)

    def _set_password_locked(self, password: str, default: bool = False) -> None:
        salt = secrets.token_bytes(16)
        self._data["password_salt"] = salt.hex()
        self._data["password_hash"] = _hash(password, salt)
        self._data["password_is_default"] = default
        self._save()

    # ---------- lectura ----------
    def get(self) -> dict[str, Any]:
        """Configuracion sin secretos (copia)."""
        with self._lock:
            data = copy.deepcopy(self._load())
        for secret in ("password_hash", "password_salt"):
            data.pop(secret, None)
        return data

    def inspection_options(self, part_number: str | None = None) -> dict[str, Any]:
        data = self.get()
        options = dict(data["inspection"])
        if part_number:
            options.update(data["parts"].get(part_number, {}))
        return options

    # ---------- escritura ----------
    def update_section(self, section: str, values: dict[str, Any]) -> None:
        if section == "dxf":
            unit = values.get("default_unit")
            if unit not in DXF_UNITS:
                raise ValueError(f"default_unit debe ser uno de {DXF_UNITS}.")
            clean: dict[str, Any] = {"default_unit": unit}
        elif section in SECTION_SCHEMAS:
            choices = CHOICE_FIELDS.get(section, {})
            clean = _validate_section({k: v for k, v in values.items() if k not in choices}, SECTION_SCHEMAS[section])
            for key, allowed in choices.items():
                if key in values:
                    if values[key] not in allowed:
                        raise ValueError(f"{key} debe ser uno de {allowed}.")
                    clean[key] = values[key]
        else:
            raise ValueError(f"Seccion desconocida: {section}.")
        if section == "inspection":
            merged = {**self.get()["inspection"], **clean}
            if merged["position_review_radii"] < merged["position_ok_radii"]:
                raise ValueError("position_review_radii debe ser >= position_ok_radii.")
            if merged["max_brightness"] <= merged["min_brightness"]:
                raise ValueError("max_brightness debe ser mayor que min_brightness.")
        with self._lock:
            self._load()[section].update(clean)
            self._save()

    def reset(self, general: bool = True, parts: bool = True) -> None:
        """Vuelve a los valores de fabrica. La clave de configuracion NO se toca."""
        with self._lock:
            data = self._load()
            if general:
                for section in ("inspection", "camera", "calibration", "dxf", "backup"):
                    data[section] = copy.deepcopy(DEFAULTS[section])
            if parts:
                data["parts"] = {}
            self._save()

    def set_part_overrides(self, part_number: str, values: dict[str, Any] | None) -> None:
        part_number = part_number.strip()
        if not part_number:
            raise ValueError("Numero de parte requerido.")
        with self._lock:
            parts = self._load()["parts"]
            if not values:
                parts.pop(part_number, None)
            else:
                parts[part_number] = _validate_section(values, INSPECTION_SCHEMA)
            self._save()

    # ---------- autenticacion ----------
    def verify_password(self, password: str) -> bool:
        with self._lock:
            data = self._load()
            expected = data["password_hash"]
            salt = bytes.fromhex(data["password_salt"])
        return hmac.compare_digest(_hash(password, salt), expected)

    def is_default_password(self) -> bool:
        with self._lock:
            return bool(self._load().get("password_is_default"))

    def change_password(self, new_password: str) -> None:
        if len(new_password) < 4:
            raise ValueError("La clave debe tener al menos 4 caracteres.")
        with self._lock:
            self._load()
            self._set_password_locked(new_password)
            self._tokens.clear()

    def login(self, password: str) -> str | None:
        if not self.verify_password(password):
            time.sleep(0.8)  # frena fuerza bruta local
            return None
        token = secrets.token_urlsafe(24)
        with self._lock:
            now = time.time()
            self._tokens = {t: e for t, e in self._tokens.items() if e > now}
            self._tokens[token] = now + TOKEN_TTL_SECONDS
        return token

    def logout(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def valid_token(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expiry = self._tokens.get(token)
            return bool(expiry and expiry > time.time())


settings_store = SettingsStore()


def require_config(x_config_token: str | None = Header(default=None)) -> str:
    """Dependencia de FastAPI: exige sesion del panel de configuracion."""
    if not settings_store.valid_token(x_config_token):
        raise HTTPException(status_code=401, detail="Se requiere la clave de configuracion.")
    return x_config_token or ""
