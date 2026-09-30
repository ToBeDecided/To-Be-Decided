"""Per-user settings (API keys) and platform-appropriate folders.

Keys can come from environment variables (which win) or from a small JSON file
the web app writes when you paste a key into Settings:

* macOS:   ~/Library/Application Support/internmatch/config.json
* Windows: %APPDATA%\\internmatch\\config.json
* Linux:   ~/.config/internmatch/config.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# setting name -> environment variable that overrides it
SETTINGS: dict[str, str] = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "usajobs_api_key": "USAJOBS_API_KEY",
    "usajobs_email": "USAJOBS_EMAIL",
    "adzuna_app_id": "ADZUNA_APP_ID",
    "adzuna_app_key": "ADZUNA_APP_KEY",
    "themuse_api_key": "THEMUSE_API_KEY",
}
SECRET_SETTINGS = {"anthropic_api_key", "usajobs_api_key", "adzuna_app_key", "themuse_api_key"}


def config_dir() -> Path:
    if os.environ.get("INTERNMATCH_CONFIG_DIR"):
        return Path(os.environ["INTERNMATCH_CONFIG_DIR"])
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "internmatch"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "internmatch"
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "internmatch"


def cache_dir() -> Path:
    if os.environ.get("INTERNMATCH_CACHE_DIR"):
        return Path(os.environ["INTERNMATCH_CACHE_DIR"])
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "internmatch"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "internmatch" / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache") / "internmatch"


def _config_file() -> Path:
    return config_dir() / "config.json"


def load() -> dict[str, str]:
    try:
        data = json.loads(_config_file().read_text())
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in data.items() if k in SETTINGS and v}


def get(name: str) -> str | None:
    """A setting's value: environment variable first, then the config file."""
    env = os.environ.get(SETTINGS[name], "").strip()
    return env or load().get(name) or None


def source_of(name: str) -> str | None:
    if os.environ.get(SETTINGS[name], "").strip():
        return "environment"
    return "settings" if load().get(name) else None


def save(updates: dict[str, str | None]) -> None:
    """Merge updates into the config file. An empty string or None removes a setting."""
    data = load()
    for key, value in updates.items():
        if key not in SETTINGS:
            raise KeyError(key)
        value = (value or "").strip()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # Owner-only permissions: this file holds API keys.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def describe() -> dict[str, dict[str, str | bool | None]]:
    """Which settings are configured and where from, without revealing secret values."""
    out: dict[str, dict[str, str | bool | None]] = {}
    for name in SETTINGS:
        value = get(name)
        shown = None
        if value and name not in SECRET_SETTINGS:
            shown = value
        elif value:
            shown = f"…{value[-4:]}" if len(value) > 8 else "set"
        out[name] = {"configured": bool(value), "source": source_of(name), "value": shown}
    return out
