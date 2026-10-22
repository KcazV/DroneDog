"""Mock C7 backend: every bridge method, backed by synthetic data (``mock_data``).

Run the UI against it with ``python -m dronedog.ui --mock``.  Everything shown is
invented and the UI labels the session "MOCK DATA".  Nothing is written to disk
except imported map packs (shared with the real backend).

``python -m dronedog.ui.mock_api --write-fixture`` writes ``web/dev/mock_fixture.json``,
the canned data that ``web/dev_mock.js`` replays when the web UI is opened in a
normal browser with ``?mock=1`` (screenshots / visual checks without pywebview).
"""

from __future__ import annotations

import copy
import json
import math
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dronedog.ui import jobs as _jobs
from dronedog.ui.api import Api
from dronedog.ui.decimate import decimate
from dronedog.ui import mock_data as md

FORMATS = ("html", "kml", "geojson", "json")
ALIASES = {"pdf": "html"}      # older UIs/scripts ask for pdf: the printable exhibit is HTML


class MockApi(Api):
    _mode = "mock"

    def __init__(self, basemap_dir=None, speed: float = 1.0):
        super().__init__(basemap_dir=basemap_dir)
        self._ds = md.Dataset()
        self._lock = threading.RLock()
        self._speed = speed            # >1 = faster job timelines (tests)
        self._recent_list = copy.deepcopy(md.RECENT)
        self._state = None             # dict when a case is open
        threading.Thread(target=self._prewarm, daemon=True).start()

    def _prewarm(self):
        for fid in (1, 2, 3, 5):
            self._ds.frames(fid)

    # ---------------------------------------------------------------- case ------
    def recent_cases(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._recent_list)

    def _touch_recent(self, path, meta):
        self._recent_list = [r for r in self._recent_list if r["path"].lower() != path.lower()]
        self._recent_list.insert(0, {"path": path, "name": meta["name"], "case_number": meta.get("case_number"),
                                     "opened_utc": md.iso(datetime.now(timezone.utc), ms=False), "exists": True})

    def _empty_state(self, root, meta):
        chain = md.CustodyChain(datetime.now(timezone.utc))
        chain.add("case.create", "case", outputs=[{"path": "case.json", "sha256": md.fake_hash(root + "case.json")}],
                  params={k: meta.get(k) for k in ("name", "case_number", "examiner", "agency")},
                  actor="examiner", dt=0)
        return {"root": root, "meta": meta, "populated": False, "custody": chain.entries}

    def _demo_state(self):
        return {"root": md.CASE_ROOT, "meta": dict(md.CASE_META), "populated": True,
                "custody": copy.deepcopy(self._ds.custody)}

    def case_new(self, meta: dict) -> dict:
        meta = dict(meta or {})
        for req in ("name", "examiner"):
            if not str(meta.get(req) or "").strip():
                raise ValueError(f"'{req}' is required")
        from dronedog.paths import default_cases_dir
        loc = (meta.pop("location", None) or str(default_cases_dir())).rstrip("\\/")
        folder = " ".join(x for x in (meta.get("case_number"), meta.get("name")) if x)
        root = f"{loc}\\{folder}"
        meta.update({"id": md.fake_hash(root)[:8] + "-mock", "created_utc": md.iso(datetime.now(timezone.utc), False),
                     "tool": "DroneDog", "tool_version": "0.1.0-mock"})
        with self._lock:
            self._state = self._empty_state(root, meta)
            self._touch_recent(root, meta)
        self._log_network("case.open")
        return self.case_info()

    def case_open(self, path: str) -> dict:
        with self._lock:
            known = {r["path"].lower(): r for r in self._recent_list}
            r = known.get((path or "").rstrip("\\/").lower())
            if r is None or not r.get("exists", True):
                raise FileNotFoundError(
                    f"Not a DroneDog case folder (case.json not found): {path}. "
                    "Mock mode: open a recent case to load the demo dataset.")
            if path.lower() == md.CASE_ROOT.lower():
                self._state = self._demo_state()
            else:
                meta = {"name": r["name"], "case_number": r["case_number"], "examiner": md.CASE_META["examiner"],
                        "agency": md.CASE_META["agency"], "created_utc": "2026-09-28T15:11:40Z",
                        "id": md.fake_hash(path)[:8] + "-mock", "tool": "DroneDog", "tool_version": "0.1.0-mock"}
                self._state = self._empty_state(r["path"], meta)
            self._touch_recent(self._state["root"], self._state["meta"])
        self._log_network("case.open")
        return self.case_info()

    def case_info(self) -> dict:
        with self._lock:
            st = self._state
            if st is None:
                return {"open": False}
            pop = st["populated"]
            arts = self._ds.artifacts if pop else []
            by_status = {}
            for a in arts:
                by_status[a["status"]] = by_status.get(a["status"], 0) + 1
            counts = {
                "evidence": len(self._ds.evidence) if pop else 0,
                "artifacts": len(arts),
                "artifact_sources": sum(len(a["sources"]) for a in arts),
                "deleted_sources": sum(a["deleted_sources"] for a in arts),
                "flights": len(self._ds.flights) if pop else 0,
                "frames": sum(len(self._ds.frames(f["id"]).get("lat", [])) for f in self._ds.flights) if pop else 0,
                "events": sum(len(v) for v in self._ds.events.values()) if pop else 0,
            }
            return {"open": True, "case": dict(st["meta"]), "root": st["root"], "schema_version": 1,
                    "counts": counts, "artifacts_by_status": by_status,
                    "custody": md.verify_entries(st["custody"]), "tool": "dronedog", "tool_version": "0.1.0-mock",
                    "mock": True}

    def _require_case(self):
        if self._state is None:
            raise RuntimeError("No case is open")
        return self._state

    # ---------------------------------------------------------------- listings --
    def list_evidence(self) -> list[dict]:
        st = self._require_case()
        return copy.deepcopy(self._ds.evidence) if st["populated"] else []

    def list_artifacts(self) -> list[dict]:
        st = self._require_case()
        return copy.deepcopy(self._ds.artifacts) if st["populated"] else []

    def list_flights(self) -> list[dict]:
        st = self._require_case()
        if not st["populated"]:
            return []
        return [self._ds.flight_row(f["id"]) for f in self._ds.flights]

    def get_flight(self, flight_id: int, max_points: int = 5000) -> dict:
        st = self._require_case()
        if not st["populated"]:
            raise KeyError(f"no flight {flight_id}")
        fid = int(flight_id)
        f = self._ds.flight(fid)
        frames, info = decimate(self._ds.frames(fid), int(max_points or 0))
        row = self._ds.flight_row(fid)
        details = dict(f["details"])
        for k, v in (("total_distance_m", row["distance_m"]), ("max_height_m", row["max_alt_m"]),
                     ("max_h_speed_ms", row["max_speed_ms"])):
            if details.get(k) is None:
                details[k] = v
        return {"flight": row, "details": details, "frames": frames,
                "events": copy.deepcopy(self._ds.events.get(fid, [])), "decimation": info,
                "links": self._ds.links_for(fid) and [
                    {"flight_id": l["other"], "kind": l["kind"], "score": l["score"], "reasons": l["reasons"]}
                    for l in self._ds.links_for(fid)],
                "warnings": row["warnings"]}

    def get_track_geojson(self, flight_ids: list[int]) -> dict:
        st = self._require_case()
        feats = []
        if st["populated"]:
            for fid in flight_ids or []:
                fid = int(fid)
                row = self._ds.flight_row(fid)
                fr = self._ds.frames(fid)
                if fr:
                    dfr, _ = decimate({k: fr[k] for k in ("lat", "lon", "alt_rel_m", "t_offset_ms")}, 3000)
                    coords = [[lo, la, round(al, 1)] for la, lo, al in zip(dfr["lat"], dfr["lon"], dfr["alt_rel_m"])
                              if la is not None and lo is not None]
                    feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                                  "properties": {"flight_id": fid, "role": "track",
                                                 "label": f"#{fid} {row['aircraft_model']}",
                                                 "aircraft_model": row["aircraft_model"],
                                                 "aircraft_sn": row["aircraft_sn"], "source": row["source"],
                                                 "start_utc": row["start_utc"]}})
                if row["home_lat"] is not None:
                    feats.append({"type": "Feature",
                                  "geometry": {"type": "Point", "coordinates": [row["home_lon"], row["home_lat"]]},
                                  "properties": {"flight_id": fid, "role": "home"}})
        return {"type": "FeatureCollection", "features": feats}

    # ---------------------------------------------------------------- import ----
    def import_paths(self, paths: list[str], options: dict | None = None) -> dict:
        st = self._require_case()
        paths = [str(p) for p in (paths or [])]
        if not paths:
            raise ValueError("No paths to import")
        timeline = build_import_timeline(self._ds, paths, duplicate=st["populated"])
        job = self._jobs.add(_jobs.new_job("import", _jobs.IMPORT_STAGES))
        jid = job["job_id"]

        def run():
            t0 = time.monotonic()
            for t, snap in timeline:
                delay = t / self._speed - (time.monotonic() - t0)
                if delay > 0:
                    time.sleep(delay)
                if snap.get("state") == "done":
                    self._commit_import(st)
                    self._jobs.update(jid, force=True, **snap)
                else:
                    self._jobs.update(jid, **snap)
        threading.Thread(target=run, daemon=True).start()
        return {"job_id": jid}

    def _commit_import(self, st):
        with self._lock:
            if st["populated"]:
                return
            st["populated"] = True
            last = datetime.strptime(st["custody"][-1]["ts_utc"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            started = max(last, datetime.now(timezone.utc) - timedelta(seconds=10))
            chain = md.CustodyChain(started, st["custody"], scale=0.01)
            self._ds.add_import_custody(chain)
            st["custody"] = chain.entries

    # ---------------------------------------------------------------- report ----
    def export_report(self, flight_ids: list[int], formats: list[str]) -> dict:
        st = self._require_case()
        fids = [int(x) for x in flight_ids or []]
        fmts = []
        for f in (formats or []):
            f = ALIASES.get(f, f)
            if f in FORMATS and f not in fmts:
                fmts.append(f)
        if not fids:
            raise ValueError("Select at least one flight")
        if not fmts:
            raise ValueError("Select at least one format")
        timeline = build_export_timeline(st["root"], fids, fmts)
        job = self._jobs.add(_jobs.new_job("export", _jobs.EXPORT_STAGES))
        jid = job["job_id"]

        def run():
            t0 = time.monotonic()
            for t, snap in timeline:
                delay = t / self._speed - (time.monotonic() - t0)
                if delay > 0:
                    time.sleep(delay)
                if snap.get("state") == "done":
                    with self._lock:
                        chain = md.CustodyChain(datetime.now(timezone.utc), st["custody"])
                        chain.add("report.export", "report",
                                  inputs=[{"path": f"case.db#flights/{f}", "sha256": md.fake_hash(f"result{f}")}
                                          for f in fids],
                                  outputs=[{"path": x["path"], "sha256": x["sha256"]} for x in snap["result"]["files"]],
                                  params={"formats": fmts}, actor="examiner", now=True)
                        st["custody"] = chain.entries
                self._jobs.update(jid, force=snap.get("state") == "done", **snap)
        threading.Thread(target=run, daemon=True).start()
        return {"job_id": jid}

    # ---------------------------------------------------------------- custody ---
    def verify_custody(self) -> dict:
        st = self._require_case()
        time.sleep(0.4 / self._speed)   # feel of work
        return md.verify_entries(st["custody"])

    def list_custody(self) -> list[dict]:
        st = self._require_case()
        return copy.deepcopy(st["custody"])

    def reveal_path(self, path: str) -> dict:
        return {"ok": False, "error": "Mock mode: no files are written, nothing to show."}

    def _custody_append(self, action: str, component: str, params: dict) -> None:
        with self._lock:
            st = self._state
            if st is None:
                return
            chain = md.CustodyChain(datetime.now(timezone.utc), st["custody"])
            chain.add(action, component, params=params, actor="examiner", now=True)
            st["custody"] = chain.entries


# ------------------------------------------------------------------- timelines ---

def _stage(snap, key, state, done=None, total=None):
    for s in snap["stages"]:
        if s["key"] == key:
            s["state"] = state
            if done is not None:
                s["done"] = done
            if total is not None:
                s["total"] = total


def _fmt_gb(n):
    return f"{n / 1e9:.1f} GB"


def build_import_timeline(ds: md.Dataset, paths: list[str], duplicate: bool = False) -> list[tuple[float, dict]]:
    """Virtual-time list of (seconds, job fields) for a ~10 s import of the demo dataset."""
    snap = _jobs.new_job("import", _jobs.IMPORT_STAGES, job_id="x")
    for k in ("job_id", "kind", "started_utc", "finished_utc"):
        snap.pop(k)
    snap["counts"] = {"evidence": 0, "artifacts": 0, "parsed": 0, "encrypted": 0, "unsupported": 0,
                      "failed": 0, "deleted": 0, "flights": 0, "duplicates": 0}
    out: list[tuple[float, dict]] = []
    t = 0.0

    def emit(dt, **kw):
        nonlocal t
        t += dt
        snap.update(kw)
        out.append((round(t, 3), copy.deepcopy(snap)))

    def log(msg, level="info"):
        snap["log"].append({"ts_utc": None, "level": level, "message": msg})

    log(f"Mock backend: {len(paths)} selected path(s) are replaced by the synthetic demo evidence set.", "warn")
    evs = ds.evidence
    _stage(snap, "hashing", "running", 0, len(evs))
    emit(0.0, stage="hashing", message="Preparing evidence intake", progress=0.0)
    # hashing: ~3 s
    for i, ev in enumerate(evs):
        snap["current_path"] = ev["src_path"]
        for q in range(1, 5):
            emit(0.15, message=f"Hashing {ev['label']} ({_fmt_gb(ev['size'] * q / 4)} / {_fmt_gb(ev['size'])})",
                 progress=round(0.3 * (i + q / 4) / len(evs), 3))
        _stage(snap, "hashing", "running", i + 1)
        snap["counts"]["evidence"] = i + 1
        log(f"evidence.add {ev['label']}  sha256 {ev['sha256'][:16]}...")
        if ev["kind"] == "image":
            log("E01 stored MD5 matches computed MD5 (media verified)")
        emit(0.0)
    _stage(snap, "hashing", "done")
    arts = ds.artifacts
    _stage(snap, "extracting", "running", 0, len(arts))
    emit(0.1, stage="extracting", message="Walking file systems and containers")
    for i, a in enumerate(arts):
        row = copy.deepcopy(a)
        row.update({"status": "pending", "detected_kind": None, "detected_subtype": None, "reasons": [],
                    "error": None, "confidence": None, "flight_ids": []})
        if duplicate:
            row["duplicate"] = True
        snap["artifacts"].append(row)
        snap["current_path"] = a["sources"][0]["inner_path"]
        snap["counts"]["artifacts"] = i + 1
        snap["counts"]["deleted"] += a["deleted_sources"]
        if a["deleted_sources"]:
            log(f"Recovered deleted entry: {a['name']} ({a['evidence_label']})")
        _stage(snap, "extracting", "running", i + 1)
        emit(0.3, message=f"Extracting {a['name']}", progress=round(0.3 + 0.2 * (i + 1) / len(arts), 3))
    _stage(snap, "extracting", "done")
    _stage(snap, "detecting", "running", 0, len(arts))
    emit(0.1, stage="detecting", message="Detecting formats")
    for i, a in enumerate(arts):
        r = snap["artifacts"][i]
        r.update({"detected_kind": a["detected_kind"], "detected_subtype": a["detected_subtype"],
                  "confidence": a["confidence"], "reasons": a["reasons"], "encrypted": a["encrypted"]})
        _stage(snap, "detecting", "running", i + 1)
        emit(0.17, message=f"Detected {a['name']}: {a['detected_subtype'] or a['detected_kind']}",
             current_path=a["rel_path"], progress=round(0.5 + 0.15 * (i + 1) / len(arts), 3))
    _stage(snap, "detecting", "done")
    _stage(snap, "parsing", "running", 0, len(arts))
    emit(0.1, stage="parsing", message="Parsing flight logs")
    for i, a in enumerate(arts):
        r = snap["artifacts"][i]
        heavy = a["size"] > 30_000_000
        if duplicate:
            r.update({"status": a["status"], "error": a["error"], "flight_ids": a["flight_ids"]})
            snap["counts"]["duplicates"] += 1
            log(f"{a['name']}: already in case (sha256 match), not re-parsed")
        else:
            r.update({"status": a["status"], "error": a["error"], "flight_ids": a["flight_ids"]})
            if a["status"] in snap["counts"]:
                snap["counts"][a["status"]] += 1
            snap["counts"]["flights"] += len(a["flight_ids"])
            if a["status"] == "encrypted":
                log(f"{a['name']}: {a['error']}", "warn")
            elif a["status"] == "failed":
                log(f"{a['name']}: {a['error']}", "error")
            elif a["status"] == "unsupported":
                log(f"{a['name']}: not a flight log", "warn")
        _stage(snap, "parsing", "running", i + 1)
        emit(0.75 if heavy else 0.25, message=f"Parsed {a['name']}", current_path=a["rel_path"],
             progress=round(0.65 + 0.35 * (i + 1) / len(arts), 3))
    _stage(snap, "parsing", "done")
    log("analysis.run correlate: flight #1 (aircraft DAT) <-> #2 (app TXT) same flight, score 0.97")
    emit(0.2, state="done", stage="done", progress=1.0, current_path=None,
         message=("Import complete (all items already in case)" if duplicate else
                  f"Import complete: {snap['counts']['flights']} flights from {len(arts)} artifacts"),
         result={"flight_ids": [] if duplicate else [f["id"] for f in ds.flights]})
    return out


def build_export_timeline(root: str, fids: list[int], fmts: list[str]) -> list[tuple[float, dict]]:
    snap = _jobs.new_job("export", _jobs.EXPORT_STAGES, job_id="x")
    for k in ("job_id", "kind", "started_utc", "finished_utc"):
        snap.pop(k)
    out = []
    t = 0.0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    export_dir = f"{root}\\exports\\{stamp}"

    def emit(dt, **kw):
        nonlocal t
        t += dt
        snap.update(kw)
        out.append((round(t, 3), copy.deepcopy(snap)))

    _stage(snap, "rendering", "running", 0, len(fmts))
    for i, f in enumerate(fmts):
        emit(0.5 if f == "html" else 0.25, stage="rendering", message=f"Rendering {f.upper()} for {len(fids)} flight(s)",
             progress=round(0.7 * (i + 1) / len(fmts), 3))
        _stage(snap, "rendering", "running", i + 1)
    _stage(snap, "rendering", "done")
    _stage(snap, "writing", "running", 0, 1)
    emit(0.3, stage="writing", message="Writing hash manifest and methodology log", progress=0.85)
    _stage(snap, "writing", "done", 1)
    _stage(snap, "hashing", "running", 0, len(fmts))
    emit(0.3, stage="hashing", message="Hashing outputs", progress=0.95)
    names = {"html": "report.html", "kml": "tracks.kml", "geojson": "tracks.geojson",
             "json": "bundle.json"}
    files = [{"format": f, "path": f"{export_dir}\\{names[f]}", "size": {"html": 481_337, "kml": 418_220,
              "geojson": 512_904, "json": 9_310_448}[f], "sha256": md.fake_hash(stamp + f)} for f in fmts]
    files.append({"format": "manifest", "path": f"{export_dir}\\manifest.sha256", "size": 612,
                  "sha256": md.fake_hash(stamp + "manifest")})
    emit(0.2, state="done", stage="done", progress=1.0, message=f"Exported {len(fmts)} format(s) to {export_dir}",
         result={"export_dir": export_dir, "files": files, "mock": True})
    return out


# ------------------------------------------------------------------- fixture -----

def write_fixture(path: Path, max_points: int = 2000) -> dict:
    api = MockApi()
    api.case_open(md.CASE_ROOT)
    flights = api.list_flights()
    fx = {
        "_comment": "Generated by python -m dronedog.ui.mock_api --write-fixture. Synthetic, not evidence.",
        "app_info": dict(api.app_info(), basemap_dir=None, tile_cache_dir=None, portable=False,
                         app_dir="C:\\Program Files\\DroneDog", data_dir="%LOCALAPPDATA%\\DroneDog",
                         default_cases_dir="C:\\Users\\Examiner\\Documents\\DroneDog Cases",
                         log_file="%LOCALAPPDATA%\\DroneDog\\logs\\dronedog.log"),
        "recent": md.RECENT,
        "case_meta": md.CASE_META,
        "case_root": md.CASE_ROOT,
        "case_info": api.case_info(),
        "evidence": api.list_evidence(),
        "artifacts": api.list_artifacts(),
        "flights": flights,
        "flight_detail": {str(f["id"]): api.get_flight(f["id"], max_points) for f in flights},
        "tracks": api.get_track_geojson([f["id"] for f in flights]),
        "custody": api.list_custody(),
        "verify": md.verify_entries(api.list_custody()),
        "import_timeline": build_import_timeline(api._ds, ["(dev)"], duplicate=False),
        "import_timeline_dup": build_import_timeline(api._ds, ["(dev)"], duplicate=True),
        "export_timeline": build_export_timeline(md.CASE_ROOT, [1, 2], ["html", "kml", "geojson", "json"]),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fx, f, separators=(",", ":"), allow_nan=False)
    return fx


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        out = Path(__file__).resolve().parent / "web" / "dev" / "mock_fixture.json"
        write_fixture(out)
        print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")
    else:
        print(__doc__)
