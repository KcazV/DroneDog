"""Candidate selection policy: which files inside a source are flight-log artifacts.

A file is selected when ANY rule fires; every rule that fires is recorded as a
reason (goes into artifact_sources.reasons_json and the custody log):

* name   - filename glob (FLY*.DAT, DJIFlightRecord_*.txt, ...). Deleted FAT
           entries lose the first character, so ``?LY*.DAT`` is also matched for
           deleted files.
* dir    - extension allowed inside a log-ish directory (MISC/LOG, FlightRecord,
           MCDatFlightRecords, ...).
* app    - any non-media file under a DJI app log directory (iOS DJI domain or
           Android DJI package path).
* sniff  - the first bytes are recognised by the header sniffer (any name).

Media (by extension) and files outside the sniff size window are not sniffed,
which keeps a walk of a 64 GB card fast. Everything is configurable.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Callable

from dronedog.ingest.sniff import HEAD_BYTES, assistant_export, sniff

MEDIA_EXTS = frozenset("""
.mp4 .mov .m4v .avi .mkv .mts .m2ts .3gp .lrf .thm .jpg .jpeg .dng .png .heic .heif .gif
.bmp .tif .tiff .webp .raw .arw .cr2 .nef .wav .mp3 .aac .m4a .flac .ogg .opus .insv .insp
""".split())

DEFAULT_NAME_GLOBS = (
    "FLY*.DAT",
    "DJIFlightRecord_*.txt",
    "DJIFlightRecord*.txt",
    "*_FLY*.DAT",          # DJI Go / Fly copies: 20-07-10-08-31-09_FLY256.DAT
)
DEFAULT_DELETED_NAME_GLOBS = ("?LY*.DAT", "?JIFlightRecord*.txt", "?JIFLI~*.TXT")

# directory names (path segments) that hold DJI logs
DEFAULT_LOG_DIRS = (
    "MISC", "LOG", "LOGS", "FlightRecord", "FlightRecords", "MCDatFlightRecords",
    "FlightLogs", "flight_logs", "DJI_RECORD", "BlackBox", "DATLog", "FlyLogs",
)
DEFAULT_DIR_EXTS = {
    # extension -> which log dirs allow it (None = any log dir)
    ".dat": None,
    ".txt": ("FlightRecord", "FlightRecords", "flight_logs", "FlightLogs"),
}

# DJI app identifiers (Android package dirs / iOS bundle ids / domains)
DJI_APP_RE = re.compile(
    r"(?i)(^|[/\-\.])((com\.)?dji[\w\.]*|djigo\w*|djifly\w*|dji\.go(\.v\d)?|dji\.pilot\w*|"
    r"com\.dji\.industry\.pilot|dji\.fly)([/\.\-]|$)")
APP_LOG_DIR_RE = re.compile(r"(?i)(^|/)(FlightRecords?|MCDatFlightRecords|FlightLogs?|DJI_RECORD)(/|$)")

ANDROID_APP_RE = re.compile(r"(?i)(?:^|/)(?:Android/data/|data/data/|DJI/)([a-z][\w]*(?:\.[\w]+)+)(?:/|$)")


def app_hint(path: str) -> str | None:
    """Best-effort app identifier from a path or iOS domain (e.g. 'dji.go.v4')."""
    m = ANDROID_APP_RE.search(path)
    if m and "dji" in m.group(1).lower():
        return m.group(1)
    m = re.search(r"(?i)AppDomain(?:Group|Plugin)?-([\w\.\-]+)", path)
    if m:
        return m.group(1)
    if re.search(r"(?i)(^|/)DJI(/|$)", path):
        return "DJI"
    return None


@dataclass
class CandidatePolicy:
    name_globs: tuple[str, ...] = DEFAULT_NAME_GLOBS
    deleted_name_globs: tuple[str, ...] = DEFAULT_DELETED_NAME_GLOBS
    log_dirs: tuple[str, ...] = DEFAULT_LOG_DIRS
    dir_exts: dict = field(default_factory=lambda: dict(DEFAULT_DIR_EXTS))
    media_exts: frozenset = MEDIA_EXTS
    sniff: bool = True
    sniff_min_size: int = 32
    sniff_max_size: int = 4 * 1024 ** 3
    app_dir_any_file: bool = True       # take any non-media file under DJI app log dirs
    max_container_depth: int = 6        # zip-in-zip-in-image ...
    max_nested_zip_bytes: int = 32 * 1024 ** 3
    skip_empty: bool = True
    extra_app_domain_re: str | None = None   # e.g. r"litchi|airdata" to widen iOS/Android matching

    def __post_init__(self):
        self._log_dirs_l = {d.lower() for d in self.log_dirs}
        self._extra_re = re.compile(self.extra_app_domain_re, re.I) if self.extra_app_domain_re else None

    def is_dji_app_path(self, path: str) -> bool:
        if DJI_APP_RE.search(path):
            return True
        return bool(self._extra_re and self._extra_re.search(path))

    def is_media(self, name: str) -> bool:
        i = name.rfind(".")
        return i >= 0 and name[i:].lower() in self.media_exts

    def evaluate(self, path: str, name: str, size: int | None, head: Callable[[int], bytes],
                 deleted: bool = False, force: bool = False) -> tuple[bool, list[str], dict | None]:
        """Returns (selected, reasons, sniff_result)."""
        reasons: list[str] = []
        if force:
            reasons.append("explicit input file")
        if size is not None and size == 0 and self.skip_empty and not force:
            return False, [], None
        lname = name.lower()
        for g in self.name_globs:
            if fnmatch.fnmatch(lname, g.lower()):
                reasons.append(f"name:{g}")
                break
        if deleted:
            for g in self.deleted_name_globs:
                if fnmatch.fnmatch(lname, g.lower()):
                    reasons.append(f"deleted-name:{g}")
                    break
        segs = [s for s in path.replace("\\", "/").split("/")[:-1] if s]
        ext = lname[lname.rfind("."):] if "." in lname else ""
        if ext in self.dir_exts:
            allowed = self.dir_exts[ext]
            allowed_l = None if allowed is None else {a.lower() for a in allowed}
            for s in segs:
                sl = s.lower()
                if sl in self._log_dirs_l and (allowed_l is None or sl in allowed_l):
                    reasons.append(f"dir:{s}/*{ext}")
                    break
        media = self.is_media(name)
        if self.app_dir_any_file and not media and self.is_dji_app_path(path) and APP_LOG_DIR_RE.search(path):
            reasons.append("app:DJI app log directory")
        sn = None
        if self.sniff and not media and (size is None or self.sniff_min_size <= size <= self.sniff_max_size):
            try:
                hb = head(HEAD_BYTES)
            except Exception:  # noqa: BLE001 - unreadable head: rely on name rules
                hb = b""
            sn = sniff(hb, name, size if size is not None else len(hb))
            if sn is not None:
                reasons.append(f"sniff:{sn['kind']}/{sn['subtype']}")
        return bool(reasons), reasons, sn

    def is_assistant_export(self, name: str, head: bytes) -> bool:
        return assistant_export(head, name)


DEFAULT_POLICY = CandidatePolicy()
