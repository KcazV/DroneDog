"""C4 ParserAdapter protocol + registry.

Registry picks the adapter with the highest ``accepts()`` score (> 0); ties go to
the first registered. Adapters never touch the evidence store: they get a path to
the read-only extracted working copy and return a C1 ParseResult dict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class AdapterUnavailable(RuntimeError):
    """The adapter's engine (e.g. dronedog_core) is not available; artifact stays pending."""


@dataclass
class ParseContext:
    artifact: dict                       # artifacts row
    detection: dict                      # C1 detection
    case_root: Path | None = None
    sources: list[dict] = field(default_factory=list)   # artifact_sources rows (provenance)
    keychains_json: str | None = None    # dormant slot (D5): only an imported, cached keychain
    cancel: object | None = None


@runtime_checkable
class ParserAdapter(Protocol):
    name: str

    def accepts(self, detection: dict) -> float: ...

    def parse(self, path: Path, ctx: ParseContext) -> dict: ...


REQUIRED_RESULT_KEYS = ("adapter", "adapter_version", "details", "frames", "events", "records_total",
                        "records_decoded", "records_failed", "warnings", "encrypted_unreadable")


def normalize_result(r: dict) -> dict:
    """Fill missing C1 keys with safe defaults and coerce containers (core returns plain dicts)."""
    r = dict(r)
    r.setdefault("details", {})
    r.setdefault("frames", {})
    r.setdefault("events", [])
    r.setdefault("warnings", [])
    for k in ("records_total", "records_decoded", "records_failed"):
        r.setdefault(k, None)
    r.setdefault("encrypted_unreadable", False)
    r["frames"] = {k: list(v) for k, v in dict(r["frames"]).items()}
    r["events"] = [dict(e) for e in r["events"]]
    r["details"] = dict(r["details"] or {})
    return r


def flight_source(adapter_name: str, detection: dict, sources: list[dict]) -> str:
    """C5 flights.source: app-txt | aircraft-dat | controller-dat."""
    if detection.get("kind") in ("dji_txt", "dji_app_tips") or adapter_name.startswith("txt") \
            or adapter_name == "app_tips_json":
        return "app-txt"   # app tips logs: phone-side app record (details.x.kind = "app_tips")
    hint = " ".join([str(detection.get("subtype") or ""), " ".join(detection.get("reasons") or [])]).lower()
    paths = " ".join(str(s.get("inner_path") or "") for s in sources).lower()
    if "controller" in hint or "remote" in hint or "/rc/" in paths or "controller" in paths:
        return "controller-dat"
    return "aircraft-dat"


class Registry:
    def __init__(self):
        self._adapters: list[ParserAdapter] = []

    def register(self, adapter: ParserAdapter) -> ParserAdapter:
        if any(a.name == adapter.name for a in self._adapters):
            raise ValueError(f"adapter {adapter.name!r} already registered")
        self._adapters.append(adapter)
        return adapter

    def unregister(self, name: str) -> None:
        self._adapters = [a for a in self._adapters if a.name != name]

    @property
    def adapters(self) -> list[ParserAdapter]:
        return list(self._adapters)

    def pick(self, detection: dict) -> tuple[ParserAdapter | None, float]:
        best, score = None, 0.0
        for a in self._adapters:
            try:
                s = float(a.accepts(detection))
            except Exception:  # noqa: BLE001
                s = 0.0
            if s > score:  # strict: ties keep the first registered
                best, score = a, s
        return best, score


registry = Registry()
