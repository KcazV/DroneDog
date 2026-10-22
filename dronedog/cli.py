"""Command line: ``python -m dronedog ...``

    python -m dronedog case new  <dir> --name N --examiner E [--case-number C] [--agency A]
    python -m dronedog case add  <dir> <paths...> [--copy|--reference] [--run] [--no-verify-media]
    python -m dronedog case run  <dir> [--retry-failed]
    python -m dronedog case info <dir> [--json]
    python -m dronedog custody verify <dir>
    python -m dronedog scan <paths...>          # dry run: list what the walker would extract
"""
from __future__ import annotations

import argparse
import json
import sys
import threading

from dronedog import PRODUCT_NAME, __version__


def _progress(ev: dict) -> None:
    cur, tot = ev.get("current"), ev.get("total")
    frac = f" {cur}/{tot}" if cur is not None and tot else (f" {cur}" if cur is not None else "")
    msg = f"[{ev['stage']}]{frac} {ev.get('message', '')}"
    sys.stderr.write("\r" + msg[:150].ljust(150))
    if ev["stage"] == "done":
        sys.stderr.write("\n")
    sys.stderr.flush()


def _print_summary(s: dict) -> None:
    print()
    for e in s.get("evidence", []):
        kind = e["kind"] + ("/" + e["subkind"] if e.get("subkind") else "")
        line = f"  evidence #{e['evidence_id']} {e['label']} [{kind}, {e['storage']}]"
        if e.get("duplicate_of"):
            print(line + f"  duplicate of #{e['duplicate_of']}")
            continue
        print(line + f"  files {e.get('files_seen', 0)}, candidates {e.get('candidates', 0)} "
              f"(new {e.get('new_artifacts', 0)}, dup {e.get('duplicate_artifacts', 0)}, "
              f"deleted {e.get('deleted_candidates', 0)}), walk errors {e.get('walk_errors', 0)}")
    for err in s.get("errors", []):
        print(f"  ERROR {err.get('stage')}: {err.get('source', '')} {err['error']}")
    keys = ("detected", "parsed", "encrypted", "parse_failed", "unsupported", "pending_no_core")
    print("  " + ", ".join(f"{k}={s.get(k, 0)}" for k in keys) + f"  [{s.get('state')}]")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m dronedog", description=f"{PRODUCT_NAME} {__version__}")
    ap.add_argument("--version", action="version", version=f"{PRODUCT_NAME} {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    case = sub.add_parser("case", help="case operations").add_subparsers(dest="sub", required=True)
    p = case.add_parser("new", help="create a case folder")
    p.add_argument("dir")
    p.add_argument("--name", required=True)
    p.add_argument("--examiner", required=True)
    p.add_argument("--case-number")
    p.add_argument("--agency")
    p.add_argument("--notes")

    p = case.add_parser("add", help="add evidence (files, folders, zips, images, drives) and extract artifacts")
    p.add_argument("dir")
    p.add_argument("paths", nargs="+")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--copy", dest="copy", action="store_true", default=None,
                   help="copy evidence into the case (default: copy small files/zips, reference images)")
    g.add_argument("--reference", dest="copy", action="store_false",
                   help="reference evidence in place (hash only)")
    p.add_argument("--run", action="store_true", help="also detect + parse (same as `case run` afterwards)")
    p.add_argument("--no-verify-media", action="store_true", help="E01: skip media-hash verification")
    p.add_argument("--no-hash", action="store_true",
                   help="testing only: reference evidence in place without hashing it (recorded as not hashed)")
    p.add_argument("--json", action="store_true")

    p = case.add_parser("run", help="detect + parse all pending artifacts")
    p.add_argument("dir")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--json", action="store_true")

    p = case.add_parser("info", help="case summary")
    p.add_argument("dir")
    p.add_argument("--json", action="store_true")

    cus = sub.add_parser("custody", help="custody log").add_subparsers(dest="sub", required=True)
    p = cus.add_parser("verify", help="verify the hash-chained custody log")
    p.add_argument("dir")

    p = sub.add_parser("scan", help="dry run: list flight-log candidates without creating a case")
    p.add_argument("paths", nargs="+")
    p.add_argument("--json", action="store_true")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    from dronedog.case import Case
    from dronedog.pipeline import ImportOptions, process_pending, run_import

    if a.cmd == "case" and a.sub == "new":
        c = Case.create(a.dir, name=a.name, examiner=a.examiner, agency=a.agency, case_number=a.case_number,
                        notes=a.notes)
        print(f"created case {c.id} at {c.root}")
        return 0

    if a.cmd == "case" and a.sub == "add":
        c = Case.open(a.dir)
        cancel = threading.Event()
        opts = ImportOptions(copy=a.copy, detect=a.run, parse=a.run, verify_media=not a.no_verify_media,
                             hash_evidence=not a.no_hash)
        try:
            s = run_import(c, a.paths, None if a.json else _progress, cancel, opts)
        except KeyboardInterrupt:
            cancel.set()
            return 130
        if a.json:
            print(json.dumps(s, indent=2, default=str))
        else:
            _print_summary(s)
        return 0 if not s["errors"] and s["state"] == "done" else 1

    if a.cmd == "case" and a.sub == "run":
        c = Case.open(a.dir)
        s = process_pending(c, None if a.json else _progress, retry_failed=a.retry_failed)
        if a.json:
            print(json.dumps(s, indent=2, default=str))
        else:
            _print_summary(s)
        return 0

    if a.cmd == "case" and a.sub == "info":
        c = Case.open(a.dir)
        info = c.info()
        if a.json:
            print(json.dumps(info, indent=2, default=str))
            return 0
        m = info["case"]
        print(f"{m['name']}  (#{m.get('case_number') or '-'})  examiner {m['examiner']}  id {m['id']}")
        print(f"  created {m['created_utc']}  schema v{info['schema_version']}  {info['root']}")
        print("  " + ", ".join(f"{k} {v}" for k, v in info["counts"].items()))
        print("  status: " + ", ".join(f"{k} {v}" for k, v in info["artifacts_by_status"].items()))
        print("  types:  " + ", ".join(f"{k} {v}" for k, v in info["artifacts_by_type"].items()))
        for e in c.store.query("SELECT id, label, kind, subkind, storage, size, sha256 FROM evidence ORDER BY id"):
            n = c.store.one("SELECT COUNT(*) n, SUM(deleted) d FROM artifact_sources WHERE evidence_id=?",
                            (e["id"],))
            print(f"  #{e['id']} {e['label']} [{e['kind']}/{e['subkind'] or '-'}, {e['storage']}] "
                  f"{(e['size'] or 0):,} B sha256 {(e['sha256'] or '')[:16]}  "
                  f"artifacts {n['n']} (deleted {n['d'] or 0})")
        cu = info["custody"]
        print(f"  custody: {'OK' if cu['ok'] else 'BROKEN'} ({cu['entries']} entries)"
              + ("" if cu["ok"] else f" first bad line {cu['first_bad_line']}: {cu['reason']}"))
        return 0

    if a.cmd == "custody" and a.sub == "verify":
        c = Case.open(a.dir)
        r = c.verify_custody()
        if r["ok"]:
            print(f"custody chain OK: {r['entries']} entries, head seq {r['head_seq']} {r['head_hash']}")
            return 0
        print(f"custody chain BROKEN at line {r['first_bad_line']} (seq {r['first_bad_seq']}): {r['reason']}")
        return 2

    if a.cmd == "scan":
        from dronedog.ingest import Walker, expand_inputs
        out = []
        for src in expand_inputs(a.paths):
            w = Walker()
            for cand in w.walk(src):
                out.append({"source": str(src.path), "name": cand.name, **cand.provenance()})
                if not a.json:
                    flag = " DELETED" if cand.deleted else ""
                    print(f"{src.label}: {cand.inner_path} ({cand.size} B){flag} {cand.reasons}")
            if not a.json:
                st = w.stats
                print(f"  -> {st.files_seen} files, {st.candidates} candidates, "
                      f"{st.deleted_candidates} deleted, {len(st.errors)} errors")
        if a.json:
            print(json.dumps(out, indent=2, default=str))
        return 0

    ap.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
