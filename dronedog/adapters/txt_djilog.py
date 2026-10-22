"""DJI app TXT logs (DJI GO / GO 4 / Fly / Pilot), v1-v14, via dronedog_core.parse_txt.

v13+ record content needs DJI keychains (D5): without an imported keychain the core
returns the plaintext header in ``details`` and ``encrypted_unreadable=True``.
"""
from __future__ import annotations

from pathlib import Path

from dronedog import core_bridge
from dronedog.adapters.base import AdapterUnavailable, ParseContext, normalize_result


class TxtDjiLogAdapter:
    name = "txt_djilog"
    formats = "DJI app TXT v1-v14"
    needs_keychain = "v13+ (records only; header always readable)"

    def accepts(self, detection: dict) -> float:
        if detection.get("kind") == "dji_txt":
            return 0.9
        return 0.0

    def parse(self, path: Path, ctx: ParseContext) -> dict:
        core = core_bridge.get_core()
        if core is None:
            raise AdapterUnavailable(f"dronedog_core not importable ({core_bridge.core_import_error()})")
        return normalize_result(core.parse_txt(str(path), ctx.keychains_json))
