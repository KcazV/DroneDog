"""Access to the Rust core (`dronedog_core`, contract C1) with a pure-Python fallback.

Tests (and anything else) can swap the core with ``set_core(fake)``; pass
``None`` to force the fallback, or call ``reset_core()`` to re-import.
"""
from __future__ import annotations

import os
import threading

from dronedog.ingest.sniff import FALLBACK_VERSION, fallback_detect

_UNSET = object()
_core = _UNSET
_lock = threading.Lock()
_import_error: str | None = None


def get_core():
    """The dronedog_core module, or None if it is not built/importable."""
    global _core, _import_error
    if _core is _UNSET:
        with _lock:
            if _core is _UNSET:
                try:
                    import dronedog_core  # type: ignore
                    _core = dronedog_core
                except Exception as e:  # noqa: BLE001
                    _import_error = f"{type(e).__name__}: {e}"
                    _core = None
    return _core


def set_core(core) -> None:
    global _core
    _core = core


def reset_core() -> None:
    global _core
    _core = _UNSET


def core_available() -> bool:
    return get_core() is not None


def core_import_error() -> str | None:
    get_core()
    return _import_error


def core_version() -> str | None:
    c = get_core()
    if c is None:
        return None
    try:
        return str(c.version())
    except Exception:  # noqa: BLE001
        return "unknown"


def detector_component() -> tuple[str, str]:
    """(component, version) for custody entries of the detect step."""
    v = core_version()
    return ("detect.core", f"dronedog_core {v}") if v else ("detect.fallback", FALLBACK_VERSION)


def detect(path: str | os.PathLike) -> dict:
    """C1 detect(); falls back to header sniffing when the core is missing.
    A core exception also falls back, with the error noted in reasons."""
    c = get_core()
    if c is not None:
        try:
            d = dict(c.detect(str(path)))
            d.setdefault("reasons", [])
            return d
        except Exception as e:  # noqa: BLE001
            d = fallback_detect(path)
            d["reasons"].append(f"dronedog_core.detect raised {type(e).__name__}: {e}")
            return d
    return fallback_detect(path)
