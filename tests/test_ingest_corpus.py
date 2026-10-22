"""Real-data smoke test: walk every FINISHED CFReDS download in corpus/raw/cfreds.

A file counts as finished when it is not a ``.part``, the fetch log records it as
``ok <size>`` with the same size (or an imager .md5/.sha1 sidecar sits next to it and
its size is stable for 2 s), and it is not a Google-Drive HTML error page.

Skipped entirely when the corpus is absent. Run with ``-s`` to see the per-device
table, or directly: ``python tests/test_ingest_corpus.py``.
"""
from __future__ import annotations

import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CFREDS = ROOT / "corpus" / "raw" / "cfreds"
FETCH_LOG = ROOT / "corpus" / "raw" / "cfreds_fetch.log"
EVIDENCE_EXT = re.compile(r"\.(001|e01|ex01|img|dd|bin|zip)$", re.I)


def _fetch_log_sizes() -> dict[str, int]:
    out: dict[str, int] = {}
    lines: list[str] = []
    for log in sorted(FETCH_LOG.parent.glob("cfreds_fetch*.log")):
        lines += log.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    cur = None
    for line in lines:
        m = re.match(r"\[\d+/\d+\]\s+(?:get\s+|skip\s+)?(.+?)(?:\s+\([\d.,]+ [KMGT]?B\))?\s*$", line)
        if m:
            cur = m.group(1).strip().replace("\\", "/")
            continue
        m = re.match(r"\s+ok ([\d,]+)", line)
        if m and cur:
            out[cur] = int(m.group(1).replace(",", ""))
            cur = None
    return out


def finished_files() -> tuple[list[Path], list[tuple[Path, str]]]:
    ok, skipped = [], []
    if not CFREDS.exists():
        return ok, skipped
    logged = _fetch_log_sizes()
    for p in sorted(CFREDS.rglob("*")):
        if not p.is_file() or not EVIDENCE_EXT.search(p.name):
            continue
        rel = p.relative_to(CFREDS).as_posix()
        size = p.stat().st_size
        with open(p, "rb") as fh:
            head = fh.read(64)
        if head.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            skipped.append((p, "HTML error page instead of data (Drive quota)"))
            continue
        if rel in logged:
            if logged[rel] == size:
                ok.append(p)
            else:
                skipped.append((p, f"size {size} != logged {logged[rel]}"))
            continue
        side = [p.with_name(p.name + e) for e in (".md5", ".sha1", ".MD5", ".SHA1")] + \
               [p.with_suffix(e) for e in (".md5", ".sha1")]
        if any(s.exists() for s in side):
            time.sleep(2)
            if p.stat().st_size == size:
                ok.append(p)
                continue
            skipped.append((p, "still growing"))
        else:
            skipped.append((p, "not finished (no fetch-log entry / sidecar)"))
    return ok, skipped


def scan_one(path: Path) -> dict:
    from dronedog.case import hashing
    from dronedog.ingest import Walker, expand_inputs
    res = {"path": path.relative_to(CFREDS).as_posix(), "device": path.relative_to(CFREDS).parts[0],
           "types": Counter(), "deleted": 0, "candidates": 0, "bytes": 0, "errors": 0}
    srcs = expand_inputs([path])
    t = time.perf_counter()
    total = 0
    for s in srcs:
        whole, _ = hashing.hash_files(s.files, hashing.IMAGE_ALGOS) if s.files else (None, None)
        total += whole.size if whole else 0
    res["hash_s"] = time.perf_counter() - t
    res["size"] = total
    t = time.perf_counter()
    for s in srcs:
        res["kind"] = f"{s.kind}/{s.subkind or '-'}"
        w = Walker()
        for c in w.walk(s):
            res["candidates"] += 1
            res["deleted"] += int(c.deleted)
            if c.sniff:
                typ = f"{c.sniff['kind']}/{c.sniff['subtype']}"
            elif c.name.upper().endswith(".DAT"):
                typ = "DAT(name-only)"
            elif c.name.lower().endswith(".txt"):
                typ = "TXT(name-only)"
            else:
                typ = "other"
            res["types"][typ] += 1
            try:
                with c.opener() as fh:
                    r = hashing.hash_stream(fh)     # = extraction cost minus the disk write
                res["bytes"] += r.size
            except Exception:  # noqa: BLE001
                res["errors"] += 1
        res["files_seen"] = w.stats.files_seen
        res["walk_errors"] = len(w.stats.errors)
        res["partitions"] = [f"{c.get('index')}:{c.get('filesystem')}@{c.get('offset')}"
                             for c in w.stats.containers if c["type"] == "partition"]
        res["ios"] = [m.get("info", {}).get("Product Type") for m in w.ios_manifests]
    res["walk_s"] = time.perf_counter() - t
    return res


def report(results: list[dict]) -> str:
    lines = []
    for r in results:
        mbs = r["size"] / 1e6 / r["hash_s"] if r["hash_s"] else 0
        types = ", ".join(f"{n} {k}" for k, n in r["types"].most_common()) or "none"
        lines.append(f"{r['device']} {r['path'].split('/', 1)[1]} [{r.get('kind')}] "
                     f"{r['size'] / 1e6:,.1f} MB: {r['candidates']} candidates ({types}), {r['deleted']} deleted; "
                     f"files {r.get('files_seen')}, read errors {r['errors']}, walk errors {r.get('walk_errors')}; "
                     f"hash {mbs:,.0f} MB/s, walk+read {r['walk_s']:.2f}s "
                     f"{('partitions ' + ' '.join(r['partitions'])) if r.get('partitions') else ''}"
                     f"{(' iOS ' + str(r['ios'])) if r.get('ios') else ''}")
    return "\n".join(lines)


@pytest.mark.skipif(not CFREDS.exists(), reason="CFReDS corpus not downloaded")
def test_cfreds_smoke():
    files, skipped = finished_files()
    if not files:
        pytest.skip("no finished CFReDS downloads yet")
    results = [scan_one(p) for p in files]
    print("\n" + report(results))
    for p, why in skipped:
        print(f"  skipped {p.relative_to(CFREDS).as_posix()}: {why}")
    assert all("candidates" in r for r in results)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    files, skipped = finished_files()
    rs = []
    for p in files:
        r = scan_one(p)
        rs.append(r)
        print(report([r]), flush=True)
    for p, why in skipped:
        print(f"  skipped {p.relative_to(CFREDS).as_posix()}: {why}")
