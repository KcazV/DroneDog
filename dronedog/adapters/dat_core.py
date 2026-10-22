"""DJI aircraft / RC DAT logs via dronedog_core.parse_dat.

Encrypted DATs come back with ``encrypted_unreadable=True`` (attribution only, D7).
"""
from __future__ import annotations

from pathlib import Path

from dronedog import core_bridge
from dronedog.adapters.base import AdapterUnavailable, ParseContext, normalize_result


class DatCoreAdapter:
    name = "dat_core"
    formats = "DJI DAT (V1/V3 framing); encrypted DATs -> attribution only"
    needs_keychain = None

    def accepts(self, detection: dict) -> float:
        if detection.get("kind") == "dji_dat":
            return 0.9
        return 0.0

    def parse(self, path: Path, ctx: ParseContext) -> dict:
        core = core_bridge.get_core()
        if core is None:
            raise AdapterUnavailable(f"dronedog_core not importable ({core_bridge.core_import_error()})")
        return normalize_result(core.parse_dat(str(path)))
