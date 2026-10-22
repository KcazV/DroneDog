"""DJI GO 4 phone-side "flight tips" logs (detector + adapter "app_tips_json").

Found in the DJI GO 4 app sandbox, e.g. iOS ``AppDomain-com.dji.go/Documents/FlightLogs/
2017-08-29 11_49_33-08RDD9100100KG.dat``: one file per app session, named
``YYYY-MM-DD HH_MM_SS-<aircraft serial>.dat``. Content is plaintext JSON-like text: a
comma-separated run of arrays, each an array of ``[local_time, category, message, ...]``
entries, e.g. ``[["2017-08-29 11:49:33","Tips","Taking Off"]],[["...","","Home Point
Recorded. RTH Altitude: 30m."]]``. Wrapped in ``[...]`` it is a valid JSON document.

Times are the phone's LOCAL wall-clock time with no UTC offset. This adapter never invents
an offset: ``start_time_utc`` stays null and ``x.timezone`` says so. The correlation stage
(dronedog/analysis/correlate.py) may later infer the offset from a matching DJI app TXT log
and back-fills ``start_time_utc``, recording how in the link reasons.

There is no position data, so ``frames`` is empty; every message becomes an event.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

ADAPTER_VERSION = "app_tips_json 0.1.0"
KIND = "dji_app_tips"
SUBTYPE = "go4_tips_json"
MAX_BYTES = 8 * 1024 * 1024          # these files are a few KB; refuse anything huge

NAME_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2}) (?P<h>\d{2})_(?P<m>\d{2})_(?P<s>\d{2})-"
                     r"(?P<sn>[A-Za-z0-9]{6,20})\.dat$", re.I)
TS_FMT = "%Y-%m-%d %H:%M:%S"
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
# "[[" + timestamp (file as written) or "[[[" + timestamp (already one JSON document)
_HEAD_RE = re.compile(rb'^\s*(?:\[\s*){2,3}"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"')

WARN_WORDS = ("low battery", "signal", "lost", "compass", "obstacle", "failed",
              "fail", "cannot", "can't", "unable", "warning", "weak", "interference",
              "not responding", "error", "max flight distance", "max altitude", "imu")
CRITICAL_WORDS = ("critically low", "critical", "force landing", "forced landing",
                  "auto landing due", "crash")


def _parse_text(raw: bytes):
    """Return (entries, reasons) or (None, reasons). entries = [[ts, cat, msg, *rest], ...]."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, ["not UTF-8 text"]
    s = text.strip().rstrip(",")
    if not s.startswith("["):
        return None, ["does not start with '['"]
    data = None
    for cand, how in ((s, "a single JSON array"), ("[" + s + "]", "comma-separated JSON arrays")):
        try:
            data = json.loads(cand)
            break
        except ValueError:
            continue
    if data is None:
        return None, ["not JSON (as one array or as comma-separated arrays)"]
    groups = data if data and isinstance(data[0], list) and data[0] and isinstance(data[0][0], list) \
        else [data]
    entries = []
    for g in groups:
        if not isinstance(g, list):
            return None, ["unexpected JSON shape (group is not a list)"]
        for e in g:
            if not (isinstance(e, list) and len(e) >= 3 and all(isinstance(x, str) for x in e[:3])
                    and _TS_RE.match(e[0])):
                return None, ["unexpected entry shape (want [\"YYYY-MM-DD HH:MM:SS\", category, "
                              "message, ...])"]
            entries.append(e)
    if not entries:
        return None, ["no entries"]
    return entries, ["parses as %s of [local_time, category, message] entries (%d groups, %d "
                     "entries)" % (how, len(groups), len(entries))]


def sniff(path, head_bytes: bytes) -> dict | None:
    """C1-shaped detection for a GO 4 tips log, or None. Reads at most MAX_BYTES."""
    if not _HEAD_RE.match(head_bytes or b""):
        return None
    p = Path(path)
    try:
        size = p.stat().st_size
        if size > MAX_BYTES:
            return None
        raw = p.read_bytes()
    except OSError:
        return None
    entries, reasons = _parse_text(raw)
    if entries is None:
        return None
    conf = 0.8
    m = NAME_RE.search(_orig_name(p.name))
    if m:
        conf = 0.95
        reasons.append("filename matches DJI GO 4 tips naming 'YYYY-MM-DD HH_MM_SS-<serial>.dat' "
                       "(serial %s)" % m.group("sn"))
    return {"kind": KIND, "subtype": SUBTYPE, "version": None, "encrypted": False,
            "confidence": conf, "reasons": reasons}


def _orig_name(name: str) -> str:
    """Extracted artifacts are stored as '<sha256[:12]>_<origname>' (C5)."""
    m = re.match(r"^[0-9a-f]{12}_(.+)$", name)
    return m.group(1) if m else name


def _severity(cat: str, msg: str) -> str:
    low = (cat + " " + msg).lower()
    if any(w in low for w in CRITICAL_WORDS):
        return "critical"
    if cat.lower() == "warning" or any(w in low for w in WARN_WORDS):
        return "warn"
    return "info"


def parse_tips(path, name_hint: str | None = None) -> dict:
    p = Path(path)
    raw = p.read_bytes()[:MAX_BYTES]
    entries, reasons = _parse_text(raw)
    warnings: list[str] = []
    if entries is None:
        raise ValueError("not a DJI GO 4 tips log: " + "; ".join(reasons))
    name = name_hint or _orig_name(p.name)
    times = [datetime.strptime(e[0], TS_FMT) for e in entries]
    t0 = min(times)
    events = []
    for e, t in zip(entries, times):
        cat, msg = e[1].strip(), e[2].strip()
        events.append({"t_offset_ms": int((t - t0).total_seconds() * 1000),
                       "kind": cat or "tip", "severity": _severity(cat, msg), "message": msg,
                       "raw": json.dumps(e, ensure_ascii=False)})
    if any(b < a for a, b in zip(times, times[1:])):
        warnings.append("entry times are not in file order; offsets are relative to the earliest")
    m = NAME_RE.search(name)
    sn = m.group("sn") if m else None
    name_local = None
    if m:
        name_local = "%sT%s:%s:%s" % (m.group("date"), m.group("h"), m.group("m"), m.group("s"))
        if datetime.strptime(name_local, "%Y-%m-%dT%H:%M:%S") != t0:
            warnings.append("filename time %s differs from the first entry time %s"
                            % (name_local, t0.isoformat()))
    else:
        warnings.append("filename does not follow 'YYYY-MM-DD HH_MM_SS-<serial>.dat'; no serial")
    warnings.append("times are device-local wall clock with no UTC offset; start_time_utc is "
                    "left empty unless correlation infers the offset from a matching app TXT log")
    dur = (max(times) - t0).total_seconds()
    details = {k: None for k in (
        "aircraft_model", "aircraft_name", "aircraft_sn", "fc_sn", "camera_sn", "rc_sn",
        "battery_sn", "app_platform", "app_version", "log_version", "start_time_utc",
        "duration_s", "total_distance_m", "max_height_m", "max_h_speed_ms", "max_v_speed_ms",
        "home_lat", "home_lon", "city", "area", "sub_street", "firmware_version",
        "record_line_count")}
    details.update({
        "aircraft_sn": sn, "log_version": SUBTYPE, "duration_s": dur,
        "start_time_local": t0.isoformat(), "record_line_count": len(entries),
        "file_size_bytes": len(raw),
        "x": {"kind": "app_tips", "format": "DJI GO 4 flight tips (JSON text)",
              "sn_basis": "filename" if sn else None,
              "timezone": "unknown (device local)", "filename_local_time": name_local,
              "utc_offset_s": None, "utc_offset_basis": None,
              "categories": sorted({e[1] for e in entries})}})
    return {"adapter": "app_tips_json", "adapter_version": ADAPTER_VERSION, "details": details,
            "frames": {}, "events": events, "records_total": len(entries),
            "records_decoded": len(entries), "records_failed": 0, "warnings": warnings,
            "encrypted_unreadable": False}


class AppTipsJsonAdapter:
    name = "app_tips_json"
    formats = "DJI GO 4 phone-side flight tips log (JSON text, local time)"
    needs_keychain = None

    def accepts(self, detection: dict) -> float:
        return 0.9 if detection.get("kind") == KIND else 0.0

    def parse(self, path: Path, ctx) -> dict:
        name = None
        art = getattr(ctx, "artifact", None) or {}
        if art.get("name"):
            name = _orig_name(str(art["name"]))
        res = parse_tips(path, name)
        srcs = getattr(ctx, "sources", None) or []
        for s in srcs:
            rp = s.get("ios_relative_path") or s.get("inner_path")
            if rp:
                res["details"]["x"]["source_path"] = ("%s/%s" % (s.get("ios_domain"), rp)
                                                      if s.get("ios_domain") else rp)
                break
        return res
