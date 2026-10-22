"""Reference Python DAT decoder adapter.

Prefers ``dronedog.adapters.dat_ref_impl.parse_dat`` (owned by the DAT agent) when it
is importable; otherwise falls back to ``dronedog_core.parse_dat``. Accepts plaintext
DAT subtypes only (dat_v3 / dat_legacy / dat_v1); encrypted DATs stay with dat_core
(attribution only).
"""
from __future__ import annotations

import importlib
from pathlib import Path

from dronedog import core_bridge
from dronedog.adapters.base import AdapterUnavailable, ParseContext, normalize_result

PLAIN_SUBTYPES = {"dat_v3", "dat_legacy", "dat_v1"}


def _impl():
    try:
        return importlib.import_module("dronedog.adapters.dat_ref_impl")
    except Exception:  # noqa: BLE001 - not written yet / broken: fall back to the core
        return None


class DatRefPyAdapter:
    name = "dat_ref_py"
    formats = "DJI DAT V3 / legacy (plaintext), Python reference decoder"
    needs_keychain = None

    def accepts(self, detection: dict) -> float:
        if detection.get("kind") == "dji_dat" and detection.get("subtype") in PLAIN_SUBTYPES:
            return 0.95 if _impl() is not None else 0.0
        return 0.0

    def parse(self, path: Path, ctx: ParseContext) -> dict:
        impl = _impl()
        if impl is not None and hasattr(impl, "parse_dat"):
            return normalize_result(impl.parse_dat(str(path)))
        core = core_bridge.get_core()
        if core is None:
            raise AdapterUnavailable("neither dat_ref_impl nor dronedog_core is available")
        return normalize_result(core.parse_dat(str(path)))
