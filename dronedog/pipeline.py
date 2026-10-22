"""Bulk pipeline: ingest -> extract -> detect -> parse (C7 import_paths, ingest half).

    job = import_paths(case, [r"E:\\", r"D:\\img\\DF004.E01", r"C:\\x\\flight_logs.zip"], progress_cb)
    job.status()   # {"job_id","state","stage","current","total","message","summary",...}
    job.cancel(); job.wait()

Progress events passed to ``progress_cb(event)``::

    {"job_id", "stage": "ingest"|"extract"|"detect"|"parse"|"done", "current", "total", "message"}

One bad source or artifact never stops the batch: evidence refusals and walk
errors are reported in the summary; artifacts that fail extraction/detection/
parsing are marked ``failed`` with the error and the batch continues.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from dronedog import __version__, core_bridge
from dronedog.adapters import AdapterUnavailable, ParseContext, flight_source, registry
from dronedog.case.case import Case
from dronedog.case.custody import canonical, io_ref
from dronedog.case.hashing import Cancelled, sha256_bytes
from dronedog.ingest import image as img_mod
from dronedog.ingest.candidates import CandidatePolicy
from dronedog.ingest.sources import Source, expand_inputs
from dronedog.ingest.walker import Walker

ProgressCb = Callable[[dict], None]


@dataclass
class ImportOptions:
    copy: bool | None = None            # None = default policy (copy small files/zips, reference images)
    policy: CandidatePolicy | None = None
    verify_media: bool = True            # E01: hash the media stream and compare with stored hashes
    hash_evidence: bool = True           # False: examiner option for testing; evidence referenced, not hashed
    detect: bool = True
    parse: bool = True
    retry_failed: bool = False
    examiner: str | None = None


class _Reporter:
    def __init__(self, job_id: str, cb: ProgressCb | None):
        self.job_id = job_id
        self.cb = cb
        self.last: dict = {}
        self._t = 0.0

    def __call__(self, stage: str, current: int | None = None, total: int | None = None,
                 message: str = "", force: bool = False) -> None:
        ev = {"job_id": self.job_id, "stage": stage, "current": current, "total": total, "message": message}
        # never drop the first/last event of a stage; throttle the rest to 10/s
        force = force or stage != self.last.get("stage") or (total is not None and current == total)
        self.last = ev
        now = time.monotonic()
        if self.cb and (force or now - self._t >= 0.1):
            self._t = now
            try:
                self.cb(ev)
            except Exception:  # noqa: BLE001 - a broken UI callback must not kill the job
                pass


def _mb(n: int | None) -> str:
    return f"{(n or 0) / 1e6:,.1f} MB"


# -- stages ------------------------------------------------------------------------------
def ingest_sources(case: Case, sources: list[Source], rep: _Reporter, cancel, opts: ImportOptions,
                   summary: dict) -> None:
    """Evidence intake + walk + extract for each source."""
    for si, src in enumerate(sources, 1):
        if cancel.is_set():
            raise Cancelled()
        label = f"[{si}/{len(sources)}] {src.label}"
        rep("ingest", 0, src.size or None, f"{label}: " + ("hashing" + (" + copying" if case.default_copy(src) else "")
            if opts.hash_evidence else "registering (hashing skipped)"), force=True)
        t0 = time.perf_counter()
        try:
            ev = case.add_evidence(
                src, copy=opts.copy, examiner=opts.examiner, cancel=cancel, verify_media=opts.verify_media,
                hash_evidence=opts.hash_evidence,
                progress=lambda st, n, tot, _l=label: rep("ingest", n, tot, f"{_l}: {st} {_mb(n)} / {_mb(tot)}"))
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            summary["errors"].append({"source": str(src.path), "stage": "ingest", "error": f"{type(e).__name__}: {e}"})
            rep("ingest", None, None, f"{label}: REFUSED - {e}", force=True)
            continue
        dt = time.perf_counter() - t0
        item = {"evidence_id": ev["id"], "label": src.label, "kind": src.kind, "subkind": src.subkind,
                "storage": ev["storage"], "size": ev["size"], "sha256": ev["sha256"],
                "hash_seconds": round(dt, 3), "duplicate_of": ev.get("duplicate_of")}
        if ev.get("size") and dt > 0:
            item["hash_mb_s"] = round(ev["size"] / 1e6 / dt, 1)
        summary["evidence"].append(item)
        if ev.get("duplicate_of"):
            rep("ingest", None, None, f"{label}: already in case as evidence #{ev['duplicate_of']}", force=True)
            continue
        extract_evidence(case, ev, src, rep, cancel, opts, item, label)


def extract_evidence(case: Case, ev: dict, src: Source, rep: _Reporter, cancel, opts: ImportOptions,
                     item: dict, label: str = "") -> None:
    walker = Walker(opts.policy, tmpdir=case.tmp_dir, cancel=cancel,
                    progress=lambda msg, n: rep("extract", n, None, f"{label}: {msg}"))
    files = case.evidence_read_paths(ev) if src.kind not in ("folder", "sd") or src.subkind == "raw_device" \
        else [Path(ev["src_path"])]
    new = dup = failed = 0
    t0 = time.perf_counter()
    try:
        for cand in walker.walk(src, files):
            prog = None
            if (cand.size or 0) >= 32 * 1024 * 1024:
                def prog(n, _c=cand):
                    rep("extract", walker.stats.candidates, None,
                        f"{label}: extracting {_c.name} {_mb(n)} / {_mb(_c.size)}")
            r = case.extract(ev, cand, cancel, progress=prog)
            if r.get("failed"):
                failed += 1
            elif r["new"]:
                new += 1
            else:
                dup += 1
            rep("extract", walker.stats.candidates, None,
                f"{label}: {walker.stats.candidates} candidates ({new} new, {dup} duplicate, {failed} failed), "
                f"{walker.stats.files_seen} files examined")
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        walker.stats.error("walk", f"{type(e).__name__}: {e}")
        item.setdefault("errors", []).append({"stage": "extract", "error": f"{type(e).__name__}: {e}",
                                              "trace": traceback.format_exc(limit=4)})
    dt = time.perf_counter() - t0
    st = walker.stats.as_dict()
    item.update({"walk_seconds": round(dt, 3), "files_seen": st["files_seen"], "candidates": st["candidates"],
                 "new_artifacts": new, "duplicate_artifacts": dup, "failed_extractions": failed,
                 "deleted_candidates": st["deleted_candidates"], "walk_errors": len(st["errors"])})
    meta = {"walk": st}
    if walker.ios_manifests:
        meta["ios_backups"] = walker.ios_manifests
    if getattr(walker, "image_meta", None):
        meta["image"] = walker.image_meta
    case.update_evidence_meta(ev["id"], **meta)
    if src.kind in ("folder", "sd") and src.subkind != "raw_device":
        case.finalize_folder_evidence(ev["id"], st)
    params = {"evidence_id": ev["id"], "summary": True, "files_seen": st["files_seen"],
              "candidates": st["candidates"], "new_artifacts": new, "duplicates": dup, "failed": failed,
              "deleted_candidates": st["deleted_candidates"], "containers": st["containers"][:200],
              "errors": st["errors"][:50], "skipped": st["skipped"],
              "policy": _policy_params(walker.policy)}
    if src.kind == "image":
        params["libraries"] = img_mod.lib_versions()
    case.log("artifact.extract", component="ingest.walk",
             inputs=[io_ref(ev["src_path"], case.store.one("SELECT sha256 FROM evidence WHERE id=?",
                                                           (ev["id"],))["sha256"])],
             params=params)


def _policy_params(p: CandidatePolicy) -> dict:
    return {"name_globs": list(p.name_globs), "deleted_name_globs": list(p.deleted_name_globs),
            "log_dirs": list(p.log_dirs), "sniff": p.sniff, "sniff_max_size": p.sniff_max_size,
            "max_container_depth": p.max_container_depth}


# Detected kinds that have a parser adapter (see dronedog/adapters/__init__.py).
PARSEABLE_KINDS = ("dji_txt", "dji_dat", "dji_app_tips")


def _python_detect(path) -> tuple[dict, str, str] | None:
    """Python-only detectors tried when the core says "unknown" (formats the core does
    not know). Returns (detection, component, version) or None."""
    from .adapters import app_tips_json
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
        d = app_tips_json.sniff(path, head)
    except Exception:  # noqa: BLE001 - a fallback detector must never fail the stage
        return None
    if d:
        return d, "detect.python.app_tips", app_tips_json.ADAPTER_VERSION
    return None


def detect_artifacts(case: Case, rep: _Reporter, cancel, summary: dict, retry_failed: bool = False) -> None:
    statuses = ("pending", "failed") if retry_failed else ("pending",)
    comp, ver = core_bridge.detector_component()
    rows = case.store.query(
        f"SELECT * FROM artifacts WHERE status IN ({','.join('?' * len(statuses))}) AND sha256 IS NOT NULL "
        "ORDER BY id", statuses)

    def needs(a):
        if a["detected_kind"] is None or a["status"] == "failed":
            return True
        # detected earlier by the python fallback, and the core is available now: re-detect
        dj = a["detection_json"] or ""
        return comp == "detect.core" and '"detector": "detect.core"' not in dj             and '"detector": "detect.python.' not in dj
    rows = [a for a in rows if needs(a)]
    for i, a in enumerate(rows, 1):
        if cancel.is_set():
            raise Cancelled()
        rep("detect", i, len(rows), f"detect {a['name']}")
        path = case.artifact_path(a)
        try:
            det = core_bridge.detect(path)
        except Exception as e:  # noqa: BLE001
            case.set_status(a["id"], "failed", f"detect failed: {type(e).__name__}: {e}")
            summary["detect_failed"] += 1
            continue
        dcomp, dver = comp, ver
        if det.get("kind") == "unknown":
            py = _python_detect(path)
            if py is not None:
                det = dict(py[0], reasons=list(py[0]["reasons"]) + [f"{comp} returned kind 'unknown'"])
                dcomp, dver = py[1], py[2]
        det = dict(det, detector=dcomp, detector_version=dver)
        status = "pending" if det.get("kind") in PARSEABLE_KINDS else "unsupported"
        case.set_detection(a["id"], det, status, None if status == "pending" else "no adapter for detected kind")
        case.log("artifact.detect", component=dcomp, tool_version=f"{__version__} / {dver}",
                 inputs=[io_ref(a["rel_path"], a["sha256"], artifact_id=a["id"])],
                 params={"artifact_id": a["id"], "result": det, "status": status})
        summary["detected"] += 1


def parse_artifacts(case: Case, rep: _Reporter, cancel, summary: dict) -> None:
    rows = case.store.query("SELECT * FROM artifacts WHERE status='pending' AND detected_kind IN "
                            f"({','.join('?' * len(PARSEABLE_KINDS))}) ORDER BY id", PARSEABLE_KINDS)
    for i, a in enumerate(rows, 1):
        if cancel.is_set():
            raise Cancelled()
        rep("parse", i, len(rows), f"parse {a['name']}")
        parse_one(case, a, summary, cancel)


def parse_one(case: Case, a: dict, summary: dict, cancel=None) -> dict | None:
    import json
    det = json.loads(a["detection_json"] or "{}")
    det = {k: det.get(k) for k in ("kind", "subtype", "version", "encrypted", "confidence", "reasons")} | \
        {"kind": a["detected_kind"], "subtype": a["detected_subtype"]}
    adapter, score = registry.pick(det)
    if adapter is None:
        case.set_status(a["id"], "unsupported", "no adapter accepts this detection")
        summary["unsupported"] += 1
        return None
    sources = case.store.query("SELECT * FROM artifact_sources WHERE artifact_id=?", (a["id"],))
    ctx = ParseContext(artifact=a, detection=det, case_root=case.root, sources=sources, cancel=cancel)
    path = case.artifact_path(a)
    t0 = time.perf_counter()
    try:
        result = adapter.parse(path, ctx)
    except AdapterUnavailable as e:
        case.set_status(a["id"], "pending", f"{adapter.name}: {e}")
        summary["pending_no_core"] += 1
        return None
    except Exception as e:  # noqa: BLE001
        err = f"{adapter.name}: {type(e).__name__}: {e}"
        case.set_status(a["id"], "failed", err)
        case.log("artifact.parse", component=f"adapter.{adapter.name}",
                 inputs=[io_ref(a["rel_path"], a["sha256"], artifact_id=a["id"])],
                 params={"artifact_id": a["id"], "adapter": adapter.name, "error": err})
        summary["parse_failed"] += 1
        return None
    dt = time.perf_counter() - t0
    try:
        digest = sha256_bytes(canonical(result).encode("utf-8"))
        src = flight_source(adapter.name, det, sources)
        stored = case.store_flight(a["id"], result, src, digest)
        try:  # derived examiner summary, stored beside (not inside) the hashed parse result
            from .analysis.summary import summarize_result
            case.store.execute("UPDATE flights SET analysis_json=? WHERE id=?",
                               (json.dumps(summarize_result(result, src), default=str), stored["flight_id"]))
        except Exception:  # noqa: BLE001 - the service recomputes it on demand
            pass
    except Exception as e:  # noqa: BLE001
        err = f"storing parse result failed: {type(e).__name__}: {e}"
        case.set_status(a["id"], "failed", err)
        summary["parse_failed"] += 1
        return None
    status = "encrypted" if result.get("encrypted_unreadable") else "parsed"
    case.set_status(a["id"], status, None)
    case.log("artifact.parse", component=f"adapter.{adapter.name}",
             tool_version=f"{__version__} / {result.get('adapter_version')}",
             inputs=[io_ref(a["rel_path"], a["sha256"], artifact_id=a["id"])],
             outputs=[{"flight_id": stored["flight_id"], "sha256": digest, "kind": "parse_result",
                       "frame_count": stored["frame_count"]}],
             params={"artifact_id": a["id"], "adapter": adapter.name, "adapter_version": result.get("adapter_version"),
                     "accept_score": score, "source": src, "status": status,
                     "records_total": result.get("records_total"), "records_decoded": result.get("records_decoded"),
                     "records_failed": result.get("records_failed"), "events": len(result.get("events") or []),
                     "warnings": (result.get("warnings") or [])[:20], "replaced_flights": stored["replaced"],
                     "parse_seconds": round(dt, 3),
                     "result_digest_basis": "sha256 of canonical JSON (sorted keys, compact) of the ParseResult"})
    summary["parsed" if status == "parsed" else "encrypted"] += 1
    return stored


# -- orchestration ---------------------------------------------------------------------------
def _correlate(case: Case, rep: _Reporter, summary: dict) -> None:
    """Link records of the same physical flight across sources. Never fails the job."""
    from .analysis.correlate import correlate
    rep("correlate", None, None, "linking aircraft, controller and app records", force=True)
    try:
        summary["links"] = len(correlate(case))
    except Exception as e:  # noqa: BLE001
        summary["errors"].append({"stage": "correlate", "error": f"{type(e).__name__}: {e}"})


def _new_summary() -> dict:
    return {"evidence": [], "errors": [], "detected": 0, "detect_failed": 0, "parsed": 0, "encrypted": 0,
            "parse_failed": 0, "unsupported": 0, "pending_no_core": 0}


def run_import(case: Case, paths: Iterable[str], progress_cb: ProgressCb | None = None, cancel=None,
               options: ImportOptions | None = None, job_id: str | None = None) -> dict:
    """Synchronous ingest -> extract -> detect -> parse. Returns the summary dict."""
    opts = options or ImportOptions()
    cancel = cancel or threading.Event()
    job_id = job_id or uuid.uuid4().hex[:12]
    rep = _Reporter(job_id, progress_cb)
    summary = _new_summary()
    paths = [str(p) for p in paths]
    case.log("job.start", component="pipeline", actor="examiner",
             params={"job_id": job_id, "paths": paths, "options": {"copy": opts.copy, "detect": opts.detect,
                                                                   "parse": opts.parse,
                                                                   "verify_media": opts.verify_media},
                     "core": core_bridge.core_version(), "core_error": core_bridge.core_import_error()})
    t0 = time.perf_counter()
    state = "done"
    try:
        sources = []
        for p in paths:
            try:
                sources.extend(expand_inputs([p]))
            except Exception as e:  # noqa: BLE001
                summary["errors"].append({"source": p, "stage": "classify", "error": f"{type(e).__name__}: {e}"})
        summary["sources"] = [{"path": str(s.path), "kind": s.kind, "subkind": s.subkind,
                               "segments": len(s.files)} for s in sources]
        ingest_sources(case, sources, rep, cancel, opts, summary)
        if opts.detect:
            detect_artifacts(case, rep, cancel, summary, opts.retry_failed)
        if opts.parse:
            parse_artifacts(case, rep, cancel, summary)
            _correlate(case, rep, summary)
    except Cancelled:
        state = "cancelled"
    except Exception as e:  # noqa: BLE001
        state = "failed"
        summary["errors"].append({"stage": "job", "error": f"{type(e).__name__}: {e}",
                                  "trace": traceback.format_exc(limit=6)})
    summary["state"] = state
    summary["seconds"] = round(time.perf_counter() - t0, 3)
    case.log("job.end", component="pipeline", params={"job_id": job_id, "state": state,
                                                      **{k: v for k, v in summary.items()
                                                         if k not in ("evidence", "sources")},
                                                      "evidence_ids": [e["evidence_id"] for e in summary["evidence"]]})
    rep("done", None, None, f"{state}: {len(summary['evidence'])} evidence, {summary['parsed']} parsed, "
        f"{summary['encrypted']} encrypted, {summary['parse_failed']} failed", force=True)
    return summary


def process_pending(case: Case, progress_cb: ProgressCb | None = None, cancel=None,
                    retry_failed: bool = False) -> dict:
    """`case run`: detect + parse everything still pending (e.g. after the core is built)."""
    cancel = cancel or threading.Event()
    rep = _Reporter(uuid.uuid4().hex[:12], progress_cb)
    summary = _new_summary()
    try:
        detect_artifacts(case, rep, cancel, summary, retry_failed)
        if retry_failed:
            for a in case.store.query("SELECT id FROM artifacts WHERE status='failed' AND detected_kind IN "
                                      f"({','.join('?' * len(PARSEABLE_KINDS))})", PARSEABLE_KINDS):
                case.set_status(a["id"], "pending", None)
        parse_artifacts(case, rep, cancel, summary)
        _correlate(case, rep, summary)
        summary["state"] = "done"
    except Cancelled:
        summary["state"] = "cancelled"
    rep("done", None, None, f"{summary['parsed']} parsed, {summary['encrypted']} encrypted, "
        f"{summary['parse_failed']} failed, {summary['pending_no_core']} waiting for core", force=True)
    return summary


class ImportJob:
    """Runs run_import on a worker thread. Thread-safe status()/cancel()."""

    def __init__(self, case: Case, paths, progress_cb: ProgressCb | None = None,
                 options: ImportOptions | None = None, job_id: str | None = None):
        self.id = job_id or uuid.uuid4().hex[:12]
        self.case = case
        self.paths = [str(p) for p in paths]
        self.options = options or ImportOptions()
        self._cb = progress_cb
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.state = "queued"
        self.last_event: dict = {}
        self.summary: dict | None = None
        self.error: str | None = None
        self.started_utc = None
        self._thread = threading.Thread(target=self._run, name=f"dronedog-import-{self.id}", daemon=True)

    def _on_progress(self, ev: dict) -> None:
        with self._lock:
            self.last_event = ev
        if self._cb:
            self._cb(ev)

    def _run(self) -> None:
        self.state = "running"
        try:
            self.summary = run_import(self.case, self.paths, self._on_progress, self._cancel, self.options, self.id)
            self.state = self.summary.get("state", "done")
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.state = "failed"

    def start(self) -> "ImportJob":
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout: float | None = None) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            ev = dict(self.last_event)
        return {"job_id": self.id, "state": self.state, "stage": ev.get("stage"), "current": ev.get("current"),
                "total": ev.get("total"), "message": ev.get("message"), "summary": self.summary,
                "error": self.error}


JOBS: dict[str, ImportJob] = {}


def import_paths(case: Case, paths, progress_cb: ProgressCb | None = None,
                 options: ImportOptions | None = None) -> ImportJob:
    """C7 import_paths: start a background ingest+extract+detect+parse job and return it."""
    job = ImportJob(case, paths, progress_cb, options)
    JOBS[job.id] = job
    return job.start()


def get_job(job_id: str) -> ImportJob | None:
    return JOBS.get(job_id)
