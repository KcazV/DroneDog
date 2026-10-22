"""C5 Case: folder layout, case.json, evidence intake, artifact extraction, flight storage.

    case = Case.create(r"D:\\Cases\\2026-0142", name="...", examiner="...", case_number="...")
    case = Case.open(r"D:\\Cases\\2026-0142")

Layout::

    <Case>/case.json  case.db  custody.log  evidence/  extracted/  exports/  .tmp/

Evidence intake rules (recorded in evidence.storage + custody params):
* loose files and zips up to ``COPY_MAX_BYTES`` are COPIED into evidence/<id>/ with
  SHA-256/SHA-1/MD5 computed while copying, then re-read and verified; a mismatch
  deletes the copy and refuses the evidence. Copies get the read-only attribute.
* disk images (and anything larger, or copy=False) are REFERENCED in place: hashed
  (whole set + per segment), never modified, size/mtime re-checked after hashing.
* folders / mounted drives are referenced; their hash is a manifest hash over the
  extracted candidates (``hash_scope='candidate-manifest'``).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from dronedog import PRODUCT_NAME, TOOL_NAME, __version__
from dronedog.case import hashing
from dronedog.case.custody import CustodyLog, io_ref, utc_now, verify_chain
from dronedog.case.store import FRAME_COLUMN_NAMES, SCHEMA_VERSION, Store

COPY_MAX_BYTES = 4 * 1024 ** 3
FRAME_CHUNK = 20000


class CaseError(Exception):
    pass


class EvidenceError(CaseError):
    pass


def _safe_name(name: str, limit: int = 120) -> str:
    s = re.sub(r"[^A-Za-z0-9._\-\[\]() ]", "_", name).strip(" .") or "artifact"
    return s[-limit:]


def _stat_sig(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
        return st.st_size, st.st_mtime_ns
    except OSError:
        return None


HEX_RE = re.compile(r"\b([0-9a-fA-F]{64}|[0-9a-fA-F]{40}|[0-9a-fA-F]{32})\b")


def sidecar_hashes(first: Path) -> dict:
    """Hashes found in imager sidecars next to an image (.md5/.sha1/.txt/.E01.txt/.log)."""
    found: dict[str, list[str]] = {}
    stem = first.name.rsplit(".", 1)[0]
    try:
        siblings = list(first.parent.iterdir())
    except OSError:
        return {}
    for s in siblings:
        n = s.name
        if s == first or not s.is_file() or s.stat().st_size > 1024 * 1024:
            continue
        if not (n.lower().startswith(stem.lower() + ".") or n.lower().startswith(first.name.lower() + ".")):
            continue
        if s.suffix.lower() not in (".md5", ".sha1", ".sha256", ".txt", ".log", ".csv", ".hash"):
            continue
        try:
            text = s.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for h in HEX_RE.findall(text):
            algo = {32: "md5", 40: "sha1", 64: "sha256"}[len(h)]
            found.setdefault(algo, [])
            if h.lower() not in found[algo]:
                found[algo].append(h.lower())
        found.setdefault("_files", []).append(n)
    return found


class Case:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.meta = json.loads((self.root / "case.json").read_text(encoding="utf-8"))
        self.store = Store(self.root / "case.db")
        self.store.migrate()
        self.custody = CustodyLog(self.root / "custody.log", on_append=self._set_head)

    # -- layout ------------------------------------------------------------
    @property
    def evidence_dir(self) -> Path:
        return self.root / "evidence"

    @property
    def extracted_dir(self) -> Path:
        return self.root / "extracted"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def tmp_dir(self) -> Path:
        p = self.root / ".tmp"
        p.mkdir(exist_ok=True)
        return p

    @property
    def custody_path(self) -> Path:
        return self.root / "custody.log"

    @property
    def id(self) -> str:
        return self.meta["id"]

    def rel(self, p: Path) -> str:
        try:
            return Path(p).resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(p)

    def abs(self, rel_path: str) -> Path:
        p = Path(rel_path)
        return p if p.is_absolute() else self.root / p

    # -- lifecycle -----------------------------------------------------------
    @classmethod
    def create(cls, root: str | os.PathLike, *, name: str, examiner: str, agency: str | None = None,
               case_number: str | None = None, notes: str | None = None) -> "Case":
        root = Path(root)
        if (root / "case.json").exists():
            raise CaseError(f"a case already exists at {root}")
        if root.exists() and any(root.iterdir()):
            raise CaseError(f"{root} exists and is not empty")
        for d in ("", "evidence", "extracted", "exports"):
            (root / d).mkdir(parents=True, exist_ok=True)
        meta = {
            "id": str(uuid.uuid4()), "name": name, "examiner": examiner, "agency": agency,
            "case_number": case_number, "notes": notes, "created_utc": utc_now(),
            "tool": PRODUCT_NAME, "tool_version": __version__, "schema_version": SCHEMA_VERSION,
            "layout_version": 1,
        }
        cj = root / "case.json"
        cj.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        case = cls(root)
        case.log("case.create", component="case", actor="examiner",
                 outputs=[io_ref("case.json", hashing.sha256_path_text(cj)),
                          io_ref("case.db", None, schema_version=SCHEMA_VERSION)],
                 params={k: meta[k] for k in ("id", "name", "examiner", "agency", "case_number")})
        return case

    @classmethod
    def open(cls, root: str | os.PathLike) -> "Case":
        root = Path(root)
        if not (root / "case.json").exists():
            raise CaseError(f"no case.json in {root}")
        return cls(root)

    def close(self) -> None:
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- custody --------------------------------------------------------------
    def _set_head(self, seq: int, h: str) -> None:
        self.store.execute("INSERT INTO custody_head(id, seq, hash) VALUES (1, ?, ?) "
                           "ON CONFLICT(id) DO UPDATE SET seq=excluded.seq, hash=excluded.hash", (seq, h))

    def log(self, action: str, *, component: str, inputs=(), outputs=(), params=None,
            actor: str = "system", tool_version: str | None = None) -> dict:
        return self.custody.append(action, inputs=inputs, outputs=outputs, component=component,
                                   params=params, actor=actor, tool_version=tool_version)

    def custody_head(self) -> tuple[int, str] | None:
        r = self.store.one("SELECT seq, hash FROM custody_head WHERE id=1")
        return (r["seq"], r["hash"]) if r else None

    def verify_custody(self) -> dict:
        rep = verify_chain(self.custody_path, self.custody_head())
        return rep.as_dict()

    # -- evidence ---------------------------------------------------------------
    def default_copy(self, src) -> bool:
        if src.kind in ("image", "folder", "sd"):
            return False
        return src.size <= COPY_MAX_BYTES

    def find_evidence(self, *, sha256: str | None = None, src_path: str | None = None) -> dict | None:
        if sha256:
            return self.store.one("SELECT * FROM evidence WHERE sha256=?", (sha256,))
        if src_path:
            return self.store.one("SELECT * FROM evidence WHERE src_path=?", (src_path,))
        return None

    def add_evidence(self, src, *, copy: bool | None = None, examiner: str | None = None,
                     progress=None, cancel=None, verify_media: bool = True, hash_evidence: bool = True) -> dict:
        """Register a Source (dronedog.ingest.sources). Returns the evidence row as a dict,
        with key 'duplicate_of' set when identical evidence was already in the case.

        hash_evidence=False (examiner option, for testing): the item is referenced in place and
        not hashed or verified; this is recorded in the evidence row (hash_scope 'not-hashed')
        and the custody log. Extracted artifacts are still hashed."""
        examiner = examiner or self.meta.get("examiner")
        copy = self.default_copy(src) if copy is None else bool(copy)
        if src.kind in ("folder", "sd") and copy:
            copy = False  # folders/drives are always referenced; candidates are extracted + hashed one by one
        if not hash_evidence:
            copy = False
        src_path = str(src.path if src.subkind == "raw_device" else Path(src.path).resolve())
        is_folder = src.kind in ("folder", "sd") and src.subkind != "raw_device"
        if is_folder:
            prev = self.find_evidence(src_path=src_path)
            if prev:
                return dict(prev, duplicate_of=prev["id"])
        meta: dict = {}
        if src.parent_label:
            meta["found_in_dropped_folder"] = src.parent_label
        if src.notes:
            meta["notes"] = src.notes
        cur = self.store.execute(
            "INSERT INTO evidence(label, src_path, kind, size, added_utc, examiner, storage, stored_path, "
            "hash_scope, subkind, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (src.label, src_path, src.kind, src.size or None, utc_now(), examiner,
             "copied" if copy else "referenced", None,
             "candidate-manifest" if is_folder else ("not-hashed" if not hash_evidence else
                                                     "file" if len(src.files) <= 1 else "segments-concat"),
             src.subkind, json.dumps(meta)))
        evid = cur.lastrowid
        try:
            if is_folder:
                stored = src_path
                whole, per, files_rows = None, [], []
            elif not hash_evidence:
                meta["hashing"] = "skipped by examiner (import option); evidence not hashed or verified"
                stored = src_path
                whole, per = None, []
                files_rows = [(evid, i + 1, str(f if src.subkind == "raw_device" else Path(f).resolve()), None,
                               None if src.subkind == "raw_device" else Path(f).stat().st_size, None, None, None)
                              for i, f in enumerate(src.files)]
            else:
                whole, per, files_rows, stored, vmeta = self._intake_files(evid, src, copy, progress, cancel,
                                                                            verify_media)
                meta.update(vmeta)
            if whole is not None:
                dup = self.store.one("SELECT * FROM evidence WHERE sha256=? AND id<>?", (whole.sha256, evid))
                if dup:
                    self._discard_evidence(evid, copy)
                    self.log("evidence.add", component="ingest.evidence", actor="examiner",
                             inputs=[io_ref(src_path, whole.sha256, sha1=whole.sha1, size=whole.size)],
                             params={"duplicate_of": dup["id"], "kind": src.kind,
                                     "note": "identical evidence already in case; not added again"})
                    return dict(dup, duplicate_of=dup["id"])
            with self.store.tx() as c:
                c.execute("UPDATE evidence SET size=?, sha256=?, sha1=?, md5=?, stored_path=?, meta_json=? WHERE id=?",
                          (whole.size if whole else (None if is_folder else src.size or None), whole.sha256 if whole else None,
                           whole.sha1 if whole else None, whole.md5 if whole else None,
                           stored, json.dumps(meta, default=str), evid))
                c.executemany("INSERT INTO evidence_files(evidence_id, seq, src_path, stored_path, size, sha256, sha1, md5)"
                              " VALUES (?,?,?,?,?,?,?,?)", files_rows)
        except BaseException as e:
            self._discard_evidence(evid, copy)
            self.log("evidence.add", component="ingest.evidence", actor="examiner",
                     inputs=[io_ref(src_path, None)],
                     params={"kind": src.kind, "error": f"{type(e).__name__}: {e}", "refused": True})
            raise
        row = self.store.one("SELECT * FROM evidence WHERE id=?", (evid,))
        params = {"evidence_id": evid, "kind": src.kind, "subkind": src.subkind,
                  "storage": row["storage"], "hash_scope": row["hash_scope"], "label": src.label}
        if not hash_evidence and not is_folder:
            params["hashing"] = meta["hashing"]
        if len(files_rows) > 1:
            params["segments"] = [{"path": r[2], "size": r[4], "sha256": r[5], "sha1": r[6], "md5": r[7]}
                                  for r in files_rows]
        outputs = []
        if copy and whole is not None:
            outputs = [io_ref(r[3], r[5], sha1=r[6], md5=r[7], size=r[4]) for r in files_rows]
        self.log("evidence.add", component="ingest.evidence", actor="examiner",
                 inputs=[io_ref(src_path, row["sha256"], sha1=row["sha1"], md5=row["md5"], size=row["size"])],
                 outputs=outputs, params=params)
        for v in meta.get("verifications", []):
            self.log("evidence.verify", component="ingest.evidence",
                     inputs=[io_ref(src_path, row["sha256"])], params=dict(v, evidence_id=evid))
        return row

    def _discard_evidence(self, evid: int, copied: bool) -> None:
        if copied:
            d = self.evidence_dir / f"{evid:04d}"
            if d.exists():
                for p in d.rglob("*"):
                    if p.is_file():
                        hashing.clear_readonly(p)
                shutil.rmtree(d, ignore_errors=True)
        with self.store.tx() as c:
            c.execute("DELETE FROM evidence_files WHERE evidence_id=?", (evid,))
            c.execute("DELETE FROM evidence WHERE id=?", (evid,))

    def _intake_files(self, evid, src, copy, progress, cancel, verify_media):
        files = [Path(f) for f in src.files]
        raw_dev = src.subkind == "raw_device"
        algos = hashing.IMAGE_ALGOS
        total = sum(f.stat().st_size for f in files) if not raw_dev else 0
        verifications = []
        vmeta: dict = {}
        before = [_stat_sig(f) for f in files] if not raw_dev else []

        def prog(stage):
            def f(n):
                if progress:
                    progress(stage, n, total)
            return f

        if raw_dev:
            from dronedog.ingest import image as img_mod
            img = img_mod.open_image("raw_device", files)
            try:
                total = img.get_size()
                rdr = img_mod.buffered(img_mod.image_reader(img), hashing.CHUNK)
                whole = hashing.hash_stream(rdr, algos, prog("hash"), cancel)
            finally:
                img.close()
            per = [whole]
            rows = [(evid, 1, str(files[0]), None, whole.size, whole.sha256, whole.sha1, whole.md5)]
            return whole, per, rows, str(files[0]), vmeta
        if copy:
            dest_dir = self.evidence_dir / f"{evid:04d}"
            dest_dir.mkdir(parents=True, exist_ok=False)
            dsts, src_hashes = [], []
            done = 0
            for f in files:
                dst = dest_dir / f.name
                base = done

                def p2(n, _b=base):
                    if progress:
                        progress("copy", _b + n, total)
                with open(f, "rb", buffering=0) as fh:
                    r = hashing.copy_with_hash(fh, dst, algos, p2, cancel)
                done += r.size
                dsts.append(dst)
                src_hashes.append(r)
            # verify-after-copy: re-read the copies
            whole, per = hashing.hash_files(dsts, algos, prog("verify"), cancel)
            for f, s, d in zip(files, src_hashes, per):
                if s.digests != d.digests or s.size != d.size:
                    raise EvidenceError(f"verify-after-copy FAILED for {f}: source {s.digests} != copy {d.digests}")
            for d in dsts:
                hashing.set_readonly(d)
            verifications.append({"check": "verify-after-copy", "result": "match",
                                  "algorithms": list(algos), "files": len(files)})
            stored_paths = [self.rel(d) for d in dsts]
            stored = self.rel(dest_dir / files[0].name)
        else:
            whole, per = hashing.hash_files(files, algos, prog("hash"), cancel)
            stored_paths = [None] * len(files)
            stored = str(files[0].resolve())
        after = [_stat_sig(f) for f in files]
        if before != after:
            raise EvidenceError("source changed (size/mtime) while it was being read; refusing evidence")
        verifications.append({"check": "source-unchanged-during-read", "result": "match",
                              "basis": "size + mtime before/after"})
        rows = [(evid, i + 1, str(f.resolve()), sp, h.size, h.sha256, h.sha1, h.md5)
                for i, (f, sp, h) in enumerate(zip(files, stored_paths, per))]
        if src.kind == "image":
            sc = sidecar_hashes(files[0])
            if sc:
                ours = {"md5": whole.md5, "sha1": whole.sha1, "sha256": whole.sha256}
                matched = {a: ours[a] for a in ("md5", "sha1", "sha256") if ours.get(a) in sc.get(a, [])}
                verifications.append({
                    "check": "imager-sidecar", "sidecars": sc.get("_files"),
                    "result": "match" if matched else ("no-match" if src.subkind not in ("e01", "ex01") else "n/a"),
                    "matched": matched,
                    "note": ("sidecar hashes describe the acquired media; for E01 they are compared with the "
                             "media hash below, not the container file hash") if src.subkind in ("e01", "ex01") else None,
                    "sidecar_values": {k: v for k, v in sc.items() if not k.startswith("_")}})
            if src.subkind in ("e01", "ex01"):
                vmeta["ewf"] = self._ewf_check(files, verify_media, verifications, sc if sc else {}, progress, cancel)
        vmeta["verifications"] = verifications
        return whole, per, rows, stored, vmeta

    def _ewf_check(self, files, verify_media, verifications, sidecars, progress, cancel) -> dict:
        from dronedog.ingest import image as img_mod
        img = img_mod.EwfImgInfo([str(f) for f in files])
        try:
            hv = img.header_values()
            out = {k: v for k, v in hv.items()}
            if verify_media:
                size = img.get_size()
                rdr = img_mod.buffered(img_mod.image_reader(img), hashing.CHUNK)
                media = hashing.hash_stream(rdr, ("md5", "sha1", "sha256"),
                                            (lambda n: progress("verify-media", n, size)) if progress else None,
                                            cancel)
                out["media_md5"], out["media_sha1"], out["media_sha256"] = media.md5, media.sha1, media.sha256
                stored = {k.split(".", 1)[1].lower(): str(v).lower() for k, v in hv.items()
                          if k.startswith("stored_hash.")}
                res = {}
                for a in ("md5", "sha1"):
                    if a in stored:
                        res[a] = "match" if stored[a] == media.digests[a] else "MISMATCH"
                for a in ("md5", "sha1", "sha256"):
                    if media.digests[a] in sidecars.get(a, []):
                        res[f"sidecar_{a}"] = "match"
                verifications.append({"check": "ewf-media-hash", "stored": stored,
                                      "computed": media.digests, "result": res or "no stored hash"})
            return out
        finally:
            img.close()

    def evidence_read_paths(self, ev: dict) -> list[Path]:
        """Where to read an evidence item's bytes from (the copy if copied)."""
        rows = self.store.query("SELECT * FROM evidence_files WHERE evidence_id=? ORDER BY seq", (ev["id"],))
        if not rows:
            return [Path(ev["stored_path"] or ev["src_path"])]
        return [self.abs(r["stored_path"]) if r["stored_path"] else Path(r["src_path"]) for r in rows]

    def finalize_folder_evidence(self, evid: int, walk_stats: dict) -> None:
        """Manifest hash over a referenced folder's extracted candidates + walk stats."""
        rows = self.store.query(
            "SELECT s.inner_path, s.size, a.sha256 FROM artifact_sources s JOIN artifacts a ON a.id=s.artifact_id "
            "WHERE s.evidence_id=? ORDER BY s.inner_path", (evid,))
        lines = "\n".join(f"{r['inner_path']}\t{r['size']}\t{r['sha256']}" for r in rows).encode("utf-8")
        import hashlib
        h256, h1 = hashlib.sha256(lines).hexdigest(), hashlib.sha1(lines).hexdigest()
        ev = self.store.one("SELECT * FROM evidence WHERE id=?", (evid,))
        meta = json.loads(ev["meta_json"] or "{}")
        meta["walk"] = {k: walk_stats.get(k) for k in ("files_seen", "dirs_seen", "candidates", "bytes_total")}
        meta["manifest_entries"] = len(rows)
        self.store.execute("UPDATE evidence SET sha256=?, sha1=?, size=?, meta_json=? WHERE id=?",
                           (h256, h1, walk_stats.get("bytes_total"), json.dumps(meta, default=str), evid))
        self.log("evidence.verify", component="ingest.folder", inputs=[io_ref(ev["src_path"], h256)],
                 params={"evidence_id": evid, "check": "candidate-manifest",
                         "basis": "sha256 over sorted 'inner_path\\tsize\\tsha256' lines of extracted candidates",
                         "entries": len(rows), "files_seen": walk_stats.get("files_seen")})

    def update_evidence_meta(self, evid: int, **kv) -> None:
        ev = self.store.one("SELECT meta_json FROM evidence WHERE id=?", (evid,))
        meta = json.loads(ev["meta_json"] or "{}")
        meta.update(kv)
        self.store.execute("UPDATE evidence SET meta_json=? WHERE id=?", (json.dumps(meta, default=str), evid))

    # -- artifacts ----------------------------------------------------------------
    def extract(self, ev: dict, cand, cancel=None, component: str | None = None, progress=None) -> dict:
        """Copy a Candidate out to extracted/, hash it, dedupe by sha256, record provenance."""
        component = component or f"ingest.{cand.container}"
        tmp_dir = self.extracted_dir / ".tmp"
        tmp_dir.mkdir(exist_ok=True)
        tmp = tmp_dir / uuid.uuid4().hex
        prov = cand.provenance()
        now = utc_now()
        ev_in = io_ref(ev["src_path"], ev.get("sha256"), evidence_id=ev["id"], inner_path=cand.inner_path)
        partial = None
        try:
            with cand.opener() as fh:
                r = hashing.copy_with_hash(fh, tmp, hashing.DEFAULT_ALGOS, progress, cancel)
                raw = getattr(fh, "raw", fh)
                if getattr(raw, "short_read_error", None):
                    partial = {"recovered_bytes": r.size, "declared_size": cand.size,
                               "stopped_at": raw.short_read_at, "read_error": raw.short_read_error}
        except hashing.Cancelled:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as e:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            err = f"extraction failed: {type(e).__name__}: {str(e).splitlines()[0][:300]}"
            if cand.extra.get("partial_image"):
                err = f"file data not present in the image - {cand.extra['partial_image']}; TSK: {err}"
            with self.store.tx() as c:
                aid = c.execute(
                    "INSERT INTO artifacts(evidence_id, rel_path, inner_path, offset, size, status, error, name, added_utc)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (ev["id"], None, cand.inner_path, cand.offset, cand.size, "failed", err, cand.name, now)).lastrowid
                sid = self._insert_source(c, aid, ev["id"], cand, now)
            self.log("artifact.extract", component=component, inputs=[ev_in], outputs=[],
                     params={"artifact_id": aid, "error": err, "provenance": prov})
            return {"artifact_id": aid, "source_id": sid, "new": True, "failed": True, "error": err}
        if cand.size is not None and r.size != cand.size:
            prov["size_note"] = f"declared size {cand.size}, extracted {r.size} bytes"
        if partial:
            prov["partial"] = partial
            cand.extra["partial"] = partial
        if r.size == 0:
            tmp.unlink(missing_ok=True)
            err = "extraction produced 0 bytes" + (f" ({partial['read_error']})" if partial else "")
            with self.store.tx() as c:
                aid = c.execute(
                    "INSERT INTO artifacts(evidence_id, inner_path, offset, size, status, error, name, added_utc)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (ev["id"], cand.inner_path, cand.offset, cand.size, "failed", err, cand.name, now)).lastrowid
                sid = self._insert_source(c, aid, ev["id"], cand, now)
            self.log("artifact.extract", component=component, inputs=[ev_in], outputs=[],
                     params={"artifact_id": aid, "error": err, "provenance": prov})
            return {"artifact_id": aid, "source_id": sid, "new": True, "failed": True, "error": err}
        existing = self.store.one("SELECT id, rel_path FROM artifacts WHERE sha256=?", (r.sha256,))
        if existing:
            tmp.unlink(missing_ok=True)
            with self.store.tx() as c:
                sid = self._insert_source(c, existing["id"], ev["id"], cand, now)
            self.log("artifact.extract", component=component, inputs=[ev_in],
                     outputs=[io_ref(existing["rel_path"], r.sha256, size=r.size)],
                     params={"artifact_id": existing["id"], "dedup": True, "source_id": sid, "provenance": prov})
            return {"artifact_id": existing["id"], "source_id": sid, "new": False, "sha256": r.sha256}
        final = self.extracted_dir / f"{r.sha256[:12]}_{_safe_name(cand.name)}"
        if final.exists():  # same 12-char prefix + name but different hash: practically never
            final = self.extracted_dir / f"{r.sha256[:20]}_{_safe_name(cand.name)}"
        os.replace(tmp, final)
        hashing.set_readonly(final)
        rel = self.rel(final)
        with self.store.tx() as c:
            aid = c.execute(
                "INSERT INTO artifacts(evidence_id, rel_path, inner_path, offset, size, sha256, sha1, status, name,"
                " added_utc, detection_json, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev["id"], rel, cand.inner_path, cand.offset, r.size, r.sha256, r.sha1, "pending", cand.name, now,
                 json.dumps({"sniff": cand.sniff, "partial": partial}) if (cand.sniff or partial) else None,
                 (f"PARTIAL recovery: {partial['recovered_bytes']} of {partial['declared_size']} bytes "
                  f"({partial['read_error']})") if partial else None)).lastrowid
            sid = self._insert_source(c, aid, ev["id"], cand, now)
        self.log("artifact.extract", component=component, inputs=[ev_in],
                 outputs=[io_ref(rel, r.sha256, sha1=r.sha1, size=r.size)],
                 params={"artifact_id": aid, "dedup": False, "source_id": sid, "provenance": prov})
        return {"artifact_id": aid, "source_id": sid, "new": True, "sha256": r.sha256}

    @staticmethod
    def _insert_source(c, aid, evid, cand, now) -> int:
        t = cand.times or {}
        ios = cand.ios or {}
        return c.execute(
            "INSERT INTO artifact_sources(artifact_id, evidence_id, inner_path, container, offset, partition_index,"
            " partition_offset, fs_type, meta_addr, deleted, overwrite_status, size, mtime_utc, atime_utc,"
            " ctime_utc, crtime_utc, ios_domain, ios_relative_path, ios_file_id, app_hint, reasons_json,"
            " extra_json, found_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, evid, cand.inner_path, cand.container, cand.offset, cand.partition_index, cand.partition_offset,
             cand.fs_type, cand.meta_addr, 1 if cand.deleted else 0, cand.overwrite_status, cand.size,
             t.get("mtime_utc"), t.get("atime_utc"), t.get("ctime_utc"), t.get("crtime_utc"),
             ios.get("domain"), ios.get("relative_path"), ios.get("file_id"), cand.app_hint,
             json.dumps(cand.reasons), json.dumps(cand.extra, default=str) if cand.extra else None, now)).lastrowid

    def artifact_path(self, art: dict) -> Path:
        return self.abs(art["rel_path"])

    def set_detection(self, aid: int, det: dict, status: str | None = None, error: str | None = None) -> None:
        old = self.store.one("SELECT detection_json FROM artifacts WHERE id=?", (aid,))
        dj = json.loads(old["detection_json"]) if old and old["detection_json"] else {}
        dj.update({k: v for k, v in det.items()})
        self.store.execute(
            "UPDATE artifacts SET detected_kind=?, detected_subtype=?, detection_json=?, status=COALESCE(?, status),"
            " error=? WHERE id=?",
            (det.get("kind"), det.get("subtype"), json.dumps(dj, default=str), status, error, aid))

    def set_status(self, aid: int, status: str, error: str | None = None) -> None:
        self.store.execute("UPDATE artifacts SET status=?, error=? WHERE id=?", (status, error, aid))

    # -- flights -----------------------------------------------------------------------
    def store_flight(self, aid: int, result: dict, source: str, result_sha256: str | None = None) -> dict:
        """Insert one ParseResult (C1) as flights/frames/events rows. Replaces earlier parses
        of the same artifact. Returns {"flight_id", "frame_count", "replaced"}."""
        frames = result.get("frames") or {}
        cols = list(frames.keys())
        n = max((len(v) for v in frames.values()), default=0)
        for k, v in frames.items():
            if len(v) != n:
                raise ValueError(f"frame column {k!r} has {len(v)} values, expected {n}")
        known = [c for c in FRAME_COLUMN_NAMES if c in frames]
        extra = [c for c in cols if c not in FRAME_COLUMN_NAMES]
        details = result.get("details") or {}
        lats = [x for x in frames.get("lat", []) if x is not None]
        lons = [x for x in frames.get("lon", []) if x is not None]
        bbox = {"min_lat": min(lats), "max_lat": max(lats), "min_lon": min(lons), "max_lon": max(lons)} \
            if lats and lons else None
        duration = details.get("duration_s")
        if duration is None and frames.get("t_offset_ms"):
            ts = [t for t in frames["t_offset_ms"] if t is not None]
            duration = (max(ts) - min(ts)) / 1000.0 if ts else None
        warnings = list(result.get("warnings") or [])
        if extra:
            warnings.append(f"non-C3 frame columns stored in frame_extra: {', '.join(extra)}")
        with self.store.tx() as c:
            old = [r[0] for r in c.execute("SELECT id FROM flights WHERE artifact_id=?", (aid,))]
            for fid in old:
                c.execute("DELETE FROM frames WHERE flight_id=?", (fid,))
                c.execute("DELETE FROM frame_extra WHERE flight_id=?", (fid,))
                c.execute("DELETE FROM events WHERE flight_id=?", (fid,))
                c.execute("DELETE FROM links WHERE flight_a=? OR flight_b=?", (fid, fid))
                c.execute("DELETE FROM flights WHERE id=?", (fid,))
            fid = c.execute(
                "INSERT INTO flights(artifact_id, adapter, adapter_version, source, details_json, start_utc, duration_s,"
                " frame_count, bbox_json, warnings_json, records_total, records_decoded, records_failed,"
                " encrypted_unreadable, result_sha256, parsed_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, result.get("adapter"), result.get("adapter_version"), source,
                 json.dumps(details, default=str), details.get("start_time_utc"), duration, n,
                 json.dumps(bbox) if bbox else None, json.dumps(warnings),
                 result.get("records_total"), result.get("records_decoded"), result.get("records_failed"),
                 1 if result.get("encrypted_unreadable") else 0, result_sha256, utc_now())).lastrowid
            if n and known:
                col_vals = []
                for col in known:
                    v = frames[col]
                    if col == "battery_cells_v":
                        v = [None if x is None else json.dumps(x) for x in v]
                    elif col in ("motor_on", "is_flying"):
                        v = [None if x is None else (1 if x else 0) for x in v]
                    col_vals.append(v)
                sql = (f"INSERT INTO frames(flight_id, idx, {', '.join(known)}) VALUES "
                       f"(?, ?, {', '.join('?' * len(known))})")
                for start in range(0, n, FRAME_CHUNK):
                    end = min(n, start + FRAME_CHUNK)
                    rows = zip([fid] * (end - start), range(start, end), *(cv[start:end] for cv in col_vals))
                    c.executemany(sql, rows)
            for col in extra:
                c.execute("INSERT INTO frame_extra(flight_id, name, values_json) VALUES (?,?,?)",
                          (fid, col, json.dumps(frames[col], default=str)))
            evs = result.get("events") or []
            c.executemany("INSERT INTO events(flight_id, t_offset_ms, kind, severity, message, raw) VALUES (?,?,?,?,?,?)",
                          [(fid, e.get("t_offset_ms"), e.get("kind"), e.get("severity"), e.get("message"),
                            e.get("raw")) for e in evs])
        return {"flight_id": fid, "frame_count": n, "replaced": old, "extra_columns": extra}

    # -- reporting helpers -----------------------------------------------------------------
    def info(self) -> dict:
        q = self.store.one
        counts = {
            "evidence": q("SELECT COUNT(*) n FROM evidence")["n"],
            "artifacts": q("SELECT COUNT(*) n FROM artifacts")["n"],
            "artifact_sources": q("SELECT COUNT(*) n FROM artifact_sources")["n"],
            "deleted_sources": q("SELECT COUNT(*) n FROM artifact_sources WHERE deleted=1")["n"],
            "flights": q("SELECT COUNT(*) n FROM flights")["n"],
            "frames": q("SELECT COUNT(*) n FROM frames")["n"],
            "events": q("SELECT COUNT(*) n FROM events")["n"],
        }
        by_status = {r["status"]: r["n"] for r in self.store.query(
            "SELECT status, COUNT(*) n FROM artifacts GROUP BY status")}
        by_kind = {f"{r['k']}/{r['s']}": r["n"] for r in self.store.query(
            "SELECT COALESCE(detected_kind,'?') k, COALESCE(detected_subtype,'?') s, COUNT(*) n FROM artifacts GROUP BY 1,2")}
        return {"case": self.meta, "root": str(self.root), "schema_version": self.store.current_version(),
                "counts": counts, "artifacts_by_status": by_status, "artifacts_by_type": by_kind,
                "custody": self.verify_custody(), "tool": TOOL_NAME, "tool_version": __version__}
