"""
Lokale Shell-Einstellungen: Vault-Pfad + Rolle in
%LOCALAPPDATA%\\RAG-OS\\app-settings.json (pro Rechner, NICHT im Vault).

Wird von der Desktop-Shell (desktop.py) VOR dem Import von config/main gelesen,
um RAG_VAULT_PATH / RAG_SERVICE_ROLE als Env zu setzen. Bewusst OHNE config-Import
(der App-Data-Pfad wird hier eigenständig bestimmt), sonst Henne-Ei.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _appdata_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    return (Path(base) / "RAG-OS") if base else (Path.home() / ".rag-os")


SETTINGS_PATH = _appdata_dir() / "app-settings.json"


def load() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — fehlend/kaputt → leere Defaults
        return {}


def save(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SETTINGS_PATH)   # atomarer Swap


def get_vault_path() -> str | None:
    v = load().get("vault_path")
    return str(v) if v else None


def set_vault_path(path: str) -> None:
    d = load()
    d["vault_path"] = str(path)
    save(d)


def get_role(default: str = "writer") -> str:
    r = load().get("role")
    return r if r in ("writer", "reader") else default


def set_role(role: str) -> None:
    if role not in ("writer", "reader"):
        raise ValueError(f"ungültige Rolle: {role!r}")
    d = load()
    d["role"] = role
    save(d)
