"""Cross-source correlation: link records of the same physical flight.

An aircraft DAT, the controller DAT and the phone/app TXT of one flight are separate
artifacts, often from separate devices. Two flights are linked as "same-flight" when the
evidence for it is explainable and checkable:

  * their UTC time windows overlap,
  * and at least one physical cross-check agrees: aircraft serial numbers match, home
    points are within HOME_M, or the two tracks sampled at the same UTC instants have a
    median separation under TRACK_M.

Every link carries the measurements that produced it (reasons), so an examiner can
state exactly why two records were tied together. Nothing here alters frames.
"""
from __future__ import annotations

import bisect
import json
import math
import re
from datetime import datetime, timezone

HOME_M = 30.0          # home points this close -> same takeoff site
TRACK_M = 25.0         # median separation of simultaneous positions -> same aircraft
MIN_OVERLAP_S = 10.0
SAMPLES = 60


def _utc(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _hav(lat1, lon1, lat2, lon2) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _track(store, flight_id: int, t0: float):
    """[(utc_seconds, lat, lon)] sorted by time, positions with a fix only."""
    rows = store.conn().execute(
        "SELECT t_offset_ms, t_utc, lat, lon FROM frames WHERE flight_id=? AND lat IS NOT NULL "
        "AND lon IS NOT NULL ORDER BY idx", (flight_id,)).fetchall()
    out = []
    for off, tu, lat, lon in rows:
        t = _utc(tu) if tu else (t0 + off / 1000.0 if off is not None else None)
        if t is not None:
            out.append((t, lat, lon))
    out.sort()
    return out


def _at(track, t):
    i = bisect.bisect_left(track, (t, -1e9, -1e9))
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(track) and (best is None or abs(track[j][0] - t) < abs(best[0] - t)):
            best = track[j]
    return best if best is not None and abs(best[0] - t) <= 2.0 else None


# -- DJI GO 4 app tips logs (local time only) -------------------------------------------------
# A tips log carries device-local wall-clock time and the aircraft serial (filename) but no
# UTC offset. A DJI app TXT log written by the same app session has the same local stamp in
# its filename (DJIFlightRecord_YYYY-MM-DD_[HH-MM-SS].txt) and a UTC start in its details,
# which gives the device's UTC offset. Rules (each link states exactly which applied):
#   R1  tips filename local time == TXT filename local time (to the second), serials not
#       contradicting -> link; offset = TXT start_utc - TXT local stamp (rounded to 15 min).
#   R2  with an offset known from R1 (or from same-serial TXT logs that all agree), the tips
#       window shifted to UTC overlaps another flight's UTC window and the aircraft serials
#       match -> link.
# When an offset is inferred, the tips flight's start_utc is back-filled and the inference
# is recorded in details.x and in every link's reasons. Frames/events are not touched.
TXT_LOCAL_RE = re.compile(r"DJIFlightRecord_(\d{4}-\d{2}-\d{2})_\[(\d{2})-(\d{2})-(\d{2})\]", re.I)
OFFSET_STEP_S = 900          # real UTC offsets are multiples of 15 min
OFFSET_RESID_S = 5.0         # TXT UTC start vs its local filename stamp must agree this well


def _txt_local(name: str) -> datetime | None:
    m = TXT_LOCAL_RE.search(name or "")
    if not m:
        return None
    return datetime.strptime("%s %s:%s:%s" % m.groups(), "%Y-%m-%d %H:%M:%S")


def _fmt_off(sec: int) -> str:
    sign = "-" if sec < 0 else "+"
    sec = abs(sec)
    return "%s%02d:%02d" % (sign, sec // 3600, (sec % 3600) // 60)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _link_app_tips(store) -> tuple[list[dict], set[int], list[dict]]:
    """Returns (links, tips_flight_ids, backfills)."""
    rows = store.query("SELECT f.id, f.source, f.start_utc, f.duration_s, f.details_json, a.name "
                       "FROM flights f JOIN artifacts a ON a.id = f.artifact_id")
    tips, others = [], []
    for r in rows:
        d = json.loads(r["details_json"] or "{}")
        x = d.get("x") or {}
        rec = {"id": r["id"], "source": r["source"], "name": r["name"], "d": d,
               "sn": d.get("aircraft_sn"), "t0": _utc(r["start_utc"]),
               "dur": float(r["duration_s"] or 0.0)}
        if x.get("kind") == "app_tips":
            try:
                rec["local"] = datetime.fromisoformat(d["start_time_local"])
            except (KeyError, TypeError, ValueError):
                rec["local"] = None
            tips.append(rec)
        else:
            others.append(rec)
    # device UTC offsets from TXT logs whose filename carries the local start
    for o in others:
        o["local"], o["offset"], o["resid"] = None, None, None
        loc = _txt_local(o["name"]) if o["source"] == "app-txt" else None
        if loc is None or o["t0"] is None:
            continue
        raw = o["t0"] - loc.replace(tzinfo=timezone.utc).timestamp()
        off = int(round(raw / OFFSET_STEP_S) * OFFSET_STEP_S)
        if abs(raw - off) <= OFFSET_RESID_S:
            o["local"], o["offset"], o["resid"] = loc, off, raw - off
    links, backfills = [], []
    for t in tips:
        if t["local"] is None:
            continue
        basis, off = None, None
        pairs: dict[int, list[str]] = {}
        for o in others:
            if o["local"] is None or o["local"] != t["local"]:
                continue
            if t["sn"] and o["sn"] and t["sn"] != o["sn"]:
                continue   # same second, different aircraft: not the same session
            rs = ["R1: tips filename local time %s equals the local start in TXT filename %s"
                  % (t["local"].isoformat(sep=" "), o["name"]),
                  "TXT start_time_utc %s minus that local stamp = device UTC offset %s "
                  "(rounded to 15 min; residual %.3f s)" % (_iso(o["t0"]), _fmt_off(-o["offset"]),
                                                             o["resid"])]
            if t["sn"] and o["sn"]:
                rs.append("aircraft serial matches (%s; tips serial taken from its filename)"
                          % t["sn"])
            pairs[o["id"]] = rs
            if off is None:
                off, basis = o["offset"], "R1 match with %s (flight %d)" % (o["name"], o["id"])
        if off is None and t["sn"]:
            cands = [o for o in others if o["offset"] is not None and o["sn"] == t["sn"]]
            if cands and len({o["offset"] for o in cands}) == 1:
                off = cands[0]["offset"]
                basis = "offset of same-serial TXT log(s) %s" % ", ".join(o["name"] for o in cands[:3])
        if off is None:
            continue
        # local wall-clock read as if UTC, then shifted by the inferred device offset
        t_utc0 = t["local"].replace(tzinfo=timezone.utc).timestamp() + off
        t_utc1 = t_utc0 + t["dur"]
        backfills.append({"id": t["id"], "start_utc": _iso(t_utc0), "offset": off, "basis": basis})
        for o in others:
            if o["id"] in pairs or o["t0"] is None or not (t["sn"] and o["sn"] == t["sn"]):
                continue
            ov = min(t_utc1, o["t0"] + o["dur"]) - max(t_utc0, o["t0"])
            if ov <= 0:
                continue
            pairs[o["id"]] = [
                "R2: aircraft serial matches (%s; tips serial taken from its filename)" % t["sn"],
                "tips window %s .. %s (local time + device UTC offset %s from %s) overlaps this "
                "record by %.0f s" % (_iso(t_utc0), _iso(t_utc1), _fmt_off(-off), basis, ov)]
        for oid, rs in pairs.items():
            r1 = rs[0].startswith("R1")
            score = 0.95 if r1 and any("serial matches" in x for x in rs) else (0.9 if r1 else 0.75)
            links.append({"flight_a": t["id"], "flight_b": oid, "kind": "app-tips", "score": score,
                          "reasons": rs + ["tips flight start_utc back-filled to %s (%s)"
                                           % (_iso(t_utc0), basis)]})
    return links, {t["id"] for t in tips}, backfills


def correlate(case) -> list[dict]:
    """Recompute all same-flight links for the case. Returns the links written."""
    store = case.store
    tips_links, tips_ids, backfills = _link_app_tips(store)
    if backfills:
        with store.tx() as cx:
            for b in backfills:
                d = json.loads(cx.execute("SELECT details_json FROM flights WHERE id=?", (b["id"],))
                               .fetchone()[0] or "{}")
                d["start_time_utc"] = b["start_utc"]
                x = d.setdefault("x", {})
                x["utc_offset_s"] = -b["offset"]      # device offset (local - UTC)
                x["utc_offset_basis"] = "inferred by correlation: " + b["basis"]
                x["timezone"] = "device local; UTC offset %s inferred by correlation" % _fmt_off(-b["offset"])
                cx.execute("UPDATE flights SET start_utc=?, details_json=? WHERE id=?",
                           (b["start_utc"], json.dumps(d, default=str), b["id"]))
    fl = []
    for f in store.query("SELECT id, artifact_id, source, start_utc, duration_s, details_json FROM flights"):
        if f["id"] in tips_ids:
            continue      # app tips logs are linked only by the explicit rules above
        t0 = _utc(f["start_utc"])
        if t0 is None or not f["duration_s"]:
            continue
        d = json.loads(f["details_json"] or "{}")
        fl.append({"id": f["id"], "artifact_id": f["artifact_id"], "source": f["source"], "t0": t0,
                   "t1": t0 + float(f["duration_s"]), "sn": d.get("aircraft_sn"),
                   "home": (d.get("home_lat"), d.get("home_lon"))})
    tracks: dict[int, list] = {}
    links = []
    for i, a in enumerate(fl):
        for b in fl[i + 1:]:
            ov = min(a["t1"], b["t1"]) - max(a["t0"], b["t0"])
            if ov < MIN_OVERLAP_S:
                continue
            shorter = min(a["t1"] - a["t0"], b["t1"] - b["t0"]) or 1.0
            reasons = [f"UTC windows overlap {ov:.0f} s ({100 * ov / shorter:.0f}% of the shorter record)"]
            checks = 0
            if a["sn"] and b["sn"]:
                if a["sn"] == b["sn"]:
                    reasons.append(f"aircraft serial matches ({a['sn']})")
                    checks += 2
                else:
                    reasons.append(f"aircraft serials differ ({a['sn']} vs {b['sn']})")
                    checks -= 3
            ha, hb = a["home"], b["home"]
            if None not in ha and None not in hb:
                dh = _hav(ha[0], ha[1], hb[0], hb[1])
                reasons.append(f"home points {dh:.1f} m apart")
                checks += 1 if dh <= HOME_M else -1
            for x in (a, b):
                if x["id"] not in tracks:
                    tracks[x["id"]] = _track(store, x["id"], x["t0"])
            ta, tb = tracks[a["id"]], tracks[b["id"]]
            if ta and tb:
                lo, hi = max(ta[0][0], tb[0][0]), min(ta[-1][0], tb[-1][0])
                seps = []
                if hi > lo:
                    for k in range(SAMPLES):
                        t = lo + (hi - lo) * k / (SAMPLES - 1)
                        pa, pb = _at(ta, t), _at(tb, t)
                        if pa and pb:
                            seps.append(_hav(pa[1], pa[2], pb[1], pb[2]))
                if len(seps) >= 5:
                    seps.sort()
                    med = seps[len(seps) // 2]
                    reasons.append(f"median separation of simultaneous positions {med:.1f} m "
                                   f"over {len(seps)} samples (max {seps[-1]:.1f} m)")
                    checks += 2 if med <= TRACK_M else -2
            if checks <= 0:
                continue
            score = round(min(1.0, 0.4 + 0.15 * checks + 0.2 * min(1.0, ov / shorter)), 2)
            links.append({"flight_a": a["id"], "flight_b": b["id"], "kind": "same-flight",
                          "score": score, "reasons": reasons})
    links = tips_links + links
    with store.tx() as cx:
        cx.execute("DELETE FROM links")
        cx.executemany("INSERT INTO links(flight_a, flight_b, kind, score, reasons_json) VALUES (?,?,?,?,?)",
                       [(l["flight_a"], l["flight_b"], l["kind"], l["score"], json.dumps(l["reasons"]))
                        for l in links])
    case.log("analysis.run", component="analysis.correlate",
             params={"method": "time-overlap + serial/home/track cross-check",
                     "thresholds": {"home_m": HOME_M, "track_m": TRACK_M, "min_overlap_s": MIN_OVERLAP_S},
                     "flights_considered": len(fl), "links": len(links),
                     "app_tips": {"rules": "R1 tips/TXT filename local-time match; R2 serial match + "
                                           "UTC overlap after the offset inferred from TXT logs",
                                  "links": len(tips_links),
                                  "utc_backfills": [{"flight_id": b["id"], "start_utc": b["start_utc"],
                                                     "offset_s": -b["offset"], "basis": b["basis"]}
                                                    for b in backfills]}})
    return links
