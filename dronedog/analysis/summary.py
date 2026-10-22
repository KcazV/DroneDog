"""Examiner summary of one flight record: classification and recorded values.

DJI aircraft write a FLYxxx.DAT every time they are powered on, including power-ons
that only dump logs or sit on a bench, so most records on an internal card are not
flights. ``summarize(...)`` classifies each record from what the record itself shows
and keeps the evidence for that call, then pulls out what an examiner is asked for:
takeoff/landing time and place, home point(s), distance, max height/speed, flight
modes, every serial number / firmware version / unique ID found, and a timeline of
key moments.

Rules (stated in the output under "method" so the examiner can cite them):
  * flight          the flight controller reported the in-air state (is_flying) for at
                    least 2 s; when that state is never reported, motors on with height
                    above takeoff > 1 m for at least 2 s; when no motor-running state is
                    recorded either, height above takeoff > 2 m for at least 5 s.
  * gps_movement    no in-air state, motor-running state or height recorded, and the GPS
                    position moved at least 50 m from the first fix.
  * motors_only     motor start recorded, no in-air state or height gain recorded.
  * powered_on      telemetry decoded, no motor start, in-air state or height gain recorded.
Height before the first GPS fix is not used (it is not referenced to the takeoff point),
unless the in-air state is reported earlier.
  * events_only     no telemetry records decoded for this platform; only the text log.
                    Flight status cannot be established from this record alone.
  * encrypted       record content is encrypted; plaintext header only.
  * empty           nothing decoded at all.

Everything here is derived from the stored parse result; nothing is written back into
it (the result hash stays the parser's own).
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

SUMMARY_VERSION = "1.6"

LABELS = {
    "flight": "Flight",
    "gps_movement": "GPS movement recorded, no in-air state",
    "motors_only": "Motor start recorded, no in-air state",
    "powered_on": "No motor start or in-air state recorded",
    "events_only": "Text log only, no telemetry decoded",
    "encrypted": "Encrypted (header only)",
    "empty": "No decodable data",
}

# modes that mean the aircraft was flying itself home or landing
RTH_MODES = ("gohome", "go_home", "autolanding", "auto_landing", "forcelanding", "force_landing",
             "confirmlanding")


def _hav(lat1, lon1, lat2, lon2) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _ok_fix(lat, lon) -> bool:
    return (lat is not None and lon is not None and not (abs(lat) < 1e-5 and abs(lon) < 1e-5)
            and -90 <= lat <= 90 and -180 <= lon <= 180 and not (math.isnan(lat) or math.isnan(lon)))


def _col(frames: dict, name: str, n: int) -> list:
    v = frames.get(name)
    return v if v is not None and len(v) == n else [None] * n


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _fmt_dur(s: float | None) -> str:
    if s is None:
        return "?"
    s = int(round(s))
    return f"{s // 3600}h {s % 3600 // 60:02d}m {s % 60:02d}s" if s >= 3600 else f"{s // 60}m {s % 60:02d}s"


# -- identifiers ----------------------------------------------------------------------------
# (group, label, regex on the event text). Group names are what the UI and report show.
ID_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("Flight controller", "Flight controller ID (Mc ID)", re.compile(r"Mc\s+ID\s*:\s*([A-Za-z0-9]{6,})")),
    ("Flight controller", "Flight controller firmware (Mc Ver)", re.compile(r"Mc\s+Ver\s*:\s*(v?[\d.]+)")),
    ("Flight controller", "Flight controller firmware build (svn)", re.compile(r"svn\s+Ver\s*:\s*([0-9a-f]{4,})", re.I)),
    ("Flight controller", "Flight controller board", re.compile(r"Board\s*:\s*\"?([A-Za-z0-9_.-]+)")),
    ("Flight controller", "Flight controller firmware time stamp", re.compile(r"<FLY>Time\s*:\s*(\d{4}-\d\d-\d\d[ \d:]*)")),
    ("Flight controller", "Center board log UID", re.compile(r"log_uid=(0x[0-9a-fA-F]+)")),
    ("Flight controller", "Center board version", re.compile(r"centerboard ver=(\d+)")),
    ("Aircraft", "Unique ID (UUID)", re.compile(r"\buuid\b\s*[:=]\s*([0-9A-Fa-f-]{8,})", re.I)),
    ("Aircraft", "UUID set by flight controller (as logged)", re.compile(r"\[set uuid\]\s*:\s*([^\s,;]+)", re.I)),
    ("Account", "User ID (GEO module, as logged)", re.compile(r"\[L-GEO\].*?user id updated\s*\S*?=>\s*(\d{4,})", re.I)),
    ("Aircraft", "Activation product ID (proId)", re.compile(r"proId\s*:\s*([A-Za-z0-9]{6,})")),
    ("Aircraft", "Activation record date", re.compile(r"\[L-ACTIVATION\].*?date:\s*([\d-]+),\s*time:\s*([\d-]+)")),
    ("Battery", "Battery serial number", re.compile(r"Bat\s+SN\s*:\s*([A-Z0-9]{8,}(?:\s*&\s*[A-Z0-9]{8,})*)")),
    ("Battery", "Battery serial number", re.compile(r"\[bat\]sn:\s*([A-Z0-9]{8,})")),
    ("Battery", "Battery barcode / serial", re.compile(r"Battery barcode\s*:\s*([A-Z0-9]{6,})")),
    ("Battery", "Battery barcode / serial", re.compile(r"barcode\[\d+\]\s*:\s*([A-Z0-9]{6,})")),
    ("Battery", "Battery barcode / serial", re.compile(r"^\s*(?:\S+\s+)?Barcode\s*:\s*([A-Z0-9]{6,})")),
    ("Battery", "Battery cell maker / pack name", re.compile(r"Battery name\s*:\s*(.+?)\s*$")),
    ("Battery", "Battery management chip serial", re.compile(r"Serial number\s*:\s*(\S+)")),
    ("Battery", "Battery gauge manufacturer", re.compile(r"Manufacturer name\s*:\s*(.+?)\s*$")),
    ("Battery", "Battery gauge chip", re.compile(r"Device name\s*:\s*(.+?)\s*$")),
    ("Battery", "Battery firmware", re.compile(r"bat app version\s*:\s*\(?([\d.]+)")),
    ("Battery", "Battery firmware (FC report)", re.compile(r"Bat\s+Ver\s*:\s*(v?[\d.]+)")),
    ("Motors / ESC", "ESC hardware", re.compile(r"(ESC\d)\s+version:.*Hardware\s*=\s*\"([^\"]+)\"")),
    ("Motors / ESC", "ESC firmware", re.compile(r"^\s*(?:\S+\s+)?Firmware\s*=\s*\[([^\]]+)\]")),
    ("Navigation", "Navigation (AHRS) product", re.compile(r"\[AHRS\]product:\s*(\S+)")),
    ("Navigation", "Navigation (AHRS) version", re.compile(r"\[AHRS\]version:\s*(\S+)")),
    ("Sensors", "IMU sensor ID", re.compile(r"sensor_id:\s*([0-9a-fA-F]{4,})")),
]
_JUNK_VALUES = {"v255.255.255.255", "255.255.255.255", "ffff", "0", "null", "none"}

DETAIL_IDS = [  # (group, label, details key)
    ("Aircraft", "Aircraft serial number", "aircraft_sn"),
    ("Aircraft", "Aircraft name (set by owner)", "aircraft_name"),
    ("Aircraft", "Aircraft model", "aircraft_model"),
    ("Aircraft", "Product type code", "product_type_code"),
    ("Flight controller", "Flight controller serial", "fc_sn"),
    ("Flight controller", "Firmware (log header)", "firmware_version"),
    ("Camera", "Camera serial number", "camera_sn"),
    ("Remote controller", "Remote controller serial number", "rc_sn"),
    ("Battery", "Battery serial number", "battery_sn"),
    ("App", "App platform", "app_platform"),
    ("App", "App version", "app_version"),
    ("File", "Log format", "log_version"),
    ("File", "DAT build string", "dat_build_string"),
]


def identifiers(details: dict, events: list[dict]) -> list[dict]:
    """Every identifier in the record, de-duplicated by (label, value), with where it came from."""
    out: list[dict] = []
    seen: set = set()

    def add(group, label, value, source, t=None):
        v = str(value).strip().strip('"').strip()
        if not v or v.lower() in _JUNK_VALUES or "�" in v:
            return
        key = (label, v)
        if key in seen:
            for o in out:
                if (o["label"], o["value"]) == key:
                    o["seen"] += 1
            return
        seen.add(key)
        out.append({"group": group, "label": label, "value": v, "source": source,
                    "t_offset_ms": t, "seen": 1})

    # decoder <= 0.1.0 put the battery gauge chip's counter ("[L-BATTERY]Serial number") in
    # battery_sn; never report that as the pack serial
    gauge = {m.group(1) for e in events or [] for m in [re.search(r"\[L-BATTERY\]Serial number\s*:\s*(\S+)",
                                                                  str(e.get("message") or ""))] if m}
    for group, label, key in DETAIL_IDS:
        v = details.get(key)
        if key == "battery_sn" and str(v) in gauge:
            continue
        if v not in (None, "", []):
            add(group, label, v, f"log header field '{key}'")
    for k, v in (details.get("component_serials") or {}).items():
        if k == "dat_header_serial_xor55":
            add("File", "DAT header ID (XOR-0x55 decoded, meaning unproven)", v, "DAT file header")
        elif k == "battery_gauge_serial":
            add("Battery", "Battery management chip serial", v, "text log '[L-BATTERY]Serial number'")
        else:
            add("Components", f"Serial: {k}", v, "log header component_serials")
    for k, v in (details.get("firmware_versions") or {}).items():
        add("Components", f"Firmware: {k}", v, "log header firmware_versions")
    battery_ctx = False
    for e in events or []:
        msg = str(e.get("message") or "")
        if "=>" in msg and "user id updated" not in msg:   # parameter dump lines never carry identifiers
            continue
        for line in msg.split("\n"):
            low = line.lower()
            if "battery" in low or "bat_log" in low or "[bat]" in low:
                battery_ctx = True
            for group, label, rx in ID_PATTERNS:
                m = rx.search(line)
                if not m:
                    continue
                if label == "Battery management chip serial" and not (battery_ctx or "batter" in low):
                    continue
                if label == "Battery serial number" and "&" in m.group(1):
                    for part in m.group(1).split("&"):
                        add(group, label, part, f"text log: {line.strip()[:160]}", e.get("t_offset_ms"))
                    continue
                if label == "ESC hardware":
                    add(group, f"{label} ({m.group(1)})", m.group(2), f"text log: {line.strip()[:160]}",
                        e.get("t_offset_ms"))
                    continue
                if label == "Activation record date":
                    add(group, label, f"{m.group(1)} {m.group(2).replace('-', ':')}",
                        f"text log: {line.strip()[:160]}", e.get("t_offset_ms"))
                    continue
                add(group, label, m.group(1), f"text log: {line.strip()[:160]}", e.get("t_offset_ms"))
    order = ["Aircraft", "Account", "Flight controller", "Battery", "Camera", "Remote controller", "Motors / ESC",
             "Navigation", "Sensors", "Components", "App", "File"]
    out.sort(key=lambda o: (order.index(o["group"]) if o["group"] in order else 99))
    return out


# -- home points ---------------------------------------------------------------------------
_HOME_RX = re.compile(r"\[L-HOME\]\s*(First|Update(?: RTK)?|Set|Record)\.?\s*lati:\s*(-?[\d.]+)\s*longti:\s*(-?[\d.]+)"
                      r"(?:\s*alti:\s*(-?[\d.]+))?", re.I)


def home_points(details: dict, frames: dict, events: list[dict], n: int) -> list[dict]:
    """Home point history: flight-controller text log lines, the OSD home record in the
    telemetry, and the app header. Each entry says where it came from."""
    out = []
    t = _col(frames, "t_offset_ms", n)
    hl, hn = _col(frames, "home_lat", n), _col(frames, "home_lon", n)
    prev = None
    for i in range(n):
        if not _ok_fix(hl[i], hn[i]):
            continue
        key = (round(hl[i], 6), round(hn[i], 6))
        if prev is None or _hav(prev[0], prev[1], key[0], key[1]) > 2.0:
            out.append({"lat": hl[i], "lon": hn[i], "t_offset_ms": t[i], "source": "telemetry (OSD home record)",
                        "kind": "recorded" if prev is None else "updated", "frame_idx": i})
            prev = key
    for e in events or []:
        m = _HOME_RX.search(str(e.get("message") or ""))
        if m:
            lat, lon = float(m.group(2)), float(m.group(3))
            if _ok_fix(lat, lon):
                rtk = "RTK" in m.group(1).upper()
                out.append({"lat": lat, "lon": lon, "alt_msl_m": float(m.group(4)) if m.group(4) else None,
                            "t_offset_ms": e.get("t_offset_ms"),
                            "source": f"flight-controller log '[L-HOME]{m.group(1)}'" + (" (RTK base reference)" if rtk else ""),
                            "kind": "recorded" if m.group(1).lower() == "first" else "updated", "rtk": rtk})
    if not out and _ok_fix(details.get("home_lat"), details.get("home_lon")):
        out.append({"lat": details["home_lat"], "lon": details["home_lon"], "t_offset_ms": None,
                    "source": "log header (app-recorded home)", "kind": "recorded"})
    out.sort(key=lambda h: (h.get("t_offset_ms") is None, h.get("t_offset_ms") or 0))
    return out


# -- segments ------------------------------------------------------------------------------
def _segments(t, flying, motor, alt) -> tuple[list[tuple[int, int]], str]:
    n = len(t)
    min_ms = 2000
    if any(bool(v) for v in flying):
        state = [bool(v) for v in flying]
        basis = "in-air flag (is_flying) reported by the flight controller"
    elif any(bool(v) for v in motor) and any(v is not None for v in alt):
        state = [bool(motor[i]) and (alt[i] or 0) > 1.0 for i in range(n)]
        basis = "motors on with height above takeoff > 1 m (in-air flag not reported in this log)"
    elif any(v is not None for v in alt):
        state = [(alt[i] or 0) > 2.0 for i in range(n)]
        basis = "height above takeoff > 2 m for at least 5 s (in-air flag and motor-running state not reported in this log)"
        min_ms = 5000
    else:
        return [], "no in-air flag, motor state or height in this log"
    runs, start = [], None
    for i in range(n):
        if state[i] and start is None:
            start = i
        elif not state[i] and start is not None:
            runs.append([start, i - 1])
            start = None
    if start is not None:
        runs.append([start, n - 1])

    def ms(i):
        return t[i] if t[i] is not None else 0

    merged: list[list[int]] = []
    for r in runs:                       # bridge flicker shorter than 5 s
        if merged and ms(r[0]) - ms(merged[-1][1]) < 5000:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    keep = [(a, b) for a, b in merged if ms(b) - ms(a) >= min_ms]
    return keep, basis


# -- main ----------------------------------------------------------------------------------
def summarize(details: dict, frames: dict, events: list[dict], *, encrypted: bool = False,
              records_total: int | None = None, source: str | None = None, locate=None) -> dict:
    """``locate(lat, lon) -> dict|None`` is the offline geocoder (dronedog.analysis.geocode.locate)."""
    details = details or {}
    frames = frames or {}
    events = events or []
    n = max((len(v) for v in frames.values()), default=0)
    t = _col(frames, "t_offset_ms", n)
    lat, lon = _col(frames, "lat", n), _col(frames, "lon", n)
    alt = _col(frames, "alt_rel_m", n)
    if not any(v is not None for v in alt):
        alt = _col(frames, "height_m", n)
    alt_msl = _col(frames, "alt_msl_m", n)
    spd = _col(frames, "speed_h_ms", n)
    vd = _col(frames, "vel_d", n)
    flying, motor = _col(frames, "is_flying", n), _col(frames, "motor_on", n)
    mode = _col(frames, "flight_mode", n)
    sats = _col(frames, "gps_sats", n)
    bpct, bv = _col(frames, "battery_pct", n), _col(frames, "battery_v", n)
    tutc = _col(frames, "t_utc", n)
    start = _parse_iso(details.get("start_time_utc"))

    def utc_at(i):
        if i is None:
            return None
        if tutc[i]:
            return _iso(_parse_iso(tutc[i]))
        if start is not None and t[i] is not None:
            return _iso(start + timedelta(milliseconds=t[i]))
        return None

    def fix_near(i, forward=True, limit=600):
        rng = range(i, min(n, i + limit)) if forward else range(i, max(-1, i - limit), -1)
        for j in rng:
            if _ok_fix(lat[j], lon[j]):
                return j
        return None

    gps_idx = [i for i in range(n) if _ok_fix(lat[i], lon[i])]
    # height is referenced to the takeoff/home point; before the first GPS fix it is not
    # (unless the flight controller already reports the aircraft in the air)
    first_air = next((i for i in range(n) if flying[i]), None)
    alt_from = min(gps_idx[0], first_air if first_air is not None else n) if gps_idx else 0
    height_note = None
    if alt_from and any(v is not None for v in alt[:alt_from]):
        alt = [None] * alt_from + alt[alt_from:]
        height_note = (f"Height before the first GPS fix (+{(t[alt_from] or 0) / 1000:.1f} s) is not used: "
                       "it is not referenced to the takeoff point.")
    segs, basis = _segments(t, flying, motor, alt)
    motor_any = any(bool(v) for v in motor)
    no_state = not any(bool(v) for v in flying) and not motor_any and not any(v is not None for v in alt)
    gps_moved = None
    if gps_idx:
        f0 = gps_idx[0]
        gps_moved = max(_hav(lat[f0], lon[f0], lat[i], lon[i]) for i in gps_idx[:: max(1, len(gps_idx) // 4000)])

    if encrypted:
        cls = "encrypted"
    elif segs:
        cls = "flight"
    elif no_state and gps_moved is not None and gps_moved >= 50:
        cls = "gps_movement"
    elif motor_any:
        cls = "motors_only"
    elif n:
        cls = "powered_on"
    elif events or (records_total or 0) > 0:
        cls = "events_only"
    else:
        cls = "empty"

    evidence: list[str] = []
    air_s = sum(((t[b] or 0) - (t[a] or 0)) / 1000.0 for a, b in segs)
    if segs:
        evidence.append(f"In-air for {_fmt_dur(air_s)} in {len(segs)} segment(s), from {basis}.")
    seg_idx = [i for a, b in segs for i in range(a, b + 1)]
    in_air = seg_idx or list(range(n))
    alts = [alt[i] for i in in_air if alt[i] is not None]
    max_alt = max(alts) if alts else None
    if motor_any:
        mt = [i for i in range(n) if motor[i]]
        evidence.append(f"Motors running from +{(t[mt[0]] or 0) / 1000:.1f} s to +{(t[mt[-1]] or 0) / 1000:.1f} s.")
    elif n:
        evidence.append("No motor-running state recorded.")
    if max_alt is not None:
        evidence.append(f"Maximum height above takeoff point: {max_alt:.1f} m ({max_alt * 3.28084:.0f} ft).")
    if height_note:
        evidence.append(height_note)
    takeoff_i = landing_i = None
    if segs:
        takeoff_i = fix_near(segs[0][0]) if gps_idx else segs[0][0]
        landing_i = fix_near(segs[-1][1], forward=False) if gps_idx else segs[-1][1]
        takeoff_i = segs[0][0] if takeoff_i is None else takeoff_i
        landing_i = segs[-1][1] if landing_i is None else landing_i
    # path distance and furthest point over the flown part only
    path_m, prev = 0.0, None
    pts = [i for i in in_air if _ok_fix(lat[i], lon[i])]
    for i in pts:
        if prev is not None:
            d = _hav(lat[prev], lon[prev], lat[i], lon[i])
            dt = max(0.05, ((t[i] or 0) - (t[prev] or 0)) / 1000.0)
            if d / dt < 60:            # drop GPS jumps faster than any small UAS flies
                path_m += d
        prev = i
    if pts and cls == "gps_movement":
        evidence.append(f"GPS position moved up to {gps_moved:.0f} m from the first fix; in-air flag, motor-running "
                        "state and height are not recorded in this log.")
    if pts and segs:
        a, b = pts[0], pts[-1]
        moved = max(_hav(lat[a], lon[a], lat[i], lon[i]) for i in pts[:: max(1, len(pts) // 2000)] + [b])
        evidence.append(f"GPS position moved up to {moved:.0f} m from the takeoff point while in the air.")
    modes_seen: list[str] = []
    for m in mode:
        if m and (not modes_seen or modes_seen[-1] != m):
            modes_seen.append(m)
    mode_names = list(dict.fromkeys(modes_seen))
    if mode_names:
        evidence.append("Flight modes reported: " + ", ".join(mode_names[:12]) + ".")
    if cls == "events_only":
        evidence.append("No telemetry (position/attitude/motor) records could be decoded for this platform, "
                        "so flight status cannot be established from this record; see the text log.")
    if cls == "encrypted":
        evidence.append("Record content is encrypted; only the plaintext header is readable.")

    homes = home_points(details, frames, events, n)
    home = None
    if homes:
        if takeoff_i is not None and t[takeoff_i] is not None:
            # the home in effect at takeoff: last one recorded at or before (or first after) takeoff
            before = [h for h in homes if h.get("t_offset_ms") is not None and h["t_offset_ms"] <= t[takeoff_i] + 15000
                      and not h.get("rtk")]
            home = (before[-1] if before else next((h for h in homes if not h.get("rtk")), homes[0]))
        else:
            home = next((h for h in homes if not h.get("rtk")), homes[0])

    def place(i):
        if i is None or not _ok_fix(lat[i], lon[i]):
            return None
        return {"t_offset_ms": t[i], "t_utc": utc_at(i), "lat": lat[i], "lon": lon[i],
                "alt_msl_m": alt_msl[i], "alt_rel_m": alt[i],
                "location": _safe_locate(locate, lat[i], lon[i])}

    takeoff = place(takeoff_i) if takeoff_i is not None else None
    if takeoff is None and takeoff_i is not None:
        takeoff = {"t_offset_ms": t[takeoff_i], "t_utc": utc_at(takeoff_i)}
    landing = place(landing_i) if landing_i is not None else None
    if landing is None and landing_i is not None:
        landing = {"t_offset_ms": t[landing_i], "t_utc": utc_at(landing_i)}
    if home:
        home = dict(home, location=_safe_locate(locate, home["lat"], home["lon"]))
        if takeoff and takeoff.get("lat") is not None:
            home["distance_from_takeoff_m"] = round(_hav(home["lat"], home["lon"], takeoff["lat"], takeoff["lon"]), 1)
            first_fix_ms = t[gps_idx[0]] if gps_idx else None
            early = (home.get("t_offset_ms") is not None and first_fix_ms is not None
                     and home["t_offset_ms"] <= first_fix_ms + 1000)
            if home["distance_from_takeoff_m"] > 30:
                home["note"] = (f"Home point is {home['distance_from_takeoff_m']:.0f} m from this takeoff point"
                                + (" and was already present when the log started, before this power-on had its own GPS fix"
                                   if early else "")
                                + ". DJI aircraft retain the last recorded home point until a new one is set; this record "
                                  "does not show when this home point was first recorded. Return-to-home targets the "
                                  "recorded home point.")
                evidence.append(home["note"])
    max_from_home = None
    if home and pts:
        max_from_home = max(_hav(home["lat"], home["lon"], lat[i], lon[i]) for i in pts[:: max(1, len(pts) // 4000)])

    # return-to-home / auto-landing periods from the mode column
    rth = []
    cur = None
    for i in range(n):
        m = (mode[i] or "").lower().replace(" ", "")
        hit = any(k in m for k in RTH_MODES)
        if hit and cur is None:
            cur = {"start_idx": i, "mode": mode[i]}
        elif not hit and cur is not None:
            cur["end_idx"] = i - 1
            rth.append(cur)
            cur = None
    if cur is not None:
        cur["end_idx"] = n - 1
        rth.append(cur)
    rth_out = [{"mode": r["mode"], "t_start_ms": t[r["start_idx"]], "t_end_ms": t[r["end_idx"]],
                "t_start_utc": utc_at(r["start_idx"]),
                "lat": lat[r["start_idx"]] if _ok_fix(lat[r["start_idx"]], lon[r["start_idx"]]) else None,
                "lon": lon[r["start_idx"]] if _ok_fix(lat[r["start_idx"]], lon[r["start_idx"]]) else None}
               for r in rth]

    spds = [spd[i] for i in in_air if spd[i] is not None]
    vds = [abs(vd[i]) for i in in_air if vd[i] is not None]
    bp = [x for x in bpct if x is not None]
    bvv = [x for x in bv if x is not None]
    sat = [x for x in sats if x is not None]
    dur = details.get("duration_s")
    if dur is None and n and t[0] is not None and t[-1] is not None:
        dur = (t[-1] - t[0]) / 1000.0
    stats = {
        "record_duration_s": dur,
        "air_time_s": round(air_s, 1) if segs else None,
        "takeoffs": len(segs),
        "max_height_m": round(max_alt, 1) if max_alt is not None else details.get("max_height_m"),
        "max_speed_ms": round(max(spds), 1) if spds else details.get("max_h_speed_ms"),
        "max_vspeed_ms": round(max(vds), 1) if vds else details.get("max_v_speed_ms"),
        "path_distance_m": round(path_m, 1) if pts and (segs or cls == "gps_movement") else details.get("total_distance_m"),
        "max_distance_from_home_m": round(max_from_home, 1) if max_from_home is not None else None,
        "gps_points": len(gps_idx),
        "gps_sats_max": max(sat) if sat else None,
        "battery_start_pct": bp[0] if bp else None,
        "battery_end_pct": bp[-1] if bp else None,
        "battery_min_v": round(min(bvv), 2) if bvv else None,
        "flight_modes": mode_names,
    }

    timeline = _timeline(n, t, utc_at, lat, lon, motor, segs, max_alt, alt, homes, rth_out, gps_idx, events)
    loc = None
    for cand in (takeoff, home):
        if cand and cand.get("location"):
            loc = cand["location"]
            break
    if loc is None and gps_idx:
        loc = _safe_locate(locate, lat[gps_idx[0]], lon[gps_idx[0]])
    app_place = ", ".join(str(details[k]) for k in ("sub_street", "street", "city", "area")
                          if details.get(k) and "�" not in str(details[k])) or None
    ids = identifiers(details, events)
    out = {
        "version": SUMMARY_VERSION,
        "classification": cls,
        "label": LABELS[cls],
        "is_real_flight": cls == "flight",
        "evidence": evidence,
        "method": {"flight_basis": basis,
                   "rules": "flight = in-air flag for >= 2 s (gaps < 5 s bridged); when the flag is never "
                            "reported, motors on with height > 1 m for >= 2 s; when no motor-running state "
                            "is recorded either, height > 2 m for >= 5 s. Height before the first GPS fix "
                            "is not used. GPS steps implying > 60 m/s are excluded from distance."},
        "takeoff": takeoff, "landing": landing,
        "home": home, "home_points": homes,
        "rth": rth_out,
        "stats": stats,
        "location": loc,
        "app_location": app_place,
        "identifiers": ids,
        "timeline": timeline,
        "source": source,
    }
    out["narrative"] = narrative(out)
    return out


def _safe_locate(locate, la, lo):
    if locate is None or not _ok_fix(la, lo):
        return None
    try:
        return locate(la, lo)
    except Exception:  # noqa: BLE001 - geocoding is a convenience, never fails the summary
        return None


def _timeline(n, t, utc_at, lat, lon, motor, segs, max_alt, alt, homes, rth, gps_idx, events) -> list[dict]:
    out = []

    def add(i, what, t_ms=None, **kw):
        tm = t[i] if i is not None else t_ms
        out.append(dict({"t_offset_ms": tm, "t_utc": utc_at(i) if i is not None else None, "event": what}, **kw))

    if n:
        add(0, "Log starts (aircraft powered on / logging began)")
    if gps_idx:
        add(gps_idx[0], "First GPS position", lat=lat[gps_idx[0]], lon=lon[gps_idx[0]])
    for h in homes[:6]:
        out.append({"t_offset_ms": h.get("t_offset_ms"), "t_utc": None,
                    "event": f"Home point {h['kind']} ({h['source']})", "lat": h["lat"], "lon": h["lon"]})
    mt = [i for i in range(n) if motor[i]]
    if mt:
        add(mt[0], "Motors started")
    for k, (a, b) in enumerate(segs, 1):
        add(a, f"Takeoff{'' if len(segs) == 1 else f' #{k}'}", lat=lat[a], lon=lon[a])
        add(b, f"Landing{'' if len(segs) == 1 else f' #{k}'}", lat=lat[b], lon=lon[b])
    if max_alt is not None and segs:
        i = max((i for a, b in segs for i in range(a, b + 1) if alt[i] is not None), key=lambda i: alt[i], default=None)
        if i is not None:
            add(i, f"Maximum height {alt[i]:.1f} m above takeoff", lat=lat[i], lon=lon[i])
    for r in rth:
        out.append({"t_offset_ms": r["t_start_ms"], "t_utc": r["t_start_utc"],
                    "event": f"Return-to-home / auto-landing mode: {r['mode']}", "lat": r["lat"], "lon": r["lon"]})
    if mt:
        add(mt[-1], "Motors stopped")
    if n:
        add(n - 1, "Log ends")
    out.sort(key=lambda e: (e["t_offset_ms"] is None, e["t_offset_ms"] or 0))
    return out


def narrative(s: dict) -> str:
    """Statement of recorded values, built only from the fields above."""
    st = s["stats"]
    cls = s["classification"]
    if cls != "flight":
        base = {"motors_only": "Motor start recorded. No in-air state or height gain recorded.",
                "powered_on": "Telemetry recorded. No motor start, in-air state or height gain recorded.",
                "gps_movement": "GPS movement recorded. No in-air state, motor-running state or height recorded.",
                "events_only": "Flight-controller text log decoded. No position or motor telemetry decoded; "
                               "flight status cannot be established from this record.",
                "encrypted": "Record content encrypted. Header metadata only.",
                "empty": "No decodable data."}[cls]
        if st.get("record_duration_s"):
            base += f" Log duration: {_fmt_dur(st['record_duration_s'])}."
        if cls == "gps_movement":
            if st.get("path_distance_m"):
                base += f" GPS track distance: {st['path_distance_m'] / 1000:.2f} km."
            if st.get("max_speed_ms"):
                base += f" Maximum ground speed: {st['max_speed_ms']:.1f} m/s ({st['max_speed_ms'] * 2.23694:.0f} mph)."
        return base
    to, la, home = s.get("takeoff") or {}, s.get("landing") or {}, s.get("home") or {}
    parts = []
    where = (to.get("location") or {}).get("summary")
    when = to.get("t_utc")
    parts.append("Takeoff recorded" + (f" at {when.replace('T', ' ').rstrip('Z')} UTC" if when else "")
                 + (f", {where}" if where else (f", {to['lat']:.6f}, {to['lon']:.6f}" if to.get("lat") is not None else ""))
                 + ".")
    if st.get("air_time_s"):
        parts.append(f"Air time: {_fmt_dur(st['air_time_s'])}.")
    if st.get("path_distance_m"):
        parts.append(f"Distance: {st['path_distance_m'] / 1000:.2f} km.")
    if st.get("max_height_m") is not None:
        parts.append(f"Maximum height above takeoff point: {st['max_height_m']:.0f} m ({st['max_height_m'] * 3.28084:.0f} ft).")
    if st.get("max_speed_ms"):
        parts.append(f"Maximum ground speed: {st['max_speed_ms']:.1f} m/s ({st['max_speed_ms'] * 2.23694:.0f} mph).")
    if st.get("max_distance_from_home_m"):
        parts.append(f"Maximum distance from home point: {st['max_distance_from_home_m']:.0f} m.")
    if s.get("rth"):
        parts.append("Return-to-home / auto-landing modes recorded: " + ", ".join(dict.fromkeys(r["mode"] for r in s["rth"])) + ".")
    if la.get("lat") is not None:
        d = _hav(to["lat"], to["lon"], la["lat"], la["lon"]) if to.get("lat") is not None else None
        parts.append("Landing recorded" + (f" at {la['t_utc'].replace('T', ' ').rstrip('Z')} UTC" if la.get("t_utc") else "")
                     + (f", {d:.0f} m from the takeoff point" if d is not None else "") + ".")
    if home.get("note"):
        parts.append(home["note"])
    elif home.get("lat") is not None and home.get("distance_from_takeoff_m") is not None:
        parts.append(f"Home point: {home['distance_from_takeoff_m']:.0f} m from the takeoff point.")
    return " ".join(parts)


def default_locator():
    """The offline geocoder's ``locate`` when its data file is present, else None."""
    try:
        from dronedog.analysis import geocode
        return geocode.locate if geocode.available() else None
    except Exception:  # noqa: BLE001
        return None


def summarize_result(result: dict, source: str | None = None) -> dict:
    """Summary straight from a C1 ParseResult (used by the import pipeline)."""
    return summarize(result.get("details") or {}, result.get("frames") or {}, result.get("events") or [],
                     encrypted=bool(result.get("encrypted_unreadable")), records_total=result.get("records_total"),
                     source=source, locate=default_locator())
