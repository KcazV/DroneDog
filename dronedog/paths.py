"""Where DroneDog keeps its own files (never evidence).

Portable mode (the packaged DroneDog.exe, when the folder next to it is writable):
everything travels with the app folder, e.g. on a USB stick:

    <app dir>/DroneDogData/   recent cases, settings, map packs, tile cache, logs
    <app dir>/Cases/          default location for new cases

Installed / development mode (running from source, or the app folder is read-only):

    %LOCALAPPDATA%/DroneDog/          app data
    <Documents>/DroneDog Cases/       default location for new cases

Overrides: DRONEDOG_DATA and DRONEDOG_CASES environment variables.
"""
from __future__ import annotations

import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path


def app_dir() -> Path:
    """Folder holding DroneDog.exe when frozen, else the repository root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=d, prefix=".probe_", delete=True):
            pass
        return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def is_portable() -> bool:
    return bool(getattr(sys, "frozen", False)) and _writable(app_dir() / "DroneDogData")


@lru_cache(maxsize=1)
def data_dir() -> Path:
    """App data root (recent cases, settings, map packs, caches). Created on demand."""
    env = os.environ.get("DRONEDOG_DATA")
    if env:
        d = Path(env)
    elif is_portable():
        d = app_dir() / "DroneDogData"
    else:
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        d = Path(root) / "DroneDog"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _documents() -> Path:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_PERSONAL = 5 (Documents), SHGFP_TYPE_CURRENT = 0; follows OneDrive redirection
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Documents"


@lru_cache(maxsize=1)
def default_cases_dir() -> Path:
    """Default parent folder for new cases (not created until a case is made)."""
    env = os.environ.get("DRONEDOG_CASES")
    if env:
        return Path(env)
    if is_portable():
        return app_dir() / "Cases"
    return _documents() / "DroneDog Cases"


def describe() -> dict:
    """For the UI's About/Settings panel and the custody log."""
    return {"portable": is_portable(), "app_dir": str(app_dir()), "data_dir": str(data_dir()),
            "default_cases_dir": str(default_cases_dir())}
