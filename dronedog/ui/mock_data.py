"""Synthetic, deterministic case data for the mock UI backend.

NOT EVIDENCE.  Every serial, hash, path and coordinate here is invented.  The data
is shaped like real pipeline output (C2 details, C3 frames, C1 events, C5 rows,
C6 custody entries) so the UI can be built and demonstrated without the core.

Flights:
  1  Phantom 4 Pro V2.0 aircraft DAT, ~14 min: takeoff, manual, waypoint survey,
     orbit, low-battery warning, user RTH, auto landing (50 Hz, ~42k frames)
  2  DJI GO 4 (iOS) TXT of the same flight, 10 Hz, starts earlier / ends later
  3  Mavic 2 Pro DJI GO 4 (Android) TXT: RTH cancelled, critical battery forced landing
  4  Mini 3 Pro DJI Fly TXT v13: encrypted records, plaintext header only
  5  Inspire 2 aircraft DAT recovered as a deleted file from a microSD image
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone

R_EARTH = 6378137.0

CASE_META = {
    "id": "6d0c5b9e-3c1f-4f7e-9a51-mock00000001",
    "name": "Heritage Park UAS incident",
    "case_number": "FH-2026-004817",
    "examiner": "Det. J. Rivera",
    "agency": "Example PD Digital Forensics Lab",
    "created_utc": "2026-10-19T13:02:44Z",
    "tool": "DroneDog",
    "tool_version": "0.1.0-mock",
}
CASE_ROOT = r"C:\DroneDog Cases\FH-2026-004817 Heritage Park"

RECENT = [
    {"path": CASE_ROOT, "name": CASE_META["name"], "case_number": CASE_META["case_number"],
     "opened_utc": "2026-10-20T19:44:10Z", "exists": True},
    {"path": r"C:\DroneDog Cases\FH-2026-003902 Warehouse roof",
     "name": "Warehouse roof survey complaint", "case_number": "FH-2026-003902",
     "opened_utc": "2026-09-28T15:20:03Z", "exists": True},
    {"path": r"D:\Cases\Archive\FH-2025-011250",
     "name": "Stadium TFR violation", "case_number": "FH-2025-011250",
     "opened_utc": "2026-03-02T17:01:55Z", "exists": False},
]


def fake_hash(label: str, algo: str = "sha256") -> str:
    return hashlib.new(algo, ("dronedog-mock:" + label).encode()).hexdigest()


def iso(dt: datetime, ms: bool = True) -> str:
    if ms:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------- simulation --

def _smooth(u: float) -> float:
    return u * u * (3 - 2 * u)


class Path3D:
    """Keyframed path: [(t, x_east_m, y_north_m, z_up_m, mode, opts)]."""

    def __init__(self, keys):
        self.keys = keys

    def pos(self, t: float):
        k = self.keys
        if t <= k[0][0]:
            return k[0][1], k[0][2], k[0][3], k[0][4]
        if t >= k[-1][0]:
            return k[-1][1], k[-1][2], k[-1][3], k[-1][4]
        lo, hi = 0, len(k) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if k[mid][0] <= t:
                lo = mid
            else:
                hi = mid
        a, b = k[lo], k[hi]
        u = (t - a[0]) / (b[0] - a[0])
        lin = a[5].get("lin", False) if len(a) > 5 else False
        e = u if lin else _smooth(u)
        return (a[1] + (b[1] - a[1]) * e, a[2] + (b[2] - a[2]) * e, a[3] + (b[3] - a[3]) * e, a[4])


def orbit_keys(t0, cx, cy, r, z, start_deg, sweep_deg, dur, mode="P-GPS", step=6):
    keys = []
    n = max(2, int(abs(sweep_deg) / step))
    for i in range(n + 1):
        ang = math.radians(start_deg + sweep_deg * i / n)
        keys.append((t0 + dur * i / n, cx + r * math.sin(ang), cy + r * math.cos(ang), z, mode,
                     {"lin": True, "poi": (cx, cy)}))
    return keys


def lawnmower_keys(t0, x0, y0, z, length, spacing, legs, speed, turn_s, mode="Waypoint"):
    keys = []
    t = t0
    x, y = x0, y0
    direction = 1
    for i in range(legs):
        keys.append((t, x, y, z, mode, {"lin": True}))
        x2 = x + direction * length
        t += length / speed
        keys.append((t, x2, y, z, mode, {}))
        if i < legs - 1:
            y += spacing
            t += turn_s
        x = x2
        direction *= -1
    return keys, t


def simulate(spec: dict, hz: float, t_from: float, t_to: float, seed: int, gps_noise_m: float,
             t0_utc: datetime) -> dict:
    """Sample a flight spec into C3 columnar frames between t_from and t_to (spec time)."""
    rnd = random.Random(seed)
    path: Path3D = spec["path"]
    home_lat, home_lon, home_msl = spec["home"]
    coslat = math.cos(math.radians(home_lat))
    bat = spec["battery"]  # [(t, pct)]
    cells = spec.get("cells", 4)
    phases = spec["phases"]  # dict: motor_on (t0, t1), flying (t0, t1)
    mot0, mot1 = phases["motor_on"]
    fly0, fly1 = phases["flying"]
    cols = {c: [] for c in (
        "t_offset_ms t_utc lat lon alt_rel_m alt_msl_m vel_n vel_e vel_d speed_h_ms roll pitch yaw "
        "gimbal_pitch gimbal_roll gimbal_yaw rc_aileron rc_elevator rc_throttle rc_rudder "
        "rc_mode_switch flight_mode gps_sats gps_health battery_pct battery_v battery_cells_v "
        "battery_temp_c home_lat home_lon distance_m height_m motor_on is_flying").split()}
    dt = 1.0 / hz
    n = int((t_to - t_from) * hz) + 1
    yaw = spec.get("initial_yaw", 0.0)
    prev_yaw = yaw
    noise_e = noise_n = 0.0
    sag_cell = spec.get("weak_cell")
    poi_ranges = []
    for k in path.keys:
        if len(k) > 5 and "poi" in k[5]:
            if poi_ranges and poi_ranges[-1][2] == k[5]["poi"]:
                poi_ranges[-1][1] = k[0]
            else:
                poi_ranges.append([k[0], k[0], k[5]["poi"]])
    for i in range(n):
        t = t_from + i * dt
        x, y, z, mode = path.pos(t)
        h = 0.05
        x1, y1, z1, _ = path.pos(t - h)
        x2, y2, z2, _ = path.pos(t + h)
        ve, vn, vu = (x2 - x1) / (2 * h), (y2 - y1) / (2 * h), (z2 - z1) / (2 * h)
        spd = math.hypot(ve, vn)
        flying = fly0 <= t <= fly1
        motors = mot0 <= t <= mot1
        # heading: towards POI during orbits, else direction of travel
        poi = None
        for (pa, pb, pp) in poi_ranges:
            if pa - 4 <= t <= pb:
                poi = pp
                break
        if poi is not None and flying:
            target = math.degrees(math.atan2(poi[0] - x, poi[1] - y))
        elif spd > 1.0:
            target = math.degrees(math.atan2(ve, vn))
        else:
            target = yaw
        d = (target - yaw + 540) % 360 - 180
        yaw = (yaw + d * min(1.0, dt * 1.6) + 180) % 360 - 180
        yaw_rate = ((yaw - prev_yaw + 540) % 360 - 180) / dt
        prev_yaw = yaw
        hr = math.radians(yaw)
        fwd = vn * math.cos(hr) + ve * math.sin(hr)
        lat_v = -vn * math.sin(hr) + ve * math.cos(hr)
        pitch = -max(-20.0, min(25.0, fwd * 1.9)) + rnd.gauss(0, 0.4) if flying else rnd.gauss(0, 0.1)
        roll = max(-25.0, min(25.0, lat_v * 1.9 + math.radians(yaw_rate) * spd * 2.5)) + \
            (rnd.gauss(0, 0.4) if flying else rnd.gauss(0, 0.1))
        # GPS noise: slow random walk
        noise_e = noise_e * 0.98 + rnd.gauss(0, gps_noise_m * 0.2)
        noise_n = noise_n * 0.98 + rnd.gauss(0, gps_noise_m * 0.2)
        lat = home_lat + (y + noise_n) / 111320.0
        lon = home_lon + (x + noise_e) / (111320.0 * coslat)
        # sticks
        manual = mode in ("P-GPS", "Sport", "Tripod") and flying
        if manual:
            ail = max(-1, min(1, lat_v / 14 + rnd.gauss(0, 0.02)))
            ele = max(-1, min(1, fwd / 14 + rnd.gauss(0, 0.02)))
            thr = max(-1, min(1, vu / 4 + rnd.gauss(0, 0.02)))
            rud = max(-1, min(1, yaw_rate / 90 + rnd.gauss(0, 0.02)))
        else:
            ail = ele = rud = 0.0
            thr = 0.0
            if mode == "AutoLanding" and spec.get("pilot_steer_landing"):
                ele = max(-1, min(1, fwd / 14 + rnd.gauss(0, 0.03)))
        if spec.get("stick_override"):
            ail, ele, thr, rud = spec["stick_override"](t, ail, ele, thr, rud)
        # battery
        pct = _interp(bat, t)
        load = (1.0 + 0.04 * spd + 0.25 * max(0.0, vu)) if flying else (0.15 if motors else 0.0)
        cell_v = 3.48 + 0.72 * (max(pct, 0) / 100.0) ** 0.85 - 0.06 * load
        cv = [round(cell_v + rnd.gauss(0, 0.004) + (0.01 * j), 3) for j in range(cells)]
        if sag_cell is not None and pct < spec.get("weak_cell_below", 30):
            cv[sag_cell] = round(cv[sag_cell] - (spec.get("weak_cell_below", 30) - pct) * 0.012, 3)
        bv = round(sum(cv), 3)
        temp = 22.0 + 20.0 * (1 - math.exp(-max(0.0, t - mot0) / 300.0)) if motors or t > mot1 else 22.0
        sats = int(round(16 + 2.5 * math.sin(t / 97.0) + rnd.gauss(0, 0.6)))
        sats = max(9, min(21, sats))
        gps_health = 5 if sats >= 13 else (4 if sats >= 10 else 3)
        if mode == "Waypoint" and flying:
            gp = -90.0
        elif flying:
            gp = -30.0 + 6 * math.sin(t / 23.0)
        else:
            gp = 0.0
        tt = t0_utc + timedelta(seconds=t)
        c = cols
        c["t_offset_ms"].append(int(round((t - t_from) * 1000)))
        c["t_utc"].append(iso(tt))
        c["lat"].append(round(lat, 7))
        c["lon"].append(round(lon, 7))
        c["alt_rel_m"].append(round(z + rnd.gauss(0, 0.05), 2))
        c["alt_msl_m"].append(round(home_msl + z + 1.4 + rnd.gauss(0, 0.2), 2))
        c["vel_n"].append(round(vn, 2))
        c["vel_e"].append(round(ve, 2))
        c["vel_d"].append(round(-vu, 2))
        c["speed_h_ms"].append(round(spd, 2))
        c["roll"].append(round(roll, 1))
        c["pitch"].append(round(pitch, 1))
        c["yaw"].append(round(yaw, 1))
        c["gimbal_pitch"].append(round(gp, 1))
        c["gimbal_roll"].append(0.0)
        c["gimbal_yaw"].append(round(yaw, 1))
        c["rc_aileron"].append(round(ail, 3))
        c["rc_elevator"].append(round(ele, 3))
        c["rc_throttle"].append(round(thr, 3))
        c["rc_rudder"].append(round(rud, 3))
        c["rc_mode_switch"].append("P")
        c["flight_mode"].append(mode)
        c["gps_sats"].append(sats)
        c["gps_health"].append(gps_health)
        c["battery_pct"].append(round(pct, 1))
        c["battery_v"].append(bv)
        c["battery_cells_v"].append(cv)
        c["battery_temp_c"].append(round(temp, 1))
        c["home_lat"].append(home_lat)
        c["home_lon"].append(home_lon)
        c["distance_m"].append(round(math.hypot(x, y), 1))
        c["height_m"].append(round(z, 2))
        c["motor_on"].append(motors)
        c["is_flying"].append(flying)
    return cols


def _interp(pts, t):
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


# --------------------------------------------------------------------- flight specs --

def _spec_phantom() -> dict:
    keys = [
        (0, 0, 0, 0, "P-GPS"),
        (20, 0, 0, 0, "P-GPS"),
        (24, 0, 0, 1.2, "AutoTakeoff"),
        (27, 0, 0, 1.2, "P-GPS"),
        (45, 0, 0, 60, "P-GPS"),
        (70, 60, 40, 60, "P-GPS"),
        (95, 120, 30, 62, "P-GPS"),
        (130, 230, 120, 62, "P-GPS"),
        (150, 250, 180, 62, "P-GPS"),
        (160, 250, 180, 80, "Waypoint"),
    ]
    legs, t_end = lawnmower_keys(160, 250, 180, 80, 360, 30, 7, 7.0, 6.0)
    keys += legs
    t = t_end
    last = keys[-1]
    keys.append((t + 8, last[1], last[2], 80, "P-GPS"))
    # orbit point of interest
    t0 = t + 50
    keys.append((t0, 400 + 0, 50 + 60, 50, "P-GPS"))
    orb = orbit_keys(t0, 400, 50, 60, 50, 0, 360, 110)
    keys += orb
    t = orb[-1][0]
    keys += [
        (t + 45, -150, 300, 45, "P-GPS"),
        (t + 85, -150, 300, 45, "P-GPS"),         # hover, observing
    ]
    t_rth = t + 92
    keys += [
        (t_rth, -150, 300, 45, "GoHome"),
        (t_rth + 8, -150, 300, 60, "GoHome"),
        (t_rth + 44, 0, 0, 60, "GoHome", {}),
        (t_rth + 48, 0, 0, 60, "AutoLanding"),
        (t_rth + 66, 0, 0, 1.0, "AutoLanding"),
        (t_rth + 70, 0, 0, 0, "P-GPS"),
        (t_rth + 82, 0, 0, 0, "P-GPS"),
    ]
    keys = [k if len(k) > 5 else (*k, {}) for k in keys]
    keys.sort(key=lambda k: k[0])
    t_land = t_rth + 69
    total = t_rth + 82
    return {
        "path": Path3D(keys),
        "home": (42.48364, -83.38810, 268.0),
        "cells": 4,
        "battery": [(0, 97), (20, 97), (t_rth - 15, 30.5), (t_land, 24), (total, 24)],
        "phases": {"motor_on": (8, t_land + 4), "flying": (21, t_land)},
        "initial_yaw": 34.0,
        "duration": total,
        "times": {"rth": t_rth, "land": t_land, "mission_start": 160, "mission_end": t_end,
                  "low_batt": t_rth - 15},
    }


def _spec_mavic2() -> dict:
    keys = [
        (0, 0, 0, 0, "P-GPS"),
        (14, 0, 0, 0, "P-GPS"),
        (18, 0, 0, 1.2, "AutoTakeoff"),
        (21, 0, 0, 1.2, "P-GPS"),
        (52, 0, 0, 120, "P-GPS"),
        (180, -720, 760, 120, "P-GPS"),
    ]
    orb = orbit_keys(190, -780, 860, 90, 120, 200, 300, 160)
    keys.append((190, orb[0][1], orb[0][2], 120, "P-GPS"))
    keys += orb
    # 350..430 further out
    keys += [
        (380, -900, 980, 118, "P-GPS"),
        (430, -940, 1010, 118, "P-GPS"),
        (432, -940, 1010, 118, "GoHome"),          # smart RTH (low battery)
        (441, -905, 975, 121, "GoHome"),
        (443, -905, 975, 121, "P-GPS"),            # cancelled by operator
        (500, -990, 1070, 115, "P-GPS"),
        (560, -985, 1060, 115, "P-GPS"),
        (562, -985, 1060, 115, "AutoLanding"),     # critical battery: forced landing
        (604, -905, 985, 0.0, "AutoLanding"),
        (608, -905, 985, 0.0, "P-GPS"),
        (622, -905, 985, 0.0, "P-GPS"),
    ]
    keys = [k if len(k) > 5 else (*k, {}) for k in keys]
    keys.sort(key=lambda k: k[0])
    return {
        "path": Path3D(keys),
        "home": (42.48061, -83.47552, 281.0),
        "cells": 4,
        "battery": [(0, 88), (14, 88), (430, 25), (560, 10), (604, 4), (622, 4)],
        "phases": {"motor_on": (8, 606), "flying": (15, 604)},
        "weak_cell": 2,
        "weak_cell_below": 28,
        "pilot_steer_landing": True,
        "initial_yaw": -40.0,
        "duration": 622,
        "times": {"rth": 432, "rth_cancel": 443, "critical": 562, "land": 604, "low_batt": 430},
    }


def _spec_inspire() -> dict:
    keys = [
        (0, 0, 0, 0, "P-GPS"),
        (10, 0, 0, 0, "P-GPS"),
        (25, 0, 0, 35, "P-GPS"),
        (70, 180, -60, 40, "P-GPS"),
        (110, 300, 20, 42, "Sport"),
        (150, 160, 140, 40, "P-GPS"),
        (200, 30, 60, 25, "P-GPS"),
        (240, 0, 0, 8, "P-GPS"),
        (262, 0, 0, 0, "P-GPS"),
        (272, 0, 0, 0, "P-GPS"),
    ]
    keys = [(*k, {}) for k in keys]
    return {
        "path": Path3D(keys),
        "home": (42.33887, -82.98610, 176.0),
        "cells": 6,
        "battery": [(0, 100), (8, 100), (262, 71), (272, 71)],
        "phases": {"motor_on": (6, 264), "flying": (11, 262)},
        "initial_yaw": 110.0,
        "duration": 272,
        "times": {"land": 262},
    }


# --------------------------------------------------------------------- dataset -----

P4_START = datetime(2026, 8, 14, 18, 2, 11, 400000, tzinfo=timezone.utc)
M2_START = datetime(2026, 9, 3, 23, 41, 22, 0, tzinfo=timezone.utc)
I2_START = datetime(2026, 7, 19, 21, 15, 3, 200000, tzinfo=timezone.utc)
MINI3_START = datetime(2026, 10, 2, 14, 12, 9, tzinfo=timezone.utc)
TXT_LEAD_S = 25.6   # phone log starts before the aircraft DAT
TXT_TAIL_S = 11.0


def _evt(t_s, kind, severity, message, raw=None):
    return {"t_offset_ms": int(round(t_s * 1000)), "kind": kind, "severity": severity,
            "message": message, "raw": raw}


class Dataset:
    """Builds and caches all mock rows; frames are generated lazily per flight."""

    def __init__(self):
        self.specs = {1: _spec_phantom(), 3: _spec_mavic2(), 5: _spec_inspire()}
        self._frames: dict[int, dict] = {}
        self.evidence = self._evidence()
        self.flights = self._flights()
        self.artifacts = self._artifacts()
        self.events = self._events()
        self.custody = self._custody()

    # -- evidence ----------------------------------------------------------------
    def _evidence(self):
        base = r"E:\Evidence\FH-2026-004817"
        rows = [
            (1, "Item 3 - Phantom 4 Pro V2 (DJI Assistant export)", base + r"\Item 3 - P4P V2\DJI Assistant export",
             "folder", None, 2_204_811_264, "referenced", "candidate-manifest"),
            (2, "Item 1 - iPhone 13 Pro full file system", base + r"\Item 1 - iPhone 13 Pro\FFS_2026-08-15.zip",
             "zip", "ffs_zip", 61_337_118_720, "referenced", "file"),
            (3, "Item 2 - Galaxy S21 logical", base + r"\Item 2 - Galaxy S21\Logical_2026-09-05",
             "folder", "android_logical", 9_883_200_512, "referenced", "candidate-manifest"),
            (4, "Item 5 - SanDisk 64GB microSD", base + r"\Item 5 - microSD\Item5_microSD.E01",
             "image", "e01", 3_402_118_404, "referenced", "segments-concat"),
            (5, "Item 7 - Mavic Air 2 (DJI Assistant export)", base + r"\Item 7 - MA2\DJI Assistant export",
             "folder", None, 1_310_720_000, "referenced", "candidate-manifest"),
        ]
        out = []
        for i, (eid, label, src, kind, sub, size, storage, scope) in enumerate(rows):
            out.append({
                "id": eid, "label": label, "src_path": src, "kind": kind, "subkind": sub,
                "size": size, "sha256": fake_hash(src), "sha1": fake_hash(src, "sha1"),
                "md5": fake_hash(src, "md5"), "storage": storage, "hash_scope": scope,
                "added_utc": f"2026-10-19T13:{10 + i * 3:02d}:0{i}Z", "examiner": CASE_META["examiner"],
                "verified": True if kind == "image" else None,
                "artifact_count": 0,
            })
        return out

    def ev(self, eid):
        return next(e for e in self.evidence if e["id"] == eid)

    # -- flights -------------------------------------------------------------------
    def _flights(self):
        p4 = self.specs[1]
        m2 = self.specs[3]
        i2 = self.specs[5]
        p4_details = {
            "aircraft_model": "Phantom 4 Pro V2.0", "aircraft_name": "PH4-HERITAGE",
            "aircraft_sn": "11XDG7R0A20317", "fc_sn": "0AXDG7R0A20317-FC", "camera_sn": "38H1C5A0350912",
            "rc_sn": "11XRG8N0A10044", "battery_sn": "1D4HG5K2A40231", "app_platform": None,
            "app_version": None, "log_version": 3, "start_time_utc": iso(P4_START, ms=False),
            "duration_s": round(p4["duration"], 1), "total_distance_m": None, "max_height_m": None,
            "max_h_speed_ms": None, "max_v_speed_ms": None, "home_lat": p4["home"][0],
            "home_lon": p4["home"][1], "city": None, "area": None, "sub_street": None,
            "firmware_version": "02.00.0700", "record_line_count": None,
        }
        txt_details = dict(p4_details, **{
            "fc_sn": None, "camera_sn": "38H1C5A0350912", "app_platform": "iOS", "app_version": "4.3.56",
            "log_version": 12, "start_time_utc": iso(P4_START - timedelta(seconds=TXT_LEAD_S), ms=False),
            "duration_s": round(p4["duration"] + TXT_LEAD_S + TXT_TAIL_S, 1),
            "city": "Farmington Hills", "area": "Michigan", "sub_street": "Heritage Park",
            "firmware_version": None, "record_line_count": int((p4["duration"] + TXT_LEAD_S + TXT_TAIL_S) * 10),
        })
        m2_details = {
            "aircraft_model": "Mavic 2 Pro", "aircraft_name": "Mavic2", "aircraft_sn": "163DF8R0A40012",
            "fc_sn": None, "camera_sn": "18K2D7A0551209", "rc_sn": "163RG4K0B10971",
            "battery_sn": "0FK3H8N1C20077", "app_platform": "Android", "app_version": "4.3.54",
            "log_version": 12, "start_time_utc": iso(M2_START, ms=False), "duration_s": m2["duration"],
            "total_distance_m": None, "max_height_m": None, "max_h_speed_ms": None, "max_v_speed_ms": None,
            "home_lat": m2["home"][0], "home_lon": m2["home"][1], "city": "Novi", "area": "Michigan",
            "sub_street": "Ten Mile Rd", "firmware_version": None, "record_line_count": m2["duration"] * 10,
        }
        mini_details = {
            "aircraft_model": "DJI Mini 3 Pro", "aircraft_name": "Mini 3 Pro", "aircraft_sn": "1581F4XFC22A1003Y2",
            "fc_sn": None, "camera_sn": None, "rc_sn": "5YSZK7Q0030AFP", "battery_sn": "3ZJPK2B0B9087Q",
            "app_platform": "iOS", "app_version": "1.13.4", "log_version": 13,
            "start_time_utc": iso(MINI3_START, ms=False), "duration_s": 1032.4, "total_distance_m": 3810.2,
            "max_height_m": 119.6, "max_h_speed_ms": 15.8, "max_v_speed_ms": 5.0,
            "home_lat": 42.47702, "home_lon": -83.40195, "city": "Farmington Hills", "area": "Michigan",
            "sub_street": "W 12 Mile Rd", "firmware_version": None, "record_line_count": 10321,
        }
        i2_details = {
            "aircraft_model": "Inspire 2", "aircraft_name": "INSPIRE2-UNIT4", "aircraft_sn": "095XE3500204318",
            "fc_sn": "095XE3500204318-FC", "camera_sn": "X5S-0J6CD1E0400183", "rc_sn": None,
            "battery_sn": "TB50-0B3CE2209711", "app_platform": None, "app_version": None, "log_version": 3,
            "start_time_utc": iso(I2_START, ms=False), "duration_s": i2["duration"], "total_distance_m": None,
            "max_height_m": None, "max_h_speed_ms": None, "max_v_speed_ms": None,
            "home_lat": i2["home"][0], "home_lon": i2["home"][1], "city": None, "area": None,
            "sub_street": None, "firmware_version": "01.03.0100", "record_line_count": None,
        }
        flights = [
            {"id": 1, "artifact_id": 1, "source": "aircraft-dat", "adapter": "dat_v3",
             "adapter_version": "dronedog_core 0.1.0 (mock)", "details": p4_details, "hz": 50.0,
             "spec": 1, "t_from": 0.0, "t_to": p4["duration"], "t0": P4_START, "noise": 0.25, "seed": 11},
            {"id": 2, "artifact_id": 2, "source": "app-txt", "adapter": "txt_djilog",
             "adapter_version": "dronedog_core 0.1.0 + dji-log-parser 0.5.7 (mock)", "details": txt_details,
             "hz": 10.0, "spec": 1, "t_from": -TXT_LEAD_S, "t_to": p4["duration"] + TXT_TAIL_S,
             "t0": P4_START, "noise": 0.9, "seed": 12},
            {"id": 3, "artifact_id": 4, "source": "app-txt", "adapter": "txt_djilog",
             "adapter_version": "dronedog_core 0.1.0 + dji-log-parser 0.5.7 (mock)", "details": m2_details,
             "hz": 10.0, "spec": 3, "t_from": 0.0, "t_to": m2["duration"], "t0": M2_START, "noise": 0.9,
             "seed": 13},
            {"id": 4, "artifact_id": 3, "source": "app-txt", "adapter": "txt_djilog",
             "adapter_version": "dronedog_core 0.1.0 + dji-log-parser 0.5.7 (mock)", "details": mini_details,
             "hz": 0, "spec": None, "encrypted_unreadable": True},
            {"id": 5, "artifact_id": 5, "source": "aircraft-dat", "adapter": "dat_v3",
             "adapter_version": "dronedog_core 0.1.0 (mock)", "details": i2_details, "hz": 50.0,
             "spec": 5, "t_from": 0.0, "t_to": i2["duration"], "t0": I2_START, "noise": 0.25, "seed": 15},
        ]
        for f in flights:
            f.setdefault("encrypted_unreadable", False)
        return flights

    def flight(self, fid):
        for f in self.flights:
            if f["id"] == fid:
                return f
        raise KeyError(f"no flight {fid}")

    def frames(self, fid) -> dict:
        if fid in self._frames:
            return self._frames[fid]
        f = self.flight(fid)
        if f["spec"] is None:
            fr = {}
        else:
            fr = simulate(self.specs[f["spec"]], f["hz"], f["t_from"], f["t_to"], f["seed"], f["noise"],
                          f["t0"])
        self._frames[fid] = fr
        return fr

    def flight_row(self, fid) -> dict:
        f = self.flight(fid)
        d = f["details"]
        fr = self.frames(fid)
        art = next(a for a in self.artifacts if a["id"] == f["artifact_id"]) if hasattr(self, "artifacts") else None
        if fr:
            lat = [v for v in fr["lat"] if v is not None]
            lon = [v for v in fr["lon"] if v is not None]
            dist = 0.0
            for i in range(1, len(lat), max(1, int(f["hz"]))):
                j = max(0, i - max(1, int(f["hz"])))
                dist += _haversine(lat[j], lon[j], lat[i], lon[i])
            max_alt = max(fr["alt_rel_m"])
            max_spd = max(fr["speed_h_ms"])
            bbox = [min(lon), min(lat), max(lon), max(lat)]
            n = len(fr["t_offset_ms"])
            dur = fr["t_offset_ms"][-1] / 1000.0
            start = fr["t_utc"][0]
        else:
            dist = d.get("total_distance_m")
            max_alt = d.get("max_height_m")
            max_spd = d.get("max_h_speed_ms")
            bbox = None
            n = 0
            dur = d.get("duration_s")
            start = d.get("start_time_utc")
        warnings = []
        evs = self.events.get(fid, []) if hasattr(self, "events") else []
        for e in evs:
            if e["severity"] == "critical":
                warnings.append(e["message"])
        if f["encrypted_unreadable"]:
            warnings.append("Records encrypted (TXT v13, no keychain): header only, no track")
        if fid == 2:
            warnings.append("3 records failed checksum and were skipped (0.03%)")
        links = [{"flight_id": l["other"], "kind": l["kind"], "score": l["score"]}
                 for l in self.links_for(fid)]
        return {
            "id": fid, "artifact_id": f["artifact_id"], "artifact_name": art["name"] if art else None,
            "evidence_label": art["evidence_label"] if art else None,
            "source": f["source"], "adapter": f["adapter"], "adapter_version": f["adapter_version"],
            "start_utc": start, "duration_s": round(dur, 1) if dur is not None else None,
            "distance_m": round(dist, 1) if dist is not None else None,
            "max_alt_m": round(max_alt, 1) if max_alt is not None else None,
            "max_speed_ms": round(max_spd, 1) if max_spd is not None else None,
            "frame_count": n, "aircraft_model": d.get("aircraft_model"), "aircraft_name": d.get("aircraft_name"),
            "aircraft_sn": d.get("aircraft_sn"), "app_platform": d.get("app_platform"),
            "app_version": d.get("app_version"), "log_version": d.get("log_version"),
            "home_lat": d.get("home_lat"), "home_lon": d.get("home_lon"), "bbox": bbox,
            "encrypted_unreadable": f["encrypted_unreadable"], "warnings": warnings, "links": links,
            "hz": f["hz"] or None,
        }

    def links_for(self, fid):
        links = [
            {"a": 1, "b": 2, "kind": "same-flight", "score": 0.97,
             "reasons": ["aircraft_sn 11XDG7R0A20317 in both logs",
                         "time overlap 845.1 s of 845.1 s aircraft log",
                         "home point separation 1.6 m",
                         "track RMS separation 1.1 m over overlap"]},
            {"a": 1, "b": 3, "kind": "rejected", "score": 0.0,
             "reasons": ["different aircraft serials (11XDG7R0A20317 vs 163DF8R0A40012)",
                         "home points 7.4 km apart", "no UTC overlap (20 days apart)"]},
        ]
        out = []
        for l in links:
            if fid in (l["a"], l["b"]):
                out.append({"other": l["b"] if fid == l["a"] else l["a"], "kind": l["kind"],
                            "score": l["score"], "reasons": l["reasons"]})
        return out

    # -- artifacts -------------------------------------------------------------------
    def _artifacts(self):
        ios_go4 = ("private/var/mobile/Containers/Data/Application/5B0E2A7C-91D4-4E8F-A1C3-1E2F3D4C5B6A/"
                   "Library/Caches/FlightRecords/DJIFlightRecord_2026-08-14_[14-01-46].txt")
        ios_go4_old = ("private/var/mobile/Containers/Data/Application/5B0E2A7C-91D4-4E8F-A1C3-1E2F3D4C5B6A/"
                       "Documents/FlightRecords/.trash/DJIFlightRecord_2026-08-14_[14-01-46].txt")
        ios_fly = ("private/var/mobile/Containers/Data/Application/9A1F6E22-3D5B-4C1A-B7E8-77D0C4A9F310/"
                   "Documents/FlightRecords/DJIFlightRecord_2026-10-02_[10-12-09].txt")
        android_go4 = "Android/data/dji.go.v4/files/FlightRecord/DJIFlightRecord_2026-09-03_[19-41-22].txt"
        rows = [
            dict(id=1, name="FLY047.DAT", evidence_id=1, inner_path="FLY047.DAT", offset=None,
                 size=118_423_552, kind="dji_dat", subtype="dat_v3_plain", version=3, encrypted=False,
                 confidence=0.99, status="parsed", error=None, flight_ids=[1], container="assistant",
                 reasons=["DUPC framing: 0x55 sync + CRC16 valid on first 512 records",
                          "DAT header 'BUILD' string: P4P V2 FC firmware 02.00.0700",
                          "filename matches FLY###.DAT"], sources=[{"deleted": False}]),
            dict(id=2, name="DJIFlightRecord_2026-08-14_[14-01-46].txt", evidence_id=2, inner_path=ios_go4,
                 offset=None, size=3_912_448, kind="dji_txt", subtype="txt_v12", version=12, encrypted=False,
                 confidence=0.98, status="parsed", error=None, flight_ids=[2], container="zip",
                 reasons=["TXT header: log version 12 (DJI GO 4)",
                          "details block decodes: Phantom 4 Pro V2.0, SN 11XDG7R0A20317",
                          "records XOR-obfuscated (v7-v12): decodable without key"],
                 sources=[{"deleted": False},
                          {"deleted": True, "inner_path": ios_go4_old, "overwrite_status": "none_detected"}]),
            dict(id=3, name="DJIFlightRecord_2026-10-02_[10-12-09].txt", evidence_id=2, inner_path=ios_fly,
                 offset=None, size=1_506_304, kind="dji_txt", subtype="txt_v13", version=13, encrypted=True,
                 confidence=0.98, status="encrypted",
                 error="TXT v13 records are AES-encrypted; no DJI keychain available (D5). Header read only.",
                 flight_ids=[4], container="zip",
                 reasons=["TXT header: log version 13 (DJI Fly 1.13.4)",
                          "plaintext details block: DJI Mini 3 Pro, SN 1581F4XFC22A1003Y2",
                          "record blocks carry keychain feature-point IDs: encrypted"],
                 sources=[{"deleted": False}]),
            dict(id=4, name="DJIFlightRecord_2026-09-03_[19-41-22].txt", evidence_id=3, inner_path=android_go4,
                 offset=None, size=2_811_904, kind="dji_txt", subtype="txt_v12", version=12, encrypted=False,
                 confidence=0.98, status="parsed", error=None, flight_ids=[3], container="folder",
                 reasons=["TXT header: log version 12 (DJI GO 4 Android 4.3.54)",
                          "details block decodes: Mavic 2 Pro, SN 163DF8R0A40012"],
                 sources=[{"deleted": False}]),
            dict(id=5, name="FLY012.DAT", evidence_id=4, inner_path="/MISC/LOG/FLY012.DAT", offset=0x0A34_C000,
                 size=33_816_576, kind="dji_dat", subtype="dat_v3_plain", version=3, encrypted=False,
                 confidence=0.95, status="parsed", error=None, flight_ids=[5], container="image",
                 reasons=["recovered from deleted FAT32 directory entry (first char 0xE5)",
                          "contiguous clusters; DUPC framing valid through EOF",
                          "DAT header: Inspire 2 FC firmware 01.03.0100"],
                 sources=[{"deleted": True, "overwrite_status": "none_detected", "fs_type": "FAT32",
                           "meta_addr": 1_218_773}]),
            dict(id=6, name="FLY013.DAT", evidence_id=4, inner_path="/MISC/LOG/FLY013.DAT", offset=0x0C81_0000,
                 size=20_971_520, kind="dji_dat", subtype="dat_v3_plain", version=3, encrypted=False,
                 confidence=0.61, status="failed",
                 error="Record framing lost at 0x00002000: clusters reallocated (zero-filled run of 19.9 MiB). "
                       "0 of ~41,000 records decoded.",
                 flight_ids=[], container="image",
                 reasons=["recovered from deleted FAT32 directory entry",
                          "first 8 KiB carry DAT header; remainder overwritten"],
                 sources=[{"deleted": True, "overwrite_status": "partial", "fs_type": "FAT32",
                           "meta_addr": 1_218_805}]),
            dict(id=7, name="CACHE_09.DAT", evidence_id=4, inner_path="/MISC/CACHE/CACHE_09.DAT",
                 offset=0x0E02_8000, size=524_288, kind="unknown", subtype=None, version=None, encrypted=None,
                 confidence=0.0, status="unsupported", error="Not a recognised flight-log format.",
                 flight_ids=[], container="image",
                 reasons=["no DUPC sync pattern in first 64 KiB", "no TXT log header",
                          "name does not match FLY###.DAT / DJIFlightRecord_*.txt"],
                 sources=[{"deleted": False, "fs_type": "FAT32", "meta_addr": 1_219_100}]),
            dict(id=8, name="FLY021.DAT", evidence_id=5, inner_path="FLY021.DAT", offset=None,
                 size=96_468_992, kind="dji_dat", subtype="dat_encrypted", version=None, encrypted=True,
                 confidence=0.93, status="encrypted",
                 error="Encrypted aircraft DAT (Mavic Air 2 family). No published lawful decryptor for this "
                       "platform (D7): attribution only.",
                 flight_ids=[], container="assistant",
                 reasons=["DAT header: Mavic Air 2 (wm231) build string",
                          "record payload entropy 7.99 bits/byte: encrypted",
                          "serial in plaintext header: 1WNBH7Q0011D3K"],
                 sources=[{"deleted": False}]),
        ]
        out = []
        for r in rows:
            ev = self.ev(r["evidence_id"])
            ev["artifact_count"] += len(r["sources"])
            srcs = []
            for s in r["sources"]:
                srcs.append({
                    "evidence_id": r["evidence_id"], "evidence_label": ev["label"],
                    "inner_path": s.get("inner_path", r["inner_path"]), "container": r["container"],
                    "offset": r["offset"] if s is r["sources"][0] else None, "fs_type": s.get("fs_type"),
                    "meta_addr": s.get("meta_addr"), "deleted": s["deleted"],
                    "overwrite_status": s.get("overwrite_status", "allocated"),
                    "mtime_utc": "2026-08-14T18:17:02Z" if r["id"] in (1, 2) else None,
                })
            sha = fake_hash("artifact:" + r["name"] + str(r["id"]))
            out.append({
                "id": r["id"], "name": r["name"], "evidence_id": r["evidence_id"], "evidence_label": ev["label"],
                "rel_path": f"extracted/{sha[:12]}_{r['name']}", "inner_path": r["inner_path"],
                "offset": r["offset"], "size": r["size"], "sha256": sha,
                "sha1": fake_hash("artifact:" + r["name"] + str(r["id"]), "sha1"),
                "detected_kind": r["kind"], "detected_subtype": r["subtype"], "version": r["version"],
                "confidence": r["confidence"], "encrypted": r["encrypted"], "reasons": r["reasons"],
                "status": r["status"], "error": r["error"],
                "deleted": all(s["deleted"] for s in srcs),
                "deleted_sources": sum(1 for s in srcs if s["deleted"]),
                "sources": srcs, "flight_ids": r["flight_ids"],
            })
        return out

    # -- events ----------------------------------------------------------------------
    def _events(self):
        p4 = self.specs[1]["times"]
        m2 = self.specs[3]["times"]
        p4_dat = [
            _evt(8, "motor_start", "info", "Motors started"),
            _evt(21, "takeoff", "info", "Takeoff (auto takeoff to 1.2 m)"),
            _evt(27, "mode_change", "info", "Flight mode AutoTakeoff -> P-GPS"),
            _evt(160, "mission_start", "info", "Waypoint mission started (7 legs, 80 m AGL, 7 m/s)"),
            _evt(160, "mode_change", "info", "Flight mode P-GPS -> Waypoint"),
            _evt(p4["mission_end"], "mission_end", "info", "Waypoint mission completed"),
            _evt(p4["mission_end"] + 1, "mode_change", "info", "Flight mode Waypoint -> P-GPS"),
            _evt(p4["mission_end"] + 61, "signal", "warn", "Downlink signal weak (RC RSSI -86 dBm)",
                 "OSD: rc_state=0x02"),
            _evt(p4["low_batt"], "battery_low", "warn", "Low battery warning: 30% (threshold 30%)",
                 "battery: goHomeStatus=1"),
            _evt(p4["rth"], "rth", "warn", "Return-to-home initiated by RC button"),
            _evt(p4["rth"], "mode_change", "info", "Flight mode P-GPS -> GoHome"),
            _evt(p4["rth"] + 8, "rth", "info", "RTH climb complete: 60 m RTH altitude"),
            _evt(p4["rth"] + 48, "mode_change", "info", "Flight mode GoHome -> AutoLanding"),
            _evt(p4["land"], "landing", "info", "Landed (home point 1.2 m)"),
            _evt(p4["land"] + 4, "motor_stop", "info", "Motors stopped"),
        ]
        txt = [dict(e, t_offset_ms=e["t_offset_ms"] + int(TXT_LEAD_S * 1000)) for e in p4_dat
               if e["kind"] not in ("signal",)]
        txt += [
            _evt(2, "app", "info", "App connected to aircraft (DJI GO 4 4.3.56, iOS 17.6.1)"),
            _evt(TXT_LEAD_S + p4["mission_end"] + 59, "app_warning", "warn",
                 "App tip: Strong wireless interference. Fly with caution.", "tip: 0x2A"),
            _evt(TXT_LEAD_S + p4["low_batt"] + 1, "app_warning", "warn",
                 "App tip: Low battery. Return to home promptly.", "tip: 0x13"),
        ]
        txt.sort(key=lambda e: e["t_offset_ms"])
        m2e = [
            _evt(8, "motor_start", "info", "Motors started"),
            _evt(15, "takeoff", "info", "Takeoff"),
            _evt(52, "altitude_limit", "warn", "Altitude limit reached: 120 m"),
            _evt(m2["low_batt"], "battery_low", "warn",
                 "Smart RTH: battery 25% is only enough to return home"),
            _evt(m2["rth"], "rth", "warn", "Return-to-home initiated (Smart RTH, low battery)"),
            _evt(m2["rth_cancel"], "rth_cancel", "critical", "Smart RTH cancelled by operator"),
            _evt(500, "battery_cell", "warn", "Battery cell 3 deviation 0.21 V (cell imbalance)"),
            _evt(m2["critical"], "battery_critical", "critical",
                 "Critical low battery 10%: aircraft is landing (forced landing)"),
            _evt(m2["critical"], "mode_change", "info", "Flight mode P-GPS -> AutoLanding"),
            _evt(m2["land"], "landing", "critical",
                 "Forced landing completed 1.33 km from home point"),
            _evt(606, "motor_stop", "info", "Motors stopped"),
        ]
        i2e = [
            _evt(6, "motor_start", "info", "Motors started"),
            _evt(11, "takeoff", "info", "Takeoff"),
            _evt(70, "mode_change", "info", "Flight mode P-GPS -> Sport"),
            _evt(110, "mode_change", "info", "Flight mode Sport -> P-GPS"),
            _evt(262, "landing", "info", "Landed"),
            _evt(264, "motor_stop", "info", "Motors stopped"),
        ]
        out = {1: p4_dat, 2: txt, 3: m2e, 4: [], 5: i2e}
        n = 1
        for fid in sorted(out):
            for e in out[fid]:
                e["id"] = n
                n += 1
        return out

    # -- custody ---------------------------------------------------------------------
    def _custody(self):
        chain = CustodyChain(datetime(2026, 10, 19, 13, 2, 44, tzinfo=timezone.utc))
        chain.add("case.create", "case", outputs=[{"path": "case.json", "sha256": fake_hash("case.json")}],
                  params={k: CASE_META[k] for k in ("name", "case_number", "examiner", "agency")},
                  actor="examiner", dt=0)
        self.add_import_custody(chain)
        return chain.entries

    def add_import_custody(self, chain: "CustodyChain") -> None:
        add = chain.add
        for ev in self.evidence:
            add("evidence.add", f"ingest.{ev['kind']}", inputs=[{"path": ev["src_path"], "sha256": ev["sha256"]}],
                params={"storage": ev["storage"], "hash_scope": ev["hash_scope"], "size": ev["size"]},
                actor="examiner", dt=170)
            if ev["kind"] == "image":
                add("evidence.verify", "ingest.image", inputs=[{"path": ev["src_path"], "sha256": ev["sha256"]}],
                    params={"ewf_md5_stored": fake_hash(ev["src_path"], "md5"),
                            "ewf_md5_computed": fake_hash(ev["src_path"], "md5"), "match": True}, dt=95)
        for a in self.artifacts:
            ev = self.ev(a["evidence_id"])
            add("artifact.extract", f"ingest.{ev['kind']}",
                inputs=[{"path": ev["src_path"], "sha256": ev["sha256"], "inner_path": a["inner_path"]}],
                outputs=[{"path": a["rel_path"], "sha256": a["sha256"]}],
                params={"offset": a["offset"], "deleted": a["deleted"]}, dt=2)
            add("artifact.detect", "detect", inputs=[{"path": a["rel_path"], "sha256": a["sha256"]}],
                params={"kind": a["detected_kind"], "subtype": a["detected_subtype"],
                        "confidence": a["confidence"], "reasons": a["reasons"]}, dt=0.2)
            if a["status"] in ("parsed", "encrypted", "failed"):
                add("artifact.parse", "adapters", inputs=[{"path": a["rel_path"], "sha256": a["sha256"]}],
                    outputs=[{"path": f"case.db#flights/{fid}", "sha256": fake_hash(f"result{fid}")}
                             for fid in a["flight_ids"]],
                    params={"status": a["status"], "error": a["error"]}, dt=3)
        add("analysis.run", "analysis.correlate", params={"links": [{"a": 1, "b": 2, "kind": "same-flight",
                                                                     "score": 0.97}]}, dt=4)


class CustodyChain:
    """C6-shaped hash chain (same canonical form as dronedog.case.custody)."""

    def __init__(self, t0: datetime | None = None, entries: list | None = None, scale: float = 1.0):
        self.scale = scale              # multiplies dt (live mock imports run in seconds, not minutes)
        self.entries = list(entries or [])
        self.t = t0 or datetime.now(timezone.utc)
        self.prev = (hashlib.sha256(_canonical(self.entries[-1]).encode()).hexdigest()
                     if self.entries else "0" * 64)

    def add(self, action, component, inputs=(), outputs=(), params=None, actor="system", dt=1.0,
            now: bool = False):
        self.t = datetime.now(timezone.utc) if now else self.t + timedelta(seconds=dt * self.scale)
        e = {"seq": len(self.entries) + 1, "ts_utc": iso(self.t), "actor": actor, "action": action,
             "inputs": list(inputs), "outputs": list(outputs), "tool": "dronedog",
             "tool_version": "0.1.0-mock", "component": component, "params": params or {},
             "prev_hash": self.prev}
        e["hash"] = hashlib.sha256(_canonical(e).encode()).hexdigest()
        self.prev = hashlib.sha256(_canonical(e).encode()).hexdigest()
        self.entries.append(e)
        return e


def verify_entries(entries: list) -> dict:
    """Same checks as dronedog.case.custody.verify_chain, on in-memory entries."""
    prev = "0" * 64
    for n, e in enumerate(entries, 1):
        body = {k: v for k, v in e.items() if k != "hash"}
        if e.get("seq") != n:
            return {"ok": False, "entries": n - 1, "first_bad_line": n, "first_bad_seq": e.get("seq"),
                    "reason": f"seq {e.get('seq')} != expected {n}", "head_seq": None, "head_hash": None}
        if e.get("prev_hash") != prev:
            return {"ok": False, "entries": n - 1, "first_bad_line": n, "first_bad_seq": n,
                    "reason": "prev_hash does not match previous line", "head_seq": None, "head_hash": None}
        if e.get("hash") != hashlib.sha256(_canonical(body).encode()).hexdigest():
            return {"ok": False, "entries": n - 1, "first_bad_line": n, "first_bad_seq": n,
                    "reason": "entry hash mismatch (this line edited)", "head_seq": None, "head_hash": None}
        prev = hashlib.sha256(_canonical(e).encode()).hexdigest()
    return {"ok": True, "entries": len(entries), "first_bad_line": None, "first_bad_seq": None,
            "reason": None, "head_seq": len(entries) or None,
            "head_hash": entries[-1]["hash"] if entries else None}


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical(obj) -> str:
    return _canonical(obj)


def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(a))
