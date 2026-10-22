"""C7 UI bridge: the object exposed to JavaScript as ``window.pywebview.api``.

Every public method is callable from JS (``await window.pywebview.api.list_flights()``)
and runs on its own worker thread.  Return values must be JSON-serialisable.  Errors
are raised as exceptions; pywebview rejects the JS promise with the message and the
UI shows it in a toast.  Long work returns ``{"job_id"}`` immediately and reports via
``window.dronedog.onProgress(job)`` (job shape: see ``dronedog/ui/jobs.py``).

Attributes starting with ``_`` are NOT exposed to JS (pywebview skips them).

``Api`` is the real backend.  The UI-only plumbing (dialogs, jobs, recent cases, map
packs, reveal in Explorer) is implemented here.  Everything that touches a case or
the pipeline is a thin call into ``dronedog.case`` / pipeline and is marked
``TODO(integrator)``; ``mock_api.MockApi`` implements the same methods with
synthetic data and is the reference for the shapes below.

Method summary (C7 v1 + UI additions marked [+], see the report / ARCHITECTURE C7):

    app_info()                                   [+] {"app","version","mode","core_version","basemap_dir"}
    recent_cases()                               [+] [{"path","name","case_number","opened_utc"}]
    case_new(meta)                               meta {name, case_number, examiner, agency, location?} -> case_info()
    case_open(path)                              -> case_info()
    case_info()                                  -> {"open": bool, "case": {...case.json}, "root", "counts", "custody"}
    pick_paths(kind)                             kind 'files'|'folder'|'image'|'mbtiles'[+]|'case'[+] -> [paths]
    import_paths(paths)                          -> {"job_id"}
    job_status(job_id)                           -> job dict
    list_evidence() / list_artifacts() / list_flights()   -> [rows]
    get_flight(flight_id, max_points=5000)       -> {"flight","details","frames","events","decimation","links"}
    get_track_geojson(flight_ids)                -> GeoJSON FeatureCollection
    basemaps() / tile(basemap_id, z, x, y)       -> [packs] / {"mime","data"} | None   (XYZ in; TMS flip inside)
    import_basemap(path)                         [+] -> {"imported": pack, "basemaps": [packs]}
    export_report(flight_ids, formats)           -> {"job_id"}; job.result = {"export_dir","files":[...]}
    verify_custody()                             -> {"ok","entries","first_bad_line","first_bad_seq","reason","head_seq","head_hash"}
    list_custody()                               [+] -> [C6 entries]
    reveal_path(path)                            [+] -> {"ok"}  (opens Explorer on an export/case path)
    net_status(include_os=True)                  [+] network light: state, hosts contacted, OS sockets (netmon.py)
    set_online(enabled)                          [+] online map tiles on/off (D11); logged to the case custody log
    ui_log(level, message)                       [+] page-side errors into <data dir>/logs/dronedog.log
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dronedog.ui import jobs as _jobs
from dronedog.ui.mbtiles import MBTilesStore, default_basemap_dir
from dronedog.ui.netmon import NetMonitor
from dronedog.ui.online_tiles import PREFIX as ONLINE_PREFIX, SOURCES as ONLINE_SOURCES, OnlineTiles

try:  # package metadata from the core agent; tolerate it being absent during parallel work
    from dronedog import __version__ as _APP_VERSION
except Exception:  # noqa: BLE001
    _APP_VERSION = "0.0.0-dev"

PICK_FILTERS = {
    "files": ("Evidence (*.E01;*.Ex01;*.001;*.dd;*.img;*.raw;*.bin;*.DAT;*.txt;*.zip)", "All files (*.*)"),
    "image": ("Disk images (*.E01;*.e01;*.Ex01;*.001;*.dd;*.img;*.raw;*.bin;*.dmg)", "All files (*.*)"),
    "mbtiles": ("Map packs (*.mbtiles)", "All files (*.*)"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RecentCases:
    """Most-recently-opened case folders, stored per Windows user (not evidence)."""

    def __init__(self, path: Path | None = None, limit: int = 12):
        from dronedog.paths import data_dir
        self.path = path or data_dir() / "recent_cases.json"
        self.limit = limit
        self._lock = threading.Lock()

    def load(self) -> list[dict]:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return [d for d in data if isinstance(d, dict) and d.get("path")]
        except (OSError, ValueError):
            return []

    def touch(self, path: str, name: str | None, case_number: str | None) -> None:
        with self._lock:
            items = [d for d in self.load() if os.path.normcase(d["path"]) != os.path.normcase(path)]
            items.insert(0, {"path": path, "name": name, "case_number": case_number,
                             "opened_utc": _utc_now()})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items[: self.limit], f, indent=1)
            os.replace(tmp, self.path)


class Api:
    """Real backend. ``app.py`` sets ``_window`` and the progress sink."""

    _mode = "live"

    def __init__(self, basemap_dir: str | os.PathLike | None = None,
                 recent: RecentCases | None = None):
        self._window = None                       # webview.Window, set by app.py
        self._jobs = _jobs.JobRegistry()
        self._maps = MBTilesStore(basemap_dir)
        self._recent = recent or RecentCases()
        self._net = NetMonitor()                   # the only path to the network (D11)
        self._online = OnlineTiles(self._net)

    # ---------------------------------------------------------------- host plumbing --
    def _attach(self, window, sink: Callable[[dict], None] | None) -> None:
        self._window = window
        self._jobs.set_sink(sink)

    _log_file: str | None = None          # set by app.py (crashlog)

    def app_info(self) -> dict:
        core = None
        try:
            import dronedog_core  # type: ignore
            core = dronedog_core.version()
        except Exception:  # noqa: BLE001
            core = None
        from dronedog import paths
        from dronedog.ui import crashlog
        return {"app": "DroneDog", "version": _APP_VERSION, "mode": self._mode,
                "core_version": core, "basemap_dir": str(self._maps.folder),
                "tile_cache_dir": str(self._online.cache), "log_file": self._log_file or str(crashlog.log_path()),
                **paths.describe()}

    def ui_log(self, level: str, message: str) -> dict:
        """Page-side errors (JS exceptions, CSP reports) into the app log (crashlog)."""
        import logging
        lvl = {"error": logging.ERROR, "warn": logging.WARNING}.get(str(level), logging.INFO)
        logging.getLogger("dronedog.ui.web").log(lvl, "%s", str(message)[:4000])
        return {"ok": True}

    def recent_cases(self) -> list[dict]:
        items = self._recent.load()
        for d in items:
            d["exists"] = Path(d["path"]).is_dir()
        return items

    def pick_paths(self, kind: str = "files") -> list[str]:
        """Native open dialog.  Returns [] when cancelled."""
        import webview  # local import: tests construct Api without a GUI

        if self._window is None:
            return []
        if kind in ("folder", "case"):
            res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        elif kind in PICK_FILTERS:
            res = self._window.create_file_dialog(
                webview.FileDialog.OPEN, allow_multiple=(kind != "mbtiles"),
                file_types=PICK_FILTERS[kind])
        else:
            raise ValueError(f"unknown pick kind {kind!r}")
        return [str(p) for p in (res or [])]

    def job_status(self, job_id: str) -> dict:
        j = self._jobs.get(job_id)
        if j is None:
            raise KeyError(f"unknown job {job_id}")
        return j

    def reveal_path(self, path: str) -> dict:
        p = Path(path)
        if not p.exists():
            return {"ok": False, "error": "path does not exist"}
        if os.name == "nt":
            if p.is_dir():
                subprocess.Popen(["explorer", str(p)])
            else:
                subprocess.Popen(["explorer", "/select,", str(p)])
            return {"ok": True}
        return {"ok": False, "error": "reveal is only implemented on Windows"}

    # ---------------------------------------------------------------- map packs --
    def basemaps(self) -> list[dict]:
        return self._maps.list() + self._online.list()

    def tile(self, basemap_id: str, z: int, x: int, y: int) -> dict | None:
        if str(basemap_id).startswith(ONLINE_PREFIX):
            return self._online.tile(basemap_id, z, x, y)
        return self._maps.tile(basemap_id, z, x, y)

    # ---------------------------------------------------------------- network (D11) --
    def net_status(self, include_os: bool = True) -> dict:
        return self._net.status(bool(include_os))

    def set_online(self, enabled: bool) -> dict:
        enabled = bool(enabled)
        if enabled != self._net.online:
            self._net.set_online(enabled, [v["name"] for v in ONLINE_SOURCES.values()])
            self._log_network("toggle")
        return self._net.status(False)

    def _network_params(self, reason: str) -> dict:
        return {"reason": reason, "online_maps": self._net.online,
                "sources": [{"id": k, "name": v["name"], "url": v["url"]} for k, v in ONLINE_SOURCES.items()]
                if self._net.online else [],
                "webview": "CSP default-src 'self'; non-loopback traffic sent to a dead proxy"}

    def _log_network(self, reason: str) -> None:
        action = "network.online_on" if self._net.online else "network.online_off"
        try:
            self._custody_append(action, "ui.network", self._network_params(reason))
        except Exception:  # noqa: BLE001 - no case open, or the log refused: the UI still works
            pass

    def _custody_append(self, action: str, component: str, params: dict) -> None:
        case = getattr(self._svc(), "case", None)
        if case is None:
            return
        try:
            case.log(action, component=component, params=params, actor="examiner")
        except ValueError:
            # custody ACTIONS predates D11: record it as a note with the intended action name
            case.log("note", component=component, params=dict(params, event=action), actor="examiner")

    def import_basemap(self, path: str) -> dict:
        imported = self._maps.import_pack(path)
        return {"imported": imported, "basemaps": self._maps.list()}

    # ---------------------------------------------------------------- case + pipeline --
    # Everything below forwards to ``dronedog.service`` (owned by the Python-core agent):
    # one plain function per C7 method with the same name and arguments.  Imported
    # lazily so the UI (and --mock) works before service.py exists.  Long-running
    # functions (import_paths, export_report) receive ``progress=<callable(job_fields)>``
    # if their signature accepts it; see _run_job.

    def _svc(self):
        """The backend service object (dronedog.service.default_service())."""
        try:
            import importlib
            mod = importlib.import_module("dronedog.service")
        except ModuleNotFoundError as e:
            if e.name in ("dronedog.service", "dronedog"):
                raise RuntimeError("DroneDog backend not available (dronedog/service.py is missing). "
                                   "Start with --mock for the demo dataset.") from e
            raise
        factory = getattr(mod, "default_service", None)
        return factory() if callable(factory) else mod

    def _forward(self, name: str, *args, **kwargs):
        fn = getattr(self._svc(), name, None)
        if fn is None:
            raise RuntimeError(f"dronedog.service.{name}() is not implemented yet")
        return fn(*args, **kwargs)

    def _run_job(self, kind: str, stages: list[str], name: str, *args) -> dict:
        """Run service.<name>(*args, progress=cb) on a worker thread as a UI job.

        If the service function returns {"job_id"} itself (and pushes progress on its own
        through the sink below), that is passed straight through."""
        import inspect

        fn = getattr(self._svc(), name, None)
        if fn is None:
            raise RuntimeError(f"dronedog.service.{name}() is not implemented yet")
        params = inspect.signature(fn).parameters
        job = self._jobs.add(_jobs.new_job(kind, stages))
        jid = job["job_id"]

        def progress(fields: dict | None = None, **kw) -> None:
            upd = dict(fields or {}, **kw)
            msg = upd.pop("log", None)
            if upd:
                self._jobs.update(jid, **upd)
            if msg:
                self._jobs.log(jid, msg if isinstance(msg, str) else json.dumps(msg))

        def run() -> None:
            try:
                kwargs = {"progress": progress} if "progress" in params else {}
                res = fn(*args, **kwargs)
                if isinstance(res, dict) and "job_id" in res and "progress" not in params:
                    return
                self._jobs.finish(jid, result=res if isinstance(res, dict) else {"value": res})
            except Exception as e:  # noqa: BLE001
                self._jobs.finish(jid, error=f"{type(e).__name__}: {e}")

        threading.Thread(target=run, name=f"dronedog-{kind}", daemon=True).start()
        return {"job_id": jid}

    def case_new(self, meta: dict) -> dict:
        """meta: {name, case_number, examiner, agency, location?} -> case_info()"""
        info = self._forward("case_new", meta)
        self._remember(info)
        self._log_network("case.open")
        return info

    def case_open(self, path: str) -> dict:
        info = self._forward("case_open", path)
        self._remember(info)
        self._log_network("case.open")
        return info

    def _remember(self, info) -> None:
        try:
            case = (info or {}).get("case") or {}
            if info.get("root"):
                self._recent.touch(str(info["root"]), case.get("name"), case.get("case_number"))
        except Exception:  # noqa: BLE001  - recent list is a convenience only
            pass

    def case_info(self) -> dict:
        try:
            return self._forward("case_info")
        except RuntimeError:
            return {"open": False}

    def import_paths(self, paths: list[str], options: dict | None = None) -> dict:
        """Ingest + extract + detect + parse every path -> {"job_id"} (job shape: jobs.py).

        service.import_paths(paths, progress_cb) runs the pipeline on its own thread and
        reports {"job_id","stage","ui_stage","current","total","progress","message"};
        this method maps those events onto a UI job and attaches the artifact rows."""
        svc = self._svc()
        paths = [str(p) for p in (paths or [])]
        if not paths:
            raise ValueError("No paths to import")
        job = self._jobs.add(_jobs.new_job("import", _jobs.IMPORT_STAGES))
        jid = job["job_id"]
        order = list(_jobs.IMPORT_STAGES)
        state = {"svc_job": None, "last_rows": 0.0, "finalizing": False}

        def refresh_rows(force=False):
            now = time.monotonic()
            if not force and now - state["last_rows"] < 0.8:
                return
            state["last_rows"] = now
            try:
                rows = svc.list_artifacts()
                counts = svc.counts() if hasattr(svc, "counts") else {}
                self._jobs.update(jid, artifacts=rows, counts=counts)
            except Exception:  # noqa: BLE001 - listing is best effort while the job writes
                pass

        def finalize(svc_jid):
            st = {}
            for _ in range(600):
                st = svc.job_status(svc_jid) if svc_jid else {}
                if st.get("state") not in ("running", "queued"):
                    break
                time.sleep(0.1)
            refresh_rows(force=True)
            summary = st.get("summary") or {}
            for e in summary.get("errors") or []:
                self._jobs.log(jid, f"{e.get('source') or e.get('stage')}: {e.get('error')}", "error")
            if st.get("state") == "failed" or st.get("error"):
                self._jobs.finish(jid, error=st.get("error") or "import failed")
            else:
                self._jobs.update(jid, message=st.get("message") or "Import complete")
                self._jobs.finish(jid, result=st.get("result") or {"summary": summary})

        def cb(ev: dict) -> None:
            stage = ev.get("ui_stage") or ev.get("stage")
            state["svc_job"] = ev.get("job_id") or state["svc_job"]
            if stage == "done":
                if not state["finalizing"]:
                    state["finalizing"] = True
                    threading.Thread(target=finalize, args=(state["svc_job"],), daemon=True).start()
                return

            def apply(j):
                if stage in order:
                    i = order.index(stage)
                    for k, s in enumerate(j["stages"]):
                        if k < i:
                            s["state"] = "done"
                        elif k == i:
                            s["state"] = "running"
                            if ev.get("current") is not None:
                                s["done"] = ev["current"]
                            if ev.get("total") is not None:
                                s["total"] = ev["total"]
                    p = ev.get("progress")
                    j["progress"] = round((i + (p if p is not None else 0.0)) / len(order), 4)
                    j["stage"] = stage
                if ev.get("message"):
                    j["message"] = ev["message"]
                    j["current_path"] = ev.get("path") or None
            self._jobs.mutate(jid, apply)
            refresh_rows()

        kw = {"hash_evidence": False} if (options or {}).get("skip_hash") else {}
        res = svc.import_paths(paths, cb, **kw)
        state["svc_job"] = state["svc_job"] or (res or {}).get("job_id")
        return {"job_id": jid}

    def list_evidence(self) -> list[dict]:
        """Rows: {id, label, src_path, kind, subkind, size, sha256, sha1, md5, storage,
        hash_scope, added_utc, examiner, artifact_count, verified}"""
        return self._forward("list_evidence")

    def list_artifacts(self) -> list[dict]:
        """Rows: {id, name, evidence_id, evidence_label, rel_path, inner_path, offset, size,
        sha256, sha1, detected_kind, detected_subtype, confidence, reasons, encrypted, status,
        error, deleted, deleted_sources, sources: [...], flight_ids}"""
        return self._forward("list_artifacts")

    def list_flights(self) -> list[dict]:
        """Rows: {id, artifact_id, artifact_name, evidence_label, source, adapter, start_utc,
        duration_s, distance_m, max_alt_m, max_speed_ms, frame_count, aircraft_model,
        aircraft_sn, home_lat, home_lon, bbox, encrypted_unreadable, warnings, links}"""
        return self._forward("list_flights")

    def get_flight(self, flight_id: int, max_points: int = 5000) -> dict:
        """{"flight", "details", "frames" (decimated for display), "events", "decimation", "links", "warnings"}"""
        res = self._forward("get_flight", int(flight_id), int(max_points or 0))
        if isinstance(res, dict) and res.get("frames") and "decimation" not in res:
            from dronedog.ui.decimate import decimate
            res["frames"], res["decimation"] = decimate(res["frames"], int(max_points or 0))
        return res

    def get_track_geojson(self, flight_ids: list[int]) -> dict:
        return self._forward("get_track_geojson", [int(x) for x in flight_ids or []])

    def _report_tile_sources(self) -> list[dict]:
        """Basemaps for report maps, best first: online HD imagery when online maps are on
        (through the monitored choke point), then every imported offline pack."""
        out = []
        if self._net.online:
            for sid in ("esri_imagery", "usgs_imagery", "osm"):
                src = ONLINE_SOURCES[sid]
                out.append({"id": ONLINE_PREFIX + sid, "name": src["name"], "attribution": src["attribution"],
                            "fetch": (lambda z, x, y, _id=ONLINE_PREFIX + sid: self.tile(_id, z, x, y))})
        for p in self._maps.list():
            out.append({"id": p["id"], "name": p.get("name"), "attribution": p.get("attribution") or p.get("name"),
                        "fetch": (lambda z, x, y, _id=p["id"]: self._maps.tile(_id, z, x, y))})
        return out

    def export_report(self, flight_ids: list[int], formats: list[str]) -> dict:
        """-> {"job_id"}; job.result = {"export_dir", "files": [{format, path, size, sha256}]}"""
        try:
            self._svc().report_tile_sources = self._report_tile_sources
        except Exception:  # noqa: BLE001
            pass
        return self._run_job("export", _jobs.EXPORT_STAGES, "export_report",
                             [int(x) for x in flight_ids or []], list(formats or []))

    def verify_custody(self) -> dict:
        return self._forward("verify_custody")

    def list_custody(self) -> list[dict]:
        return self._forward("list_custody")


def jsonable(obj: Any) -> Any:
    """Round-trip through JSON (used by tests to prove the bridge contract)."""
    return json.loads(json.dumps(obj))


__all__ = ["Api", "RecentCases", "default_basemap_dir", "jsonable"]
