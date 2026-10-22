"""Background job registry + progress push for the UI bridge (C7).

A job is a plain dict so it can be pushed to JS unchanged::

    {
      "job_id": "imp-3f2a91c0",
      "kind": "import" | "export",
      "state": "running" | "done" | "failed" | "cancelled",
      "stage": "hashing" | "extracting" | "detecting" | "parsing" | "rendering" | "done",
      "stages": [{"key": "hashing", "label": "Hashing", "done": 3, "total": 5,
                  "state": "pending" | "running" | "done"}],
      "progress": 0.0 .. 1.0,
      "message": "Hashing Item 5 - microSD.E01 (1.2 / 3.4 GB)",
      "current_path": str | None,
      "artifacts": [ <artifact rows, same shape as list_artifacts()> ],   # import jobs
      "counts": {"evidence": n, "artifacts": n, "parsed": n, "encrypted": n,
                 "unsupported": n, "failed": n, "deleted": n, "flights": n, "duplicates": n},
      "log": [ {"ts_utc": str, "level": "info"|"warn"|"error", "message": str} ],  # last 200
      "started_utc": str, "finished_utc": str | None,
      "result": dict | None,   # export: {"export_dir", "files": [...]}; import: {"flight_ids": [...]}
      "error": str | None,
    }

``JobRegistry.push`` is throttled so a fast pipeline does not flood the webview.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

STAGE_LABELS = {
    "hashing": "Hashing",
    "extracting": "Extracting",
    "detecting": "Detecting",
    "parsing": "Parsing",
    "rendering": "Rendering",
    "writing": "Writing",
}

IMPORT_STAGES = ["hashing", "extracting", "detecting", "parsing"]
EXPORT_STAGES = ["rendering", "writing", "hashing"]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def new_job(kind: str, stages: list[str], job_id: str | None = None) -> dict:
    return {
        "job_id": job_id or f"{kind[:3]}-{uuid.uuid4().hex[:8]}",
        "kind": kind,
        "state": "running",
        "stage": stages[0] if stages else None,
        "stages": [{"key": s, "label": STAGE_LABELS.get(s, s.title()), "done": 0, "total": 0,
                    "state": "pending"} for s in stages],
        "progress": 0.0,
        "message": "",
        "current_path": None,
        "artifacts": [],
        "counts": {},
        "log": [],
        "started_utc": utc_now(),
        "finished_utc": None,
        "result": None,
        "error": None,
    }


class JobRegistry:
    """Thread-safe store of job dicts with a throttled push sink."""

    def __init__(self, sink: Callable[[dict], None] | None = None, min_interval: float = 0.2):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._sink = sink
        self._min_interval = min_interval
        self._last_push: dict[str, float] = {}
        self._pending: dict[str, threading.Timer] = {}

    def set_sink(self, sink: Callable[[dict], None] | None) -> None:
        self._sink = sink

    def add(self, job: dict) -> dict:
        with self._lock:
            self._jobs[job["job_id"]] = job
        self.push(job["job_id"], force=True)
        return job

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(job_id)
            return copy.deepcopy(j) if j else None

    def update(self, job_id: str, force: bool = False, **fields) -> None:
        with self._lock:
            j = self._jobs[job_id]
            j.update(fields)
        self.push(job_id, force=force)

    def mutate(self, job_id: str, fn: Callable[[dict], None], force: bool = False) -> None:
        with self._lock:
            fn(self._jobs[job_id])
        self.push(job_id, force=force)

    def log(self, job_id: str, message: str, level: str = "info") -> None:
        def _f(j):
            j["log"].append({"ts_utc": utc_now(), "level": level, "message": message})
            del j["log"][:-200]
        self.mutate(job_id, _f)

    def finish(self, job_id: str, result: dict | None = None, error: str | None = None) -> None:
        def _f(j):
            j["state"] = "failed" if error else "done"
            j["error"] = error
            j["result"] = result
            j["finished_utc"] = utc_now()
            if not error:
                j["progress"] = 1.0
                j["stage"] = "done"
                for s in j["stages"]:
                    s["state"] = "done"
        self.mutate(job_id, _f, force=True)

    # -- push ---------------------------------------------------------------------------
    def push(self, job_id: str, force: bool = False) -> None:
        if not self._sink:
            return
        now = time.monotonic()
        with self._lock:
            last = self._last_push.get(job_id, 0.0)
            if not force and now - last < self._min_interval:
                if job_id not in self._pending:
                    t = threading.Timer(self._min_interval - (now - last), self._deferred, (job_id,))
                    t.daemon = True
                    self._pending[job_id] = t
                    t.start()
                return
            self._last_push[job_id] = now
            snap = copy.deepcopy(self._jobs.get(job_id))
        if snap is not None:
            try:
                self._sink(snap)
            except Exception:  # noqa: BLE001  - a closed window must not kill the job
                pass

    def _deferred(self, job_id: str) -> None:
        with self._lock:
            self._pending.pop(job_id, None)
        self.push(job_id, force=True)
