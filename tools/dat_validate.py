#!/usr/bin/env python3
"""DroneDog: diff tools/dat_ref.py against DatCon CSV output (ground truth).

For every row in a DatCon CSV, DatCon's Tick# column is the tick of the
record after which it printed the row (spec 9.1). dat_ref decodes the same
DAT and emits its own state at exactly those ticks, then each mapped field
is compared numerically. Reported per field: rows compared, rows within
tolerance, max absolute error. Reported per file: % of DatCon rows whose
tick exists in dat_ref's accepted record stream ("rows matched").

Usage:
    python tools/dat_validate.py PAIRS.json --out corpus/baselines/dat
    python tools/dat_validate.py --dat FLY134.DAT --csv FLY134.csv
    python tools/dat_validate.py --scan corpus/_work/dataverse --out corpus/baselines/dat

--scan pairs every *.DAT under the folder with a *.csv in the same folder
whose name contains the DAT's FLYnnn stem.

Build-time tool; standard library only.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import math
import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dat_ref as D  # noqa: E402

csv.field_size_limit(1 << 30)

Getter = Callable[[Dict[str, Any], Dict[str, Any]], Any]


def _g(kind: str, field: str, scale: float = 1.0) -> Getter:
    def f(st, c3):
        v = st.get(kind)
        if v is None:
            return None
        x = v.get(field)
        if x is None:
            return None
        return x * scale if scale != 1.0 else x
    return f


def _c3(col: str) -> Getter:
    return lambda st, c3: c3.get(col)


def _raw(kind: str, field: str) -> Getter:
    """recDef field value exactly as stored (DatCon 4 ':D' columns)."""
    def f(st, c3):
        v = st.get(kind)
        if v is None or "raw" not in v:
            return None
        return v["raw"].get(field)
    return f


def _cell(i: int) -> Getter:
    def f(st, c3):
        v = st.get("batt0")
        if v is None or len(v["batt_cells_v"]) <= i:
            return None
        return v["batt_cells_v"][i]
    return f


def _flyc(st, c3):
    v = st.get("osd")
    return None if v is None else D.FLYC_STATE.get(v["flyc_state"], "OTHER")


def _flight_time(st, c3):
    v = st.get("osd")
    return None if v is None else v["motor_startup_time_ds"] * 100


def _dist_hp(st, c3):
    imu, hp = st.get("imu0"), st.get("_home_valid")
    if imu is None or hp is None:
        return None
    return D.haversine_m(imu["lat_rad"], imu["lon_rad"], hp["home_lat_rad"], hp["home_lon_rad"])


# (label, [DatCon column aliases], getter, abs_tol, kind: num|str, c3 column or "")
# Tolerances are "rounding level": float32 values printed by Java's
# Float.toString round-trip exactly, so the expected error is 0 or 1 ulp.
FIELDS: List[Tuple[str, List[str], Getter, float, str, str]] = [
    ("lat (IMU_ATTI0)", ["IMU_ATTI(0):Latitude", "IMU_ATTI(0):Latitude[degrees]"],
     _g("imu0", "lat_deg"), 1e-9, "num", "lat"),
    ("lon (IMU_ATTI0)", ["IMU_ATTI(0):Longitude", "IMU_ATTI(0):Longitude[degrees]"],
     _g("imu0", "lon_deg"), 1e-9, "num", "lon"),
    ("numSats (IMU_ATTI0)", ["IMU_ATTI(0):numSats"], _g("imu0", "num_sats"), 0, "num",
     "gps_sats"),
    ("baro raw", ["IMU_ATTI(0):barometer:Raw", "IMU_ATTI(0):barometer:Raw[meters]",
                  "IMU_ATTI(0):press:D"],
     _g("imu0", "baro_raw_m"), 1e-4, "num", ""),
    ("velN", ["IMU_ATTI(0):velN", "IMU_ATTI(0):velN[meters/Sec]"], _g("imu0", "vel_n"),
     1e-6, "num", "vel_n"),
    ("velE", ["IMU_ATTI(0):velE", "IMU_ATTI(0):velE[meters/Sec]"], _g("imu0", "vel_e"),
     1e-6, "num", "vel_e"),
    ("velD", ["IMU_ATTI(0):velD", "IMU_ATTI(0):velD[meters/Sec]"], _g("imu0", "vel_d"),
     1e-6, "num", "vel_d"),
    ("velH", ["IMU_ATTI(0):velH", "IMU_ATTI(0):velH[meters/Sec]", "IMU_ATTI(0):velH:C"],
     _c3("speed_h_ms"),
     1e-6, "num", "speed_h_ms"),
    ("roll", ["IMU_ATTI(0):roll", "IMU_ATTI(0):roll[degrees]", "IMU_ATTI(0):roll:C"],
     _g("imu0", "roll_deg"),
     1e-6, "num", "roll"),
    ("pitch", ["IMU_ATTI(0):pitch", "IMU_ATTI(0):pitch[degrees]", "IMU_ATTI(0):pitch:C"],
     _g("imu0", "pitch_deg"),
     1e-6, "num", "pitch"),
    ("yaw", ["IMU_ATTI(0):yaw", "IMU_ATTI(0):yaw[degrees]", "IMU_ATTI(0):yaw:C"],
     _g("imu0", "yaw_deg"), 1e-6,
     "num", "yaw"),
    ("accel X", ["IMU_ATTI(0):accel:X", "IMU_ATTI(0):accel:X[g]", "IMU_ATTI(0):accelX"],
     _g("imu0", "acc_x_g"),
     1e-7, "num", ""),
    ("gyro Z", ["IMU_ATTI(0):gyro:Z", "IMU_ATTI(0):gyro:Z[degrees/sec]"],
     _g("imu0", "gyro_z_dps"), 1e-5, "num", ""),
    ("mag X", ["IMU_ATTI(0):mag:X", "IMU_ATTI(0):magX"], _g("imu0", "mag_x"), 0, "num", ""),
    ("IMU temp", ["IMU_ATTI(0):temperature", "IMU_ATTI(0):temperature[degrees C]"],
     _g("imu0", "imu_temp_c"), 1e-9, "num", ""),
    ("distanceHP", ["IMU_ATTI(0):distanceHP", "IMU_ATTI(0):distanceHP[meters]",
                    "IMU_ATTI(0):distanceHP:C"],
     _dist_hp, 1e-6, "num", "distance_m"),
    ("relativeHeight (OSD)", ["General:relativeHeight", "General:relativeHeight[meters]",
                              "osd_data:relativeHeight"],
     _g("osd", "height_m"), 1e-6, "num", "alt_rel_m"),
    ("vpsHeight (OSD)", ["General:vpsHeight", "General:vpsHeight[meters]",
                         "osd_data:vpsHeight"],
     _g("osd", "vps_height_m"), 1e-6, "num", ""),
    ("flightTime (OSD)", ["flightTime", "flightTime[msec]", "osd_data:flightTime"],
     _flight_time, 0, "num", ""),
    ("navHealth/gpsHealth", ["navHealth", "gpsHealth", "osd_data:navHealth"],
     _g("osd", "gps_level"), 0, "num",
     "gps_health"),
    ("flyCState", ["flyCState", "osd_data:flyCState"], _flyc, 0, "str", "flight_mode"),
    ("GPS lat", ["GPS:Lat", "GPS(0):Lat", "GPS(0):Lat[degrees]"], _g("gps0", "gps_lat_deg"), 1e-9,
     "num", ""),
    ("GPS lon", ["GPS:Long", "GPS(0):Long", "GPS(0):Long[degrees]"], _g("gps0", "gps_lon_deg"), 1e-9,
     "num", ""),
    ("GPS date", ["GPS:Date", "GPS(0):Date"], _g("gps0", "gps_date"), 0, "num", ""),
    ("GPS time", ["GPS:Time", "GPS(0):Time"], _g("gps0", "gps_time"), 0, "num", ""),
    ("GPS heightMSL", ["GPS:heightMSL", "GPS(0):heightMSL", "GPS(0):heightMSL[meters]"],
     _g("gps0", "gps_hmsl_m"), 1e-4, "num", "alt_msl_m"),
    ("GPS hDOP", ["GPS:hDOP", "GPS(0):hDOP"], _g("gps0", "hdop"), 1e-6, "num", ""),
    ("GPS numGPS", ["GPS:numGPS", "GPS(0):numGPS"], _g("gps0", "gps_used"), 0, "num", ""),
    ("GPS numSV", ["GPS:numSV", "GPS(0):numSV"], _g("gps0", "num_sv"), 0, "num", ""),
    ("GPS velN", ["GPS:velN", "GPS(0):velN", "GPS(0):velN[meters/Sec]"], _g("gps0", "gps_vel_n"), 1e-6,
     "num", ""),
    ("RC aileron", ["RC:Aileron"], _g("rc", "rc_roll"), 0, "num", ""),
    ("ctrl_roll raw (recDef)", ["Controller:ctrl_roll:D"], _raw("rc", "ctrl_roll"), 1e-7, "num", ""),
    ("ctrl_pitch raw (recDef)", ["Controller:ctrl_pitch:D"], _raw("rc", "ctrl_pitch"), 1e-7, "num",
     ""),
    ("ctrl_thr raw (recDef)", ["Controller:ctrl_thr:D"], _raw("rc", "ctrl_thr"), 1e-7, "num", ""),
    ("ctrl_yaw raw (recDef)", ["Controller:ctrl_yaw:D"], _raw("rc", "ctrl_yaw"), 1e-7, "num", ""),
    ("mode_switch raw (recDef)", ["Controller:mode_switch:D"], _raw("rc", "mode_switch"), 0, "num",
     ""),
    ("BatVol mV (recDef)", ["BatteryInfo:BatVol:D"], _raw("binfo", "BatVol"), 0, "num", ""),
    ("BatCurrent mA (recDef)", ["BatteryInfo:BatCurrent:D"], _raw("binfo", "BatCurrent"), 0, "num",
     ""),
    ("CapPercnt (recDef)", ["BatteryInfo:CapPercnt:D"], _raw("binfo", "CapPercnt"), 0, "num", ""),
    ("BatTemp (recDef)", ["BatteryInfo:BatTemp:D"], _raw("binfo", "BatTemp"), 0, "num", ""),
    ("cell1 mV (recDef)", ["bat_dynamic_data:cell1:D"], _raw("bdyn", "cell1"), 0, "num", ""),
    ("cell4 mV (recDef)", ["bat_dynamic_data:cell4:D"], _raw("bdyn", "cell4"), 0, "num", ""),
    ("C3 battery_pct", ["BatteryInfo:CapPercnt:D", "BattInfo:Remaining%"], _c3("battery_pct"), 0,
     "num", "battery_pct"),
    ("C3 battery_v", ["BatteryInfo:BatVol:D"], lambda st, c3: None if c3.get("battery_v") is None
     else round(c3["battery_v"] * 1000.0), 0.5, "num", "battery_v (mV)"),
    ("RC elevator", ["RC:Elevator"], _g("rc", "rc_pitch"), 0, "num", ""),
    ("RC throttle", ["RC:Throttle"], _g("rc", "rc_thr"), 0, "num", ""),
    ("RC rudder", ["RC:Rudder"], _g("rc", "rc_yaw"), 0, "num", ""),
    ("HP lat", ["HP:Latitude", "HP:Latitude[degrees]"], _c3("home_lat"), 1e-9, "num",
     "home_lat"),
    ("HP lon", ["HP:Longitude", "HP:Longitude[degrees]"], _c3("home_lon"), 1e-9, "num",
     "home_lon"),
    ("HP rthHeight", ["HP:rthHeight", "HP:rthHeight[meters]"], _g("home", "go_home_height_m"),
     0, "num", ""),
    ("battery pct (BattInfo)", ["BattInfo:Remaining%", "BattInfo:Remaining%[%]"],
     _g("binfo", "bi_cap_pct"), 0, "num", ""),
    ("battery V (BattInfo)", ["BattInfo:Pack_ve", "BattInfo:Pack_ve[Volts]"],
     _g("binfo", "bi_pack_v"), 1e-5, "num", ""),
    ("battery I (BattInfo)", ["BattInfo:Current", "BattInfo:Current[Amps]"],
     _g("binfo", "bi_i"), 1e-5, "num", ""),
    ("battery temp (BattInfo)", ["BattInfo:Temp", "BattInfo:Temp[degrees C]"],
     _g("binfo", "bi_temp_c"), 1e-5, "num", ""),
    ("battery pct (Battery0)", ["Battery(0):battery%", "Battery(0):battery%[%]"],
     _g("batt0", "batt_pct"), 0, "num", "battery_pct"),
    ("battery V (Battery0)", ["Battery(0):totalVolts", "Battery(0):totalVolts[Volts]"],
     _g("batt0", "batt_v"), 1e-5, "num", "battery_v"),
    ("battery temp (Battery0)", ["Battery(0):Temp", "Battery(0):Temp[degrees C]"],
     _g("batt0", "batt_temp_c"), 1e-5, "num", "battery_temp_c"),
    ("cell 1 V", ["Battery(0):cellVolts1", "Battery(0):cellVolts1[Volts]"], _cell(0), 1e-5,
     "num", ""),
    ("cell 4 V", ["Battery(0):cellVolts4", "Battery(0):cellVolts4[Volts]"], _cell(3), 1e-5,
     "num", ""),
    ("motor RF rpm", ["Motor:Speed:RFront"], _g("esc", "rf_rpm"), 0, "num", ""),
    ("motor RB rpm", ["Motor:Speed:RBack"], _g("esc", "rb_rpm"), 0, "num", ""),
    ("motor RF current", ["Motor:Current:RFront"], _g("esc", "rf_current_a"), 1e-5, "num",
     ""),
]


def _num(s: str) -> Optional[float]:
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        if s.lower() in ("true", "false"):
            return 1.0 if s.lower() == "true" else 0.0
        return None


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_datcon_csv(path: str) -> Tuple[List[str], List[List[str]], Dict[str, str]]:
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        header = next(r)
        rows = [row for row in r if row and row[0].strip()]
    meta = {}
    # DatCon puts attribute|value pairs in the 'Attribute|Value' column and the
    # converter version in the trailing header cells
    if "Attribute|Value" in header:
        i = header.index("Attribute|Value")
        meta["datcon_version"] = "|".join(header[i + 1:]).strip(",|")
        for row in rows:
            if len(row) > i and "|" in row[i]:
                k, _, v = row[i].partition("|")
                meta.setdefault(k.strip(), v.strip())
    return header, rows, meta


def validate_pair(dat_path: str, csv_path: str) -> Dict[str, Any]:
    header, rows, meta = read_datcon_csv(csv_path)
    col = {h: i for i, h in enumerate(header)}
    ti = col.get("Tick#", 0)
    ticks = []
    for row in rows:
        try:
            ticks.append(int(float(row[ti])))
        except (ValueError, IndexError):
            ticks.append(None)
    with open(dat_path, "rb") as f:
        buf = f.read()
    h = D.parse_header(buf, os.path.basename(dat_path))
    tickset = {t for t in ticks if t is not None}
    dd = D.DatDecode(buf, os.path.basename(dat_path), header=h, emit_ticks=tickset).run()
    c3 = dd.frames()
    # rows emitted by dat_ref keyed by tick
    ours: Dict[int, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
    home = None
    for i, t in enumerate(dd.row_ticks):
        st = dict(dd.rows[i])
        hp = st.get("home")
        if hp is not None and hp["home_valid_datcon"]:
            home = hp
        st["_home_valid"] = home
        ours[t] = (st, {c: c3[c][i] for c in c3})
    matched_rows = sum(1 for t in ticks if t is not None and t in ours)
    res_fields = []
    for label, aliases, getter, tol, kind, c3col in FIELDS:
        ci = next((col[a] for a in aliases if a in col), None)
        if ci is None:
            continue
        n = nm = 0
        nboth_empty = n_ours_only = n_dc_only = 0
        maxerr = 0.0
        worst = None
        for t, row in zip(ticks, rows):
            if t is None or t not in ours:
                continue
            st, c3row = ours[t]
            dv = row[ci].strip() if ci < len(row) else ""
            ov = getter(st, c3row)
            if dv == "" and ov is None:
                nboth_empty += 1
                continue
            if dv == "":
                n_ours_only += 1
                continue
            if ov is None:
                n_dc_only += 1
                continue
            n += 1
            if kind == "str":
                if str(ov) == dv:
                    nm += 1
                elif worst is None:
                    worst = (t, dv, ov)
                continue
            x = _num(dv)
            if x is None:
                continue
            e = abs(float(ov) - x)
            if math.isnan(e):
                e = 0.0 if (math.isnan(float(ov)) and math.isnan(x)) else float("inf")
            if e <= tol + 1e-7 * abs(x) * (1 if tol else 0):
                nm += 1
            if e > maxerr:
                maxerr = e
                worst = (t, dv, ov)
        res_fields.append({
            "field": label, "datcon_column": header[ci], "c3_column": c3col,
            "compared": n, "within_tol": nm, "pct": (100.0 * nm / n) if n else None,
            "max_abs_err": maxerr if kind == "num" else None, "tol": tol,
            "datcon_only": n_dc_only, "ours_only": n_ours_only,
            "worst": None if worst is None else {"tick": worst[0], "datcon": worst[1],
                                                 "ours": worst[2]}})
    # parity fixture: ~60 evenly spaced tick-matched rows with every mapped column
    fcols = []
    for label, aliases, getter, tol, kind, c3col in FIELDS:
        ci = next((col[a] for a in aliases if a in col), None)
        if ci is not None:
            fcols.append((header[ci], ci, tol))
    cand = [(t, row) for t, row in zip(ticks, rows) if t is not None and t in ours]
    step = max(1, len(cand) // 60)
    fixture = {"dat_sha256": None, "datcon": meta.get("datcon_version"),
               "columns": {c: tol for c, _, tol in fcols},
               "rows": [{"tick": t, "values": {c: (row[ci].strip() if ci < len(row) else "")
                                                for c, ci, _ in fcols}}
                        for t, row in cand[::step][:60]]}
    # internal consistency: OSD (type 12, 0.1 deg) vs quaternion attitude
    cons = {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "n": 0}
    for t, (st, _) in ours.items():
        o, i = st.get("osd"), st.get("imu0")
        if o is None or i is None or abs(o["_tick"] - i["_tick"]) > dd.header.clock_hz * 0.05:
            continue
        cons["n"] += 1
        for a in ("pitch", "roll"):
            cons[a] = max(cons[a], abs(o[a + "_deg"] - i[a + "_deg"]))
        dy = abs((o["yaw_deg"] - i["yaw_deg"] + 180) % 360 - 180)
        cons["yaw"] = max(cons["yaw"], dy)
    # DatCon offsetTime vs our tick arithmetic: infer DatCon timeOffset
    oi = col.get("offsetTime", col.get("Clock:offsetTime"))
    off = None
    if oi is not None:
        for t, row in zip(ticks, rows):
            if t and oi < len(row) and row[oi].strip():
                off = t - float(row[oi]) * dd.header.clock_hz
                break
    return {
        "dat": os.path.relpath(dat_path), "csv": os.path.relpath(csv_path),
        "dat_sha256": sha256_file(dat_path), "csv_sha256": sha256_file(csv_path),
        "dat_bytes": len(buf), "model": h.model, "product_code": h.product_code,
        "kind": h.kind, "build": h.build, "header_serial": h.serial_obf,
        "datcon": meta.get("datcon_version"), "datcon_actype": meta.get("ACType"),
        "datcon_rows": len(rows), "rows_tick_matched": matched_rows,
        "rows_matched_pct": 100.0 * matched_rows / len(rows) if rows else None,
        "records_ok": dd.stats.records_ok, "crc_fail": dd.stats.crc16_fail
        + dd.stats.crc8_fail, "resyncs": dd.stats.resyncs,
        "seq_dropped": dd.seq_dropped, "jump_dropped": dd.jump_dropped,
        "clock_hz": dd.header.clock_hz, "datcon_time_offset_ticks": off,
        "utc_start": dd.details.get("start_time_utc"),
        "utc_anchor_method": dd.anchor.method, "utc_anchor_bound_s": dd.anchor.bound_s,
        "osd_vs_quat_max_deg": cons, "fields": res_fields, "_fixture": fixture,
    }


def find_pairs(root: str) -> List[Tuple[str, str]]:
    pairs = []
    for dp, dn, fn in os.walk(root):
        dats = [f for f in fn if f.lower().endswith(".dat") and re.search(r"FLY\d+", f, re.I)]
        csvs = [f for f in fn if f.lower().endswith(".csv")]
        for d in sorted(dats):
            stem = re.search(r"FLY\d+", d, re.I).group(0).upper()
            cands = [c for c in csvs if stem in c.upper()]
            if cands:
                cands.sort(key=len)
                pairs.append((os.path.join(dp, d), os.path.join(dp, cands[0])))
    return pairs


def summarize(results: List[Dict[str, Any]]) -> str:
    out = []
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out.append("# DAT reference decoder vs DatCon: validation\n")
    out.append("Generated %s by `tools/dat_validate.py` (dat_ref.py %s). Ground truth: "
               "DataverseNO doi:10.18710/AFDYLS (CC0), DJI Phantom 4 Pro flight-controller "
               "DATs with the DatCon CSVs the dataset authors produced.\n" % (
                   now, D.DECODER_VERSION))
    out.append("Method: every DatCon CSV row carries `Tick#`, the tick of the record after "
               "which DatCon printed the row. dat_ref.py decodes the same DAT and snapshots "
               "its decoded state at exactly those ticks; each mapped field is then compared "
               "numerically. A row is *tick-matched* when that tick exists in dat_ref's "
               "accepted record stream. Values count as matching when the absolute error is "
               "within the listed tolerance (0 for integers; float32 rounding level for "
               "floats). Rows where one side is empty are counted separately and are not "
               "errors by themselves (DatCon suppresses values until its own validity "
               "flags are set).\n")
    out.append("## Per file\n")
    out.append("| DAT | model | DatCon | DatCon rows | tick-matched | CRC fails | UTC start "
               "(dat_ref) | DAT sha256 (first 16) |")
    out.append("|---|---|---|---:|---:|---:|---|---|")
    for r in results:
        out.append("| %s | %s (code %s) | %s | %d | %.2f%% | %d | %s | %s |" % (
            os.path.basename(r["dat"]), r["model"], r["product_code"], r["datcon"] or "?",
            r["datcon_rows"], r["rows_matched_pct"] or 0, r["crc_fail"], r["utc_start"],
            r["dat_sha256"][:16]))
    out.append("")
    agg: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in results:
        for f in r["fields"]:
            a = agg.get(f["field"])
            if a is None:
                a = agg[f["field"]] = {"compared": 0, "within": 0, "max": 0.0, "files": 0,
                                       "col": f["datcon_column"], "c3": f["c3_column"],
                                       "tol": f["tol"], "dc_only": 0, "ours_only": 0,
                                       "worst": None}
                order.append(f["field"])
            a["compared"] += f["compared"]
            a["within"] += f["within_tol"]
            a["files"] += 1
            a["dc_only"] += f["datcon_only"]
            a["ours_only"] += f["ours_only"]
            if f["max_abs_err"] is not None and f["max_abs_err"] >= a["max"]:
                if f["max_abs_err"] > a["max"] or a["worst"] is None:
                    a["worst"] = (os.path.basename(r["dat"]), f["worst"])
                a["max"] = f["max_abs_err"]
    out.append("## Per field (all files)\n")
    out.append("| field | DatCon column | C3 column | files | values compared | within tol "
               "| % | max abs err | tol | DatCon-only | ours-only |")
    out.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k in order:
        a = agg[k]
        pct = 100.0 * a["within"] / a["compared"] if a["compared"] else 0.0
        out.append("| %s | `%s` | %s | %d | %d | %d | %.4f | %.3g | %g | %d | %d |" % (
            k, a["col"], a["c3"] or "", a["files"], a["compared"], a["within"], pct, a["max"],
            a["tol"], a["dc_only"], a["ours_only"]))
    out.append("")
    out.append("## Worst residuals\n")
    for k in order:
        a = agg[k]
        if a["worst"] and a["worst"][1] and (a["compared"] != a["within"]):
            out.append("- %s: %s tick %s DatCon=`%s` dat_ref=`%r`" % (
                k, a["worst"][0], a["worst"][1]["tick"], a["worst"][1]["datcon"],
                a["worst"][1]["ours"]))
    out.append("")
    out.append("## Known, explained differences\n")
    out.append("- `GPS hDOP`: DatCon 4.0.5 prints the stored float / 100 on code-35 firmware "
               "(stored 96.0 -> 0.96). dat_ref keeps the stored value; hDOP is not a C3 column.")
    out.append("- `DatCon-only` / `ours-only` count rows where one tool leaves the cell empty "
               "(DatCon suppresses IMU position until its GPS-valid flag is set; DatCon 4 has no "
               "home point / distanceHP for these files). They are not value errors.")
    out.append("- The ~0.01% of DatCon rows that are not tick-matched fall on records dat_ref "
               "excludes by the sequence rule or on the final record of the file.")
    out.append("- t_utc (dat_ref, interpolated) minus DatCon's whole-second `GPS:dateTimeStamp` "
               "on the same rows: -0.07..+1.19 s (FLY134), -0.07..+2.67 s (FLY168; larger "
               "values where the GPS record is stale).\n")
    out.append("## Internal consistency (not DatCon)\n")
    out.append("Max |OSD type-12 attitude (0.1 deg) - quaternion attitude from type 2048| on "
               "rows where both records are within 50 ms:\n")
    for r in results:
        c = r["osd_vs_quat_max_deg"]
        out.append("- %s: pitch %.2f, roll %.2f, yaw %.2f deg (n=%d)" % (
            os.path.basename(r["dat"]), c["pitch"], c["roll"], c["yaw"], c["n"]))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Corpus scan (deliverable: model/era detection + decode success per file)
# --------------------------------------------------------------------------
IMAGE_EXT = (".001", ".e01", ".dd", ".img", ".raw", ".bin", ".aff", ".ex01", ".vmdk")


def _sniff(head: bytes) -> bool:
    return (len(head) >= 21 and head[16:21] == b"BUILD") or head[:4] == b"LOGH" or (
        len(head) >= 2 and head[0] == 0x78 and head[1] == 0x9C)


def _crc8_coverage(buf: bytes, start: int) -> Tuple[int, int]:
    st = D.FrameStats()
    ok = bad = 0
    for r in D.iter_records(buf, start, st, check_hdr_crc=False):
        if D.crc8_dji(buf[r.offset:r.offset + 3]) == buf[r.offset + 3]:
            ok += 1
        else:
            bad += 1
    return ok, bad


def scan_one(name: str, container: str, buf: bytes, out_dir: Optional[str]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"name": name, "container": container, "bytes": len(buf),
                           "sha256": hashlib.sha256(buf).hexdigest()}
    h = D.parse_header(buf, os.path.basename(name))
    row.update({"kind": h.kind, "model": h.model, "product_code": h.product_code,
                "build": h.build, "clock_hz": h.clock_hz, "header_hz": h.header_tick_hz,
                "header_serial": h.serial_obf, "encrypted": h.encrypted})
    inner = []
    if h.kind == "dji_assistant_export":
        ents = D.assistant_entries(buf)
        row["assistant_entries"] = [(e.get("name"), e.get("length")) for e in ents]
        for i, e in enumerate(ents):
            if e.get("data") and _sniff(e["data"][:256]):
                inner.append(scan_one("%s#%d:%s" % (name, i, e.get("name")), container,
                                      e["data"], out_dir))
        row["status"] = "assistant export: %d entries, %d DAT inside" % (len(ents), len(inner))
        row["inner"] = inner
        return row
    if h.kind == "logh_encrypted":
        row["status"] = "encrypted (LOGH) - recognised only"
        return row
    if h.kind not in ("dat_v1", "dat_v3", "dat_headerless"):
        row["status"] = "not a DJI DAT"
        return row
    dd = D.DatDecode(buf, os.path.basename(name), header=h).run()
    res = dd.parse_result()
    s = dd.stats
    bad = s.crc16_fail + s.crc8_fail + s.bad_length
    c8ok, c8bad = _crc8_coverage(buf, h.start_of_records)
    nrows = len(res["frames"]["t_offset_ms"])
    fixrows = sum(1 for v in res["frames"]["lat"] if v is not None)
    key = {t: dd.type_stats.get(t, {}).get("decoded", 0) for t in (12, 13, 2048, 1, 2096, 5,
                                                                     1000, 0, 1710, 17, 10090)}
    row.update({
        "records_ok": s.records_ok, "damaged_starts": bad,
        "damaged_pct": 100.0 * bad / max(1, s.records_ok + bad), "resyncs": s.resyncs,
        "truncated_tail": s.truncated_tail_bytes, "crc8_on_valid": (c8ok, c8bad),
        "types": len(dd.type_stats), "recdef_types": len(dd.recdefs),
        "app_side": dd.is_tablet, "frames": nrows, "frames_with_fix": fixrows,
        "utc_start": dd.details.get("start_time_utc"), "duration_s": dd.details.get(
            "duration_s"), "anchor": dd.anchor.method, "fc_sn": dd.details.get("fc_sn"),
        "battery_sn": dd.details.get("battery_sn"), "key_decoded": key,
        "events": len(res["events"]), "decode_errors": dict(dd.decode_fail),
        "status": "decoded" if nrows and s.records_ok else "framed, no frames"})
    if out_dir:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[-120:]
        os.makedirs(os.path.join(out_dir, "records"), exist_ok=True)
        with open(os.path.join(out_dir, "records", safe + ".txt"), "w", encoding="utf-8") as f:
            f.write("sha256: %s\n" % row["sha256"])
            f.write(D.records_report(dd) + "\n")
    return row


def corpus_scan(root: str, out_dir: Optional[str], skip: List[str]) -> List[Dict[str, Any]]:
    import zipfile
    rows: List[Dict[str, Any]] = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if not any(sk in os.path.join(dp, d) for sk in skip))
        for f in sorted(fn):
            path = os.path.join(dp, f)
            low = f.lower()
            if low.endswith(IMAGE_EXT) or low.endswith(".part"):
                continue
            if low.endswith(".zip"):
                try:
                    z = zipfile.ZipFile(path)
                except (zipfile.BadZipFile, OSError) as e:
                    rows.append({"name": f, "container": os.path.relpath(path),
                                 "status": "zip unreadable (incomplete?): %s" % e})
                    continue
                for info in z.infolist():
                    if info.is_dir() or info.file_size < 300:
                        continue
                    if info.filename.lower().endswith(IMAGE_EXT):
                        continue
                    try:
                        with z.open(info) as zf:
                            head = zf.read(256)
                    except Exception as e:  # noqa: BLE001
                        rows.append({"name": info.filename, "container": os.path.relpath(path),
                                     "status": "zip entry unreadable: %s" % e})
                        continue
                    if not _sniff(head):
                        continue   # not a DJI DAT (e.g. weather-logger .dat)
                    print("  %s :: %s" % (os.path.basename(path), info.filename), file=sys.stderr)
                    rows.append(scan_one(info.filename, os.path.relpath(path), z.read(info),
                                         out_dir))
                continue
            try:
                with open(path, "rb") as fh:
                    head = fh.read(256)
            except OSError:
                continue
            if not _sniff(head):
                if not (low.endswith(".dat") and re.search(r"FLY\d+", f, re.I)):
                    continue
            print("  %s" % os.path.relpath(path), file=sys.stderr)
            with open(path, "rb") as fh:
                rows.append(scan_one(os.path.relpath(path, root), "(loose)", fh.read(), out_dir))
    return rows


def scan_markdown(rows: List[Dict[str, Any]]) -> str:
    flat: List[Dict[str, Any]] = []
    for r in rows:
        flat.append(r)
        flat.extend(r.get("inner", []))
    out = ["# DAT corpus scan\n",
           "Generated %s by `tools/dat_validate.py --corpus-scan` (dat_ref.py %s). One row per "
           "DAT found under `corpus/raw/` (loose, or inside a zip read in place); disk images "
           "are skipped (another workstream extracts them). Damaged %% = damaged record starts "
           "/ (valid + damaged). crc8 = header CRC-8 matches on CRC-16-valid records "
           "(good/bad).\n" % (_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                              D.DECODER_VERSION),
           "| file | container | bytes | kind | model (code) | build | clock Hz | header SN "
           "| app-side | records | damaged % | crc8 | frames (with fix) | UTC start | dur s "
           "| status |",
           "|---|---|---:|---|---|---|---:|---|---|---:|---:|---|---:|---|---:|---|"]
    for r in flat:
        if "kind" not in r:
            out.append("| %s | %s | | | | | | | | | | | | | | %s |" % (
                r["name"], r["container"], r["status"]))
            continue
        out.append("| %s | %s | %d | %s | %s (%s) | %s | %s | %s | %s | %s | %s | %s | %s | %s "
                   "| %s | %s |" % (
                       r["name"], r["container"], r["bytes"], r["kind"], r["model"],
                       r["product_code"], (r.get("build") or "").replace("BUILD ", ""),
                       "%.0f" % r["clock_hz"] if r.get("clock_hz") else "",
                       r.get("header_serial") or "", r.get("app_side", ""),
                       r.get("records_ok", ""),
                       "%.3f" % r["damaged_pct"] if "damaged_pct" in r else "",
                       "%d/%d" % tuple(r["crc8_on_valid"]) if "crc8_on_valid" in r else "",
                       "%s (%s)" % (r["frames"], r["frames_with_fix"]) if "frames" in r else "",
                       r.get("utc_start") or "", r.get("duration_s") or "", r["status"]))
    out.append("")
    out.append("SHA-256 per file:\n")
    for r in flat:
        if "sha256" in r:
            out.append("- `%s` %s" % (r["sha256"], r["name"]))
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pairs", nargs="?", help="JSON list of [dat, csv] pairs")
    ap.add_argument("--dat")
    ap.add_argument("--csv")
    ap.add_argument("--scan", help="folder to scan for DAT/CSV pairs")
    ap.add_argument("--out", help="output folder for VALIDATION.md + validation.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--corpus-scan", help="scan this folder (e.g. corpus/raw) for DATs")
    ap.add_argument("--skip", action="append", default=[],
                    help="path fragment to skip during --corpus-scan")
    a = ap.parse_args(argv)
    if a.corpus_scan:
        rows = corpus_scan(a.corpus_scan, a.out, a.skip)
        md = scan_markdown(rows)
        if a.out:
            os.makedirs(a.out, exist_ok=True)
            with open(os.path.join(a.out, "corpus_scan.md"), "w", encoding="utf-8") as f:
                f.write(md)
            with open(os.path.join(a.out, "corpus_scan.json"), "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=1, default=str)
        else:
            print(md)
        return 0
    pairs: List[Tuple[str, str]] = []
    if a.dat and a.csv:
        pairs.append((a.dat, a.csv))
    if a.pairs:
        with open(a.pairs, encoding="utf-8") as f:
            pairs += [tuple(p) for p in json.load(f)]
    if a.scan:
        pairs += find_pairs(a.scan)
    if a.limit:
        pairs = pairs[:a.limit]
    if not pairs:
        ap.error("no DAT/CSV pairs (or use --corpus-scan)")
    results = []
    for dp, cp in pairs:
        print("validating %s vs %s" % (dp, os.path.basename(cp)), file=sys.stderr)
        r = validate_pair(dp, cp)
        fx = r.pop("_fixture")
        fx["dat_sha256"] = r["dat_sha256"]
        fx["dat_name"] = os.path.basename(dp)
        if a.out:
            os.makedirs(os.path.join(a.out, "fixtures"), exist_ok=True)
            stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.relpath(dp, a.scan or "."))
            with open(os.path.join(a.out, "fixtures", stem + ".json"), "w",
                      encoding="utf-8") as f:
                json.dump(fx, f, indent=0)
        results.append(r)
        worst = [f for f in r["fields"] if f["compared"] and f["within_tol"] != f["compared"]]
        print("  rows %d tick-matched %.2f%%; fields not 100%%: %s" % (
            r["datcon_rows"], r["rows_matched_pct"] or 0,
            ", ".join("%s %.3f%%" % (f["field"], f["pct"]) for f in worst) or "none"),
            file=sys.stderr)
    md = summarize(results)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "VALIDATION.md"), "w", encoding="utf-8") as f:
            f.write(md)
        with open(os.path.join(a.out, "validation.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1, default=str)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
