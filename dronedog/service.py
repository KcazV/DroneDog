"""Backend service for the UI bridge (C7). ``dronedog/ui/api.py`` only forwards calls.

    svc = Service()
    svc.case_new({"name", "case_number", "examiner", "agency", "location"})   # or case_open(path)
    job = svc.import_paths([...], progress_cb)     # -> {"job_id"}; progress_cb(event) on a worker thread
    svc.job_status(job_id)
    svc.list_evidence() / list_artifacts() / list_flights()
    svc.get_flight(fid, max_points=5000) / get_track_geojson([fid, ...])
    svc.export_report([fid, ...], ["json", "geojson", "kml", "pdf"])   # synchronous; returns files
    svc.verify_custody() / list_custody()

Every return value is JSON-serialisable. Row shapes follow the docstrings in
``dronedog/ui/api.py``.
"""
from __future__ import annotations

import hashlib
import html
import json
import uuid
import shutil
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dronedog import PRODUCT_NAME, __version__, core_bridge
from dronedog.case import Case
from dronedog.case.custody import io_ref
from dronedog.case.hashing import set_readonly
from dronedog.case.store import FRAME_COLUMN_NAMES
from dronedog.pipeline import JOBS, ImportJob, ImportOptions, process_pending

# pipeline stage -> UI job stage (dronedog/ui/jobs.py IMPORT_STAGES)
UI_STAGE = {"ingest": "hashing", "extract": "extracting", "detect": "detecting", "parse": "parsing",
            "done": "done"}


def _j(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def _decimate(frames: dict, max_points: int) -> tuple[dict, dict]:
    try:
        from dronedog.ui.decimate import decimate
        return decimate(frames, max_points)
    except Exception:  # noqa: BLE001 - ui package absent: plain stride
        n = len(next(iter(frames.values()), []))
        if not max_points or n <= max_points:
            return frames, {"total": n, "returned": n, "method": "none"}
        step = n / max_points
        idx = sorted({int(i * step) for i in range(max_points)} | {n - 1})
        return {k: [v[i] for i in idx] for k, v in frames.items()}, \
            {"total": n, "returned": len(idx), "method": "stride"}


class Service:
    def __init__(self):
        self.case: Case | None = None
        self._lock = threading.RLock()

    # -- case -------------------------------------------------------------------
    def _need(self) -> Case:
        if self.case is None:
            raise RuntimeError("no case is open")
        return self.case

    def case_new(self, meta: dict) -> dict:
        """meta: {name, examiner, case_number?, agency?, notes?, location | path}.
        The case folder is ``path`` if given, else ``<location>/<case_number or name>``.
        There is no default location: the examiner chooses where the case lives."""
        if meta.get("path"):
            root = Path(meta["path"])
        else:
            if not str(meta.get("location") or "").strip():
                raise ValueError("Choose a case folder location")
            location = Path(meta["location"])
            leaf = "".join(c if c.isalnum() or c in " -_." else "_" for c in
                           (meta.get("case_number") or meta["name"])).strip(" .") or "case"
            root = location / leaf
        with self._lock:
            self._close()
            self.case = Case.create(root, name=meta["name"], examiner=meta.get("examiner") or "",
                                    agency=meta.get("agency"), case_number=meta.get("case_number"),
                                    notes=meta.get("notes"))
        return self.case_info()

    def case_open(self, path: str) -> dict:
        with self._lock:
            self._close()
            self.case = Case.open(path)
        return self.case_info()

    def _close(self):
        if self.case is not None:
            self.case.close()
            self.case = None

    def case_info(self) -> dict:
        if self.case is None:
            return {"open": False}
        d = self.case.info()
        d["open"] = True
        d["core_version"] = core_bridge.core_version()
        return d

    # -- import -------------------------------------------------------------------
    def import_paths(self, paths: list[str], progress_cb: Callable[[dict], None] | None = None,
                     copy: bool | None = None, hash_evidence: bool = True) -> dict:
        """Starts ingest+extract+detect+parse on a worker thread. progress_cb gets
        {"job_id","stage","ui_stage","current","total","progress","message"}."""
        case = self._need()

        def cb(ev):
            if progress_cb is None:
                return
            ev = dict(ev)
            ev["ui_stage"] = UI_STAGE.get(ev["stage"], ev["stage"])
            cur, tot = ev.get("current"), ev.get("total")
            ev["progress"] = (min(1.0, cur / tot) if cur is not None and tot else None)
            progress_cb(ev)

        job = ImportJob(case, paths, cb, ImportOptions(copy=copy, hash_evidence=hash_evidence))
        JOBS[job.id] = job
        job.start()
        return {"job_id": job.id}

    def job_status(self, job_id: str) -> dict:
        job = JOBS.get(job_id)
        if job is None:
            return {"job_id": job_id, "state": "unknown"}
        st = job.status()
        st["ui_stage"] = UI_STAGE.get(st.get("stage") or "", st.get("stage"))
        if self.case is not None:
            st["counts"] = self.counts()
            if st["state"] != "running":
                st["result"] = {"flight_ids": [f["id"] for f in self.case.store.query("SELECT id FROM flights")],
                                "summary": st.get("summary")}
        return st

    def cancel_job(self, job_id: str) -> dict:
        job = JOBS.get(job_id)
        if job:
            job.cancel()
        return {"job_id": job_id, "cancelling": bool(job)}

    def run_pending(self, retry_failed: bool = False) -> dict:
        return process_pending(self._need(), retry_failed=retry_failed)

    def counts(self) -> dict:
        q = self._need().store.one
        st = {r["status"]: r["n"] for r in self._need().store.query(
            "SELECT status, COUNT(*) n FROM artifacts GROUP BY status")}
        return {"evidence": q("SELECT COUNT(*) n FROM evidence")["n"],
                "artifacts": q("SELECT COUNT(*) n FROM artifacts")["n"],
                "parsed": st.get("parsed", 0), "encrypted": st.get("encrypted", 0),
                "unsupported": st.get("unsupported", 0), "failed": st.get("failed", 0),
                "pending": st.get("pending", 0),
                "deleted": q("SELECT COUNT(DISTINCT artifact_id) n FROM artifact_sources WHERE deleted=1")["n"],
                "flights": q("SELECT COUNT(*) n FROM flights")["n"],
                "duplicates": q("SELECT COUNT(*) - COUNT(DISTINCT artifact_id) n FROM artifact_sources")["n"]}

    # -- listings -------------------------------------------------------------------
    def list_evidence(self) -> list[dict]:
        c = self._need()
        rows = c.store.query(
            "SELECT e.*, (SELECT COUNT(*) FROM artifact_sources s WHERE s.evidence_id=e.id) artifact_count "
            "FROM evidence e ORDER BY e.id")
        out = []
        for r in rows:
            meta = _j(r.pop("meta_json"), {})
            ver = meta.get("verifications") or []
            bad = [v for v in ver if v.get("result") not in ("match", "n/a")]
            r["verified"] = (not bad) if ver else None
            r["verifications"] = ver
            r["walk"] = {k: (meta.get("walk") or {}).get(k) for k in ("files_seen", "candidates", "deleted_candidates")}
            r["partitions"] = [x for x in (meta.get("walk") or {}).get("containers", []) if x.get("type") == "partition"]
            r["ios"] = [m.get("info") for m in meta.get("ios_backups", [])]
            r["ewf"] = meta.get("ewf")
            out.append(r)
        return out

    def list_artifacts(self) -> list[dict]:
        c = self._need()
        ev_label = {e["id"]: e["label"] for e in c.store.query("SELECT id, label FROM evidence")}
        srcs: dict[int, list] = {}
        for s in c.store.query("SELECT * FROM artifact_sources ORDER BY id"):
            srcs.setdefault(s["artifact_id"], []).append({
                "evidence_id": s["evidence_id"], "evidence_label": ev_label.get(s["evidence_id"]),
                "inner_path": s["inner_path"], "container": s["container"], "offset": s["offset"],
                "partition_index": s["partition_index"], "fs_type": s["fs_type"], "meta_addr": s["meta_addr"],
                "deleted": bool(s["deleted"]), "overwrite_status": s["overwrite_status"],
                "mtime_utc": s["mtime_utc"], "crtime_utc": s["crtime_utc"], "ios_domain": s["ios_domain"],
                "ios_relative_path": s["ios_relative_path"], "app_hint": s["app_hint"],
                "reasons": _j(s["reasons_json"], []), "extra": _j(s["extra_json"], {})})
        fl: dict[int, list] = {}
        for f in c.store.query("SELECT id, artifact_id FROM flights"):
            fl.setdefault(f["artifact_id"], []).append(f["id"])
        out = []
        for a in c.store.query("SELECT * FROM artifacts ORDER BY id"):
            det = _j(a.pop("detection_json"), {})
            ss = srcs.get(a["id"], [])
            a.update({"evidence_label": ev_label.get(a["evidence_id"]), "confidence": det.get("confidence"),
                      "reasons": det.get("reasons") or [], "encrypted": det.get("encrypted"),
                      "detector": det.get("detector"), "partial": det.get("partial"),
                      "deleted": bool(ss) and all(s["deleted"] for s in ss),
                      "deleted_sources": sum(1 for s in ss if s["deleted"]), "sources": ss,
                      "flight_ids": fl.get(a["id"], [])})
            out.append(a)
        return out

    def analysis(self, flight_id: int, force: bool = False) -> dict:
        """Examiner summary (dronedog.analysis.summary) for one flight; computed once and kept
        in flights.analysis_json, which is separate from the hashed parse result."""
        from .analysis.summary import SUMMARY_VERSION, default_locator, summarize
        c = self._need()
        f = c.store.one("SELECT * FROM flights WHERE id=?", (int(flight_id),))
        if f is None:
            raise KeyError(f"no flight {flight_id}")
        a = _j(f.get("analysis_json"))
        if a and a.get("version") == SUMMARY_VERSION and not force:
            return a
        events = c.store.query("SELECT t_offset_ms, kind, severity, message FROM events WHERE flight_id=? "
                               "ORDER BY t_offset_ms, id", (f["id"],))
        a = summarize(_j(f["details_json"], {}), self.frames(f["id"]), events,
                      encrypted=bool(f["encrypted_unreadable"]), records_total=f["records_total"],
                      source=f["source"], locate=default_locator())
        c.store.execute("UPDATE flights SET analysis_json=? WHERE id=?", (json.dumps(a, default=str), f["id"]))
        return a

    def _flight_row(self, f: dict, names: dict) -> dict:
        d = _j(f["details_json"], {})
        bb = _j(f["bbox_json"])
        art = names.get(f["artifact_id"], {})
        try:
            a = self.analysis(f["id"])
        except Exception:  # noqa: BLE001 - a broken summary must not hide the flight
            a = {}
        st = a.get("stats") or {}
        ids = a.get("identifiers") or []

        def idv(*labels):
            return list(dict.fromkeys(i["value"] for i in ids if i["label"] in labels))

        loc = a.get("location") or {}
        return {
            "classification": a.get("classification"), "class_label": a.get("label"),
            "is_real_flight": a.get("is_real_flight"),
            "takeoff_utc": (a.get("takeoff") or {}).get("t_utc"), "landing_utc": (a.get("landing") or {}).get("t_utc"),
            "air_time_s": st.get("air_time_s"), "path_distance_m": st.get("path_distance_m"),
            "gps_points": st.get("gps_points"),
            "location_label": loc.get("label"), "location_summary": loc.get("summary"),
            "app_location": a.get("app_location"),
            "battery_serials": idv("Battery serial number", "Battery barcode / serial"),
            "fc_ids": idv("Flight controller ID (Mc ID)", "Flight controller serial"),
            "fc_firmware": idv("Flight controller firmware (Mc Ver)"),
            "uuids": idv("Unique ID (UUID)", "Center board log UID", "Activation product ID (proId)"),
            "identifier_count": len(ids),
            "id": f["id"], "artifact_id": f["artifact_id"], "artifact_name": art.get("name"),
            "evidence_label": art.get("label"), "source": f["source"], "adapter": f["adapter"],
            "adapter_version": f["adapter_version"], "start_utc": f["start_utc"], "duration_s": f["duration_s"],
            "distance_m": d.get("total_distance_m") if d.get("total_distance_m") is not None else st.get("path_distance_m"),
            "max_alt_m": st.get("max_height_m") if st.get("max_height_m") is not None else d.get("max_height_m"),
            "max_speed_ms": d.get("max_h_speed_ms") if d.get("max_h_speed_ms") is not None else st.get("max_speed_ms"),
            "frame_count": f["frame_count"],
            "aircraft_model": d.get("aircraft_model"), "aircraft_name": d.get("aircraft_name"),
            "aircraft_sn": d.get("aircraft_sn"), "app_platform": d.get("app_platform"),
            "app_version": d.get("app_version"), "log_version": d.get("log_version"),
            "home_lat": d.get("home_lat"), "home_lon": d.get("home_lon"),
            "bbox": [bb["min_lon"], bb["min_lat"], bb["max_lon"], bb["max_lat"]] if bb else None,
            "encrypted_unreadable": bool(f["encrypted_unreadable"]), "warnings": _j(f["warnings_json"], []),
            "records_total": f["records_total"], "records_decoded": f["records_decoded"],
            "records_failed": f["records_failed"], "result_sha256": f["result_sha256"],
            "links": self.links_for(f["id"]),
        }

    def links_for(self, flight_id: int) -> list[dict]:
        """Same-flight links touching this flight, from the other flight's point of view."""
        out = []
        for r in self._need().store.query(
                "SELECT l.*, f.source, f.start_utc, a.name FROM links l "
                "JOIN flights f ON f.id = CASE WHEN l.flight_a=? THEN l.flight_b ELSE l.flight_a END "
                "JOIN artifacts a ON a.id=f.artifact_id WHERE l.flight_a=? OR l.flight_b=? ORDER BY l.score DESC",
                (flight_id, flight_id, flight_id)):
            other = r["flight_b"] if r["flight_a"] == flight_id else r["flight_a"]
            out.append({"flight_id": other, "kind": r["kind"], "score": r["score"], "source": r["source"],
                        "artifact_name": r["name"], "start_utc": r["start_utc"],
                        "reasons": _j(r["reasons_json"], [])})
        return out

    def correlate(self) -> dict:
        from .analysis.correlate import correlate
        return {"links": correlate(self._need())}

    def _art_names(self) -> dict:
        return {r["id"]: r for r in self._need().store.query(
            "SELECT a.id, a.name, e.label FROM artifacts a LEFT JOIN evidence e ON e.id=a.evidence_id")}

    def list_flights(self) -> list[dict]:
        c = self._need()
        names = self._art_names()
        rows = [self._flight_row(f, names) for f in c.store.query("SELECT * FROM flights ORDER BY start_utc, id")]
        # fill max alt/speed from frames when the details block lacks them
        for r in rows:
            if r["frame_count"] and (r["max_alt_m"] is None or r["max_speed_ms"] is None):
                m = c.store.one("SELECT MAX(alt_rel_m) a, MAX(speed_h_ms) s FROM frames WHERE flight_id=?", (r["id"],))
                r["max_alt_m"] = r["max_alt_m"] if r["max_alt_m"] is not None else m["a"]
                r["max_speed_ms"] = r["max_speed_ms"] if r["max_speed_ms"] is not None else m["s"]
        return rows

    def frames(self, flight_id: int) -> dict:
        """Full columnar C3 frames for one flight (plus frame_extra columns)."""
        c = self._need()
        cur = c.store.conn().execute(
            f"SELECT {', '.join(FRAME_COLUMN_NAMES)} FROM frames WHERE flight_id=? ORDER BY idx", (flight_id,))
        rows = cur.fetchall()
        cols = {name: [r[i] for r in rows] for i, name in enumerate(FRAME_COLUMN_NAMES)}
        cols["battery_cells_v"] = [json.loads(v) if v else None for v in cols["battery_cells_v"]]
        for b in ("motor_on", "is_flying"):
            cols[b] = [None if v is None else bool(v) for v in cols[b]]
        cols = {k: v for k, v in cols.items() if any(x is not None for x in v)}
        for r in c.store.query("SELECT name, values_json FROM frame_extra WHERE flight_id=?", (flight_id,)):
            vals = _j(r["values_json"], [])
            if len(vals) == len(rows):
                cols[r["name"]] = vals
        return cols

    def get_flight(self, flight_id: int, max_points: int = 5000) -> dict:
        c = self._need()
        f = c.store.one("SELECT * FROM flights WHERE id=?", (int(flight_id),))
        if f is None:
            raise KeyError(f"no flight {flight_id}")
        frames, info = _decimate(self.frames(f["id"]), int(max_points or 0))
        events = c.store.query("SELECT id, t_offset_ms, kind, severity, message, raw FROM events WHERE flight_id=? "
                               "ORDER BY t_offset_ms, id", (f["id"],))
        row = self._flight_row(f, self._art_names())
        try:
            from .analysis.explain import explain_many
            events = explain_many(events)
        except Exception:  # noqa: BLE001 - explanations are an aid; raw events always shown
            pass
        return {"flight": row, "details": _j(f["details_json"], {}), "frames": frames, "events": events,
                "decimation": info, "links": row["links"], "warnings": row["warnings"],
                "analysis": self.analysis(f["id"])}

    def track(self, flight_id: int, max_points: int = 3000) -> list[list[float]]:
        rows = self._need().store.conn().execute(
            "SELECT lon, lat, alt_rel_m FROM frames WHERE flight_id=? AND lat IS NOT NULL AND lon IS NOT NULL "
            "ORDER BY idx", (flight_id,)).fetchall()
        # C3: no-fix must never be drawn as 0,0; altitude stays None when not decoded.
        pts = [[r[0], r[1], r[2]] for r in rows if abs(r[0]) > 1e-5 or abs(r[1]) > 1e-5]
        if max_points and len(pts) > max_points:
            step = len(pts) / max_points
            pts = [pts[int(i * step)] for i in range(max_points)] + [pts[-1]]
        return pts

    def get_track_geojson(self, flight_ids: list[int], max_points: int = 3000) -> dict:
        names = self._art_names()
        feats = []
        for fid in flight_ids:
            f = self._need().store.one("SELECT * FROM flights WHERE id=?", (int(fid),))
            if f is None:
                continue
            row = self._flight_row(f, names)
            pts = self.track(f["id"], max_points)
            props = {"flight_id": f["id"], "label": row["artifact_name"], "aircraft_model": row["aircraft_model"],
                     "aircraft_sn": row["aircraft_sn"], "source": row["source"], "start_utc": row["start_utc"]}
            if len(pts) >= 2:
                coords = [q if q[2] is not None else q[:2] for q in pts]
                feats.append({"type": "Feature", "properties": props,
                              "geometry": {"type": "LineString", "coordinates": coords}})
            hl, hn, src = row["home_lat"], row["home_lon"], "header"
            if hl is None or hn is None:
                h = self._need().store.one(
                    "SELECT home_lat, home_lon FROM frames WHERE flight_id=? AND home_lat IS NOT NULL "
                    "AND home_lon IS NOT NULL AND (ABS(home_lat) > 1e-5 OR ABS(home_lon) > 1e-5) "
                    "ORDER BY idx LIMIT 1", (f["id"],))
                hl, hn, src = (h["home_lat"], h["home_lon"], "frames") if h else (None, None, None)
            if hl is not None and hn is not None:
                feats.append({"type": "Feature", "properties": {"flight_id": f["id"], "role": "home",
                                                                "home_source": src},
                              "geometry": {"type": "Point", "coordinates": [hn, hl]}})
        return {"type": "FeatureCollection", "features": feats}

    # -- custody -------------------------------------------------------------------
    def verify_custody(self) -> dict:
        return self._need().verify_custody()

    def list_custody(self) -> list[dict]:
        return self._need().custody.entries()

    # -- export -------------------------------------------------------------------
    def export_report(self, flight_ids: list[int], formats: list[str], out_dir: str | None = None) -> dict:
        """Writes the requested formats to exports/<UTC stamp>/ (+ manifest.sha256), sets them
        read-only and logs report.export. 'pdf' is rendered as a printable HTML exhibit
        (report.html) until a PDF engine is bundled. Returns {"export_dir","files":[...]}."""
        with self._lock:  # case_open/case_new wait instead of swapping the case mid-export
            c = self._need()
            now = datetime.now(timezone.utc)
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            final = Path(out_dir) if out_dir else c.exports_dir / f"{stamp}_{now.microsecond // 1000:03d}"
            base, n = final, 2
            while final.exists() and any(final.iterdir()):
                final = base.with_name(f"{base.name}~{n}")
                n += 1
            d = final.parent / f".tmp-{uuid.uuid4().hex[:12]}"
            d.mkdir(parents=True)
            try:
                return self._export_into(c, d, final, stamp, flight_ids, formats)
            except BaseException:
                shutil.rmtree(d, ignore_errors=True)
                raise

    def _export_into(self, c, d: Path, final: Path, stamp: str, flight_ids, formats) -> dict:
        fids = [int(x) for x in flight_ids] or [f["id"] for f in c.store.query("SELECT id FROM flights")]
        flights = [f for f in self.list_flights() if f["id"] in fids]
        written: list[Path] = []
        fmts = [x.lower() for x in formats]
        if "json" in fmts:
            bundle = {"generator": f"{PRODUCT_NAME} {__version__}", "core": core_bridge.core_version(),
                      "generated_utc": stamp, "case": c.meta, "custody": c.verify_custody(),
                      "evidence": self.list_evidence(),
                      "artifacts": [a for a in self.list_artifacts() if set(a["flight_ids"]) & set(fids)],
                      "flights": []}
            for f in flights:
                g = self.get_flight(f["id"], 0)
                bundle["flights"].append(dict(f, details=g["details"], events=g["events"], frames=g["frames"]))
            p = d / "bundle.json"
            p.write_text(json.dumps(bundle, indent=1, default=str), encoding="utf-8", newline="\n")
            written.append(p)
        if "geojson" in fmts:
            p = d / "tracks.geojson"
            p.write_text(json.dumps(self.get_track_geojson(fids, 0)), encoding="utf-8", newline="\n")
            written.append(p)
        if "kml" in fmts:
            p = d / "tracks.kml"
            p.write_text(self._kml(flights), encoding="utf-8", newline="\n")
            written.append(p)
        if "pdf" in fmts or "html" in fmts:
            p = d / "report.html"
            p.write_text(self._html(flights), encoding="utf-8", newline="\n")
            written.append(p)
        files = []
        for p in written:
            data = p.read_bytes()
            files.append({"format": {".json": "json", ".geojson": "geojson", ".kml": "kml", ".html": "html"}[p.suffix],
                          "path": str(p), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        man = d / "manifest.sha256"
        # LF only: sha256sum -c rejects CRLF manifests
        man.write_bytes("".join(f"{f['sha256']}  {Path(f['path']).name}\n" for f in files).encode("utf-8"))
        if final.exists():
            final.rmdir()  # only an empty leftover can remain here (checked by the caller)
        d.rename(final)
        man = final / man.name
        written = [final / q.name for q in written]
        for f in files:
            f["path"] = str(final / Path(f["path"]).name)
        for q in written + [man]:
            set_readonly(q)
        d = final
        c.log("report.export", component="report.service", actor="examiner",
              inputs=[{"flight_id": f["id"], "sha256": f["result_sha256"]} for f in flights],
              outputs=[io_ref(c.rel(Path(f["path"])), f["sha256"], size=f["size"], format=f["format"]) for f in files]
              + [io_ref(c.rel(man), hashlib.sha256(man.read_bytes()).hexdigest())],
              params={"formats": fmts, "flight_ids": fids,
                      "note": "pdf requested -> printable HTML exhibit (report.html)" if "pdf" in fmts else None})
        return {"export_dir": str(d), "files": files, "manifest": str(man)}

    def _kml(self, flights: list[dict]) -> str:
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
                 f"<name>{html.escape(self._need().meta.get('name') or 'DroneDog')}</name>",
                 '<Style id="trk"><LineStyle><color>ff3aa4e0</color><width>3</width></LineStyle></Style>']
        for f in flights:
            pts = self.track(f["id"], 0)
            if len(pts) < 2:
                continue
            has_alt = all(q[2] is not None for q in pts)
            coords = " ".join(f"{q[0]:.7f},{q[1]:.7f},{q[2]:.1f}" if has_alt else f"{q[0]:.7f},{q[1]:.7f}"
                              for q in pts)
            mode = "relativeToGround" if has_alt else "clampToGround"
            text = (f"{f.get('aircraft_model') or ''} {f.get('aircraft_sn') or ''} start {f.get('start_utc') or '?'} "
                    f"flight #{f['id']}" + ("" if has_alt else " (altitude not decoded)"))
            # Google Earth renders <description> as HTML: escape for HTML, then for XML.
            desc = html.escape(html.escape(_clean(text)))
            parts.append(f"<Placemark><name>{html.escape(_clean(str(f['artifact_name'])))}</name>"
                         f"<description>{desc}"
                         f"</description><styleUrl>#trk</styleUrl><LineString><altitudeMode>{mode}"
                         f"</altitudeMode><coordinates>{coords}</coordinates></LineString></Placemark>")
        parts.append("</Document></kml>")
        return "\n".join(parts)

    # basemap tile sources for report maps: [{"id","name","attribution","fetch"(z,x,y)->tile}]; the UI host
    # (dronedog/ui/api.py) installs a provider that offers offline packs and, only when the examiner
    # has online maps on, the monitored online sources. Default: none (graticule maps).
    report_tile_sources: Callable[[], list] | None = None

    def _html(self, flights: list[dict]) -> str:
        from .report.exhibit import build_html
        try:
            sources = self.report_tile_sources() if self.report_tile_sources else []
        except Exception:  # noqa: BLE001 - maps fall back to the graticule
            sources = []
        return build_html(self, flights, sources)

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _clean(s: str) -> str:
    """XML 1.0 forbids C0 control chars even when escaped; real DJI headers contain NUL runs."""
    return _CTRL.sub("\ufffd", s)


def _note(f: dict) -> str:
    notes = []
    if f.get("encrypted_unreadable"):
        notes.append("ENCRYPTED (header only)")
    tot, bad = f.get("records_total") or 0, f.get("records_failed") or 0
    if any("INCOMPLETE" in str(w) for w in (f.get("warnings") or [])) or (tot and bad / tot >= 0.25):
        notes.append("INCOMPLETE")
    if not f.get("frame_count") and not f.get("encrypted_unreadable"):
        notes.append("NO TRACK")
    return "; ".join(notes)


_default: Service | None = None


def default_service() -> Service:
    global _default
    if _default is None:
        _default = Service()
    return _default
