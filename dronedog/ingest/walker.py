"""One source walker: yields flight-log Candidates from any kind of evidence.

    walker = Walker(policy, tmpdir=case.tmp_dir, cancel=event)
    for cand in walker.walk(source):          # Source from dronedog.ingest.sources
        ...extract cand.opener() ...
    walker.stats                                # WalkStats: counts, containers, errors

Containers recurse: zip-in-zip, zip-in-image, iOS-backup-in-zip, tar / tar.gz anywhere
(e.g. the sdcard.tar.gz + adb-data.tar inside an Android "quick image" zip), DJI Assistant
export bundles (.DAT zlib bundles of FLY files) anywhere. Every candidate carries
its full provenance (inner path chain, deleted flag, inode, partition, offsets,
iOS domain/relativePath mapping, timestamps).
"""
from __future__ import annotations

import bz2
import gzip
import lzma
import os
import shutil
import struct
import tarfile
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from dronedog.case.hashing import Cancelled
from dronedog.ingest import image as img_mod
from dronedog.ingest import ios as ios_mod
from dronedog.ingest.candidates import DEFAULT_POLICY, CandidatePolicy, app_hint
from dronedog.ingest.entries import Candidate, Entry, WalkStats
from dronedog.ingest.sources import Source

ASSISTANT_HDR = 283


def _iso_ts(ts: float | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _stat_times(st: os.stat_result) -> dict:
    t = {"mtime_utc": _iso_ts(st.st_mtime), "atime_utc": _iso_ts(st.st_atime)}
    birth = getattr(st, "st_birthtime", None)
    if birth is not None:
        t["crtime_utc"] = _iso_ts(birth)
    return {k: v for k, v in t.items() if v}


def _is_zip_head(head: bytes, name: str) -> bool:
    return head[:4] == b"PK\x03\x04" or (name.lower().endswith(".zip") and head[:4] == b"PK\x05\x06")


def _tar_mode(head: bytes, name: str) -> str | None:
    """tarfile stream mode for a tar container, else None. Plain tar is recognised by the
    ustar magic; compressed tars by the compressor magic plus a tar-style name."""
    n = name.lower()
    if head[257:262] == b"ustar":
        return "r|"
    if head[:2] == b"\x1f\x8b" and n.endswith((".tar.gz", ".tgz")):
        return "r|gz"
    if head[:3] == b"BZh" and n.endswith((".tar.bz2", ".tbz2", ".tbz")):
        return "r|bz2"
    if head[:6] == b"\xfd7zXZ\x00" and n.endswith((".tar.xz", ".txz")):
        return "r|xz"
    return None


FCLOG_ENTRY = 64


def _is_fclog_head(head: bytes) -> bool:
    """DJI flight-controller ring log (MISC/LOG/flylog/fc_log.log on the aircraft's SD card):
    u32 write position, u32 entry count, then 64-byte index entries from 0x20 tagged 'LGY '."""
    if len(head) < 0x20 + FCLOG_ENTRY or head[0x30:0x34] != b"LGY ":
        return False
    count = struct.unpack_from("<I", head, 4)[0]
    return 1 <= count <= 4096


def fclog_index(fh: BinaryIO, size: int) -> dict:
    """Parse the fc_log.log index and resolve each log's byte ranges in the ring.

    Layout (observed on a Mavic Mini SD card, see docs/DAT_FORMAT.md section 13): u32 write
    position, u32 entry count, 24 bytes 0xFF, then per log: u32 log number, u32 start, u32 end,
    u32 length, 'LGY ', u32 month, day, year, hour, minute, second, then 3 u32 and 8 bytes 0xFF.
    Logs are written in index order into a ring that wraps from the end of the file back to the
    start of the data area. A log is extracted up to the first byte a later log overwrote."""
    fh.seek(0)
    hdr = fh.read(0x20)
    write_pos, count = struct.unpack_from("<II", hdr, 0)
    raw = fh.read(FCLOG_ENTRY * count)
    entries = []
    for i in range(count):
        r = raw[i * FCLOG_ENTRY:(i + 1) * FCLOG_ENTRY]
        if len(r) < FCLOG_ENTRY or r[16:20] != b"LGY ":
            break
        num, start, end, length = struct.unpack_from("<IIII", r, 0)
        mo, dd, yy, hh, mi, ss = struct.unpack_from("<6I", r, 20)
        entries.append({"index_slot": i, "log_number": num, "start": start, "end": end, "length": length,
                        "index_time_raw": f"{yy:04d}-{mo:02d}-{dd:02d} {hh:02d}:{mi:02d}:{ss:02d}"})
    data_start = None
    for e in entries:                     # the wrapped log fixes where the data area begins
        if e["end"] < e["start"]:
            data_start = size - e["start"] + e["end"] - e["length"]
            break
    if data_start is None:
        data_start = min((e["start"] for e in entries), default=0x20 + FCLOG_ENTRY * count)
    for e in entries:
        if e["start"] + e["length"] == e["end"] and e["end"] <= size:
            e["pieces"] = [(e["start"], e["end"])]
        elif e["end"] < e["start"] and data_start <= e["end"] and size - e["start"] + e["end"] - data_start == e["length"]:
            e["pieces"] = [(e["start"], size), (data_start, e["end"])]
            e["wrapped"] = True
        else:
            e["pieces"] = []
            e["problem"] = "index entry is inconsistent (start/end/length do not agree)"
    # bytes of each log that later logs overwrote: extract the intact leading part only
    for k, e in enumerate(entries):
        later = [p for x in entries[k + 1:] for p in x["pieces"]]
        surviving = []
        for a, b in e["pieces"]:
            cur = [(a, b)]
            for la, lb in later:
                cur = [r for ca, cb in cur for r in ((ca, min(cb, la)), (max(ca, lb), cb)) if r[0] < r[1]]
            surviving += cur
        e["surviving"] = surviving
        keep, pos = [], 0
        for (a, b), s in zip(e["pieces"], surviving + [None] * len(e["pieces"])):
            if s is None or s[0] != a:
                break
            keep.append(s)
            pos += s[1] - s[0]
            if s[1] != b:
                break
        if pos < e["length"] and e["pieces"]:
            e["overwritten_from"] = pos
        e["extract"] = keep
    return {"write_position": write_pos, "count": count, "data_start": data_start, "file_size": size,
            "entries": entries}


class _FcLogSegment:
    """One log inside fc_log.log, spooled from its ring pieces to a temp file on first open."""

    def __init__(self, parent: BinaryIO, pieces: list[tuple[int, int]], tmpdir: Path):
        self._parent, self._pieces, self._tmpdir = parent, pieces, tmpdir
        self.path: str | None = None

    def head(self, n: int) -> bytes:
        a, b = self._pieces[0]
        self._parent.seek(a)
        return self._parent.read(min(n, b - a))

    def open(self) -> BinaryIO:
        if self.path is None:
            fd, tmp = tempfile.mkstemp(prefix="fclog_", suffix=".DAT", dir=self._tmpdir)
            try:
                with os.fdopen(fd, "wb") as out:
                    for a, b in self._pieces:
                        self._parent.seek(a)
                        left = b - a
                        while left:
                            chunk = self._parent.read(min(left, 4 * 1024 * 1024))
                            if not chunk:
                                raise OSError(f"fc_log.log ended at {a + (b - a - left):#x}, expected {b:#x}")
                            out.write(chunk)
                            left -= len(chunk)
            except BaseException:
                os.unlink(tmp)
                raise
            self.path = tmp
        return open(self.path, "rb")

    def cleanup(self) -> None:
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass


class _TarMember:
    """One member of a forward-only tar stream. The head stays in memory for the candidate
    policy; the first open() spools head + rest to a temp file (and checks the size against
    the tar header), so the member can be re-opened while the walk is positioned on it."""

    def __init__(self, src: BinaryIO, head: bytes, size: int, tmpdir: Path):
        self._src, self.head, self.size, self._tmpdir = src, head, size, tmpdir
        self.path: str | None = None

    def open(self) -> BinaryIO:
        if self.path is None:
            fd, tmp = tempfile.mkstemp(prefix="tar_", dir=self._tmpdir)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(self.head)
                    shutil.copyfileobj(self._src, out, 4 * 1024 * 1024)
                    got = out.tell()
                if got != self.size:
                    raise OSError(f"tar member truncated: {got} of {self.size} bytes")
            except BaseException:
                os.unlink(tmp)
                raise
            self.path = tmp
        return open(self.path, "rb")

    def cleanup(self) -> None:
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass


class Walker:
    def __init__(self, policy: CandidatePolicy | None = None, tmpdir: str | os.PathLike | None = None,
                 cancel=None, progress=None):
        self.policy = policy or DEFAULT_POLICY
        self.tmpdir = Path(tmpdir) if tmpdir else Path(tempfile.gettempdir())
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.cancel = cancel
        self.progress = progress          # progress(message:str, files_seen:int)
        self.stats = WalkStats()
        self.ios_manifests: list[dict] = []   # summaries of iOS manifests used (for evidence meta)
        self._last_tick = 0

    # -- public -----------------------------------------------------------
    def walk(self, src: Source, files: list[Path] | None = None) -> Iterator[Candidate]:
        """files: where to read the evidence from (stored copies), defaults to src.files/src.path."""
        files = files or src.files
        if src.kind == "image" or src.subkind == "raw_device":
            yield from self._walk_image(src, files)
        elif src.kind == "zip":
            with open(files[0], "rb") as fh:
                yield from self._walk_zip(fh, "", 0, {}, src.label)
        elif src.kind in ("folder", "sd"):
            root = Path(files[0]) if files else src.path
            if src.subkind == "ios_backup":
                yield from self._walk_ios_folder(root, "", {})
            else:
                yield from self._walk_folder(root, src.exclude)
        elif src.kind == "file":
            p = Path(files[0])
            st = p.stat()
            e = Entry(p.name, p.name, st.st_size, lambda _p=p: open(_p, "rb"), {"times": _stat_times(st)})
            self.stats.files_seen += 1
            self.stats.bytes_total += st.st_size
            yield from self._process(e, "", "file", 0, {}, force=True)
        else:
            raise ValueError(f"unknown source kind {src.kind}")

    # -- core dispatch ----------------------------------------------------
    def _check_cancel(self):
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()

    def _tick(self, where: str):
        if self.progress and self.stats.files_seen - self._last_tick >= 200:
            self._last_tick = self.stats.files_seen
            self.progress(f"{where}: {self.stats.files_seen} files examined, "
                          f"{self.stats.candidates} candidates", self.stats.files_seen)

    def _process(self, e: Entry, prefix: str, container: str, depth: int, outer: dict,
                 force: bool = False) -> Iterator[Candidate]:
        self._check_cancel()
        self._tick(container)
        pol = self.policy
        try:
            head = e.head(512) if not pol.is_media(e.name) or _is_zip_name(e.name) else b""
        except Exception as ex:  # noqa: BLE001
            self.stats.error(prefix + e.path, f"unreadable: {ex}")
            head = b""
        deleted = bool(e.meta.get("deleted")) or bool(outer.get("deleted"))
        # containers ------------------------------------------------------
        if head and _is_zip_head(head, e.name):
            if depth >= pol.max_container_depth:
                self.stats.skip("container_depth_limit")
            elif e.size is not None and e.size > pol.max_nested_zip_bytes:
                self.stats.skip("nested_zip_too_large")
            else:
                yield from self._nested_zip(e, prefix, depth, outer)
                return
        mode = _tar_mode(head, e.name) if head else None
        if mode:
            if depth >= pol.max_container_depth:
                self.stats.skip("container_depth_limit")
            else:
                yield from self._nested_tar(e, prefix, depth, outer, mode)
                return
        if head and _is_fclog_head(head) and depth < pol.max_container_depth:
            yield from self._walk_fclog(e, prefix, depth, outer)
            return
        if head and pol.is_assistant_export(e.name, head) and depth < pol.max_container_depth:
            res = {"members": 0}
            yield from self._walk_assistant(e, prefix, depth, outer, res)
            if res["members"]:
                return
        # candidate policy ----------------------------------------------------
        full_path = prefix + e.path
        sel, reasons, sn = pol.evaluate(full_path, e.name, e.size, e.head, deleted=deleted, force=force)
        if not sel:
            if e.size == 0:
                self.stats.skip("empty_file")
            return
        cand = Candidate(
            name=e.name, inner_path=full_path, size=e.size, opener=e.opener, container=container,
            reasons=reasons, sniff=sn, deleted=deleted,
            meta_addr=e.meta.get("meta_addr"), offset=e.meta.get("offset"),
            partition_index=e.meta.get("partition_index"), partition_offset=e.meta.get("partition_offset"),
            fs_type=e.meta.get("fs_type"), overwrite_status=e.meta.get("overwrite_status"),
            times=e.meta.get("times") or {}, ios=e.meta.get("ios"),
            app_hint=app_hint(full_path),
        )
        if "_tsk_file" in e.meta:
            extra = img_mod.finish_image_provenance(e)
            cand.offset = extra.pop("offset", cand.offset)
            cand.overwrite_status = extra.pop("overwrite_status", cand.overwrite_status)
            cand.extra.update(extra)
        for k in ("times_note", "zip_member", "assistant_member", "tar_member", "fc_log_segment"):
            if k in e.meta:
                cand.extra[k] = e.meta[k]
        if outer:
            cand.extra["outer"] = outer
            if cand.partition_index is None:
                cand.partition_index = outer.get("partition_index")
                cand.partition_offset = outer.get("partition_offset")
                cand.fs_type = outer.get("fs_type")
            if outer.get("deleted") and not e.meta.get("deleted"):
                cand.overwrite_status = cand.overwrite_status or outer.get("overwrite_status")
        self.stats.candidates += 1
        if deleted:
            self.stats.deleted_candidates += 1
        yield cand

    # -- folders ----------------------------------------------------------
    def _walk_folder(self, root: Path, exclude: set[str]) -> Iterator[Candidate]:
        self.stats.containers.append({"type": "folder", "path": str(root)})
        for dirpath, dirnames, filenames in os.walk(root):
            self._check_cancel()
            dp = Path(dirpath)
            dirnames.sort()
            keep = []
            for d in dirnames:
                full = dp / d
                if os.path.normcase(str(full)) in exclude:
                    continue
                if (full / "Manifest.db").is_file() or (full / "Manifest.mbdb").is_file():
                    rel = full.relative_to(root).as_posix()
                    yield from self._walk_ios_folder(full, rel + "/", {})
                    continue
                keep.append(d)
            dirnames[:] = keep
            self.stats.dirs_seen += 1
            for fn in sorted(filenames):
                fp = dp / fn
                if os.path.normcase(str(fp)) in exclude:
                    continue
                try:
                    st = fp.stat()
                except OSError as ex:
                    self.stats.error(str(fp), str(ex))
                    continue
                self.stats.files_seen += 1
                self.stats.bytes_total += st.st_size
                rel = fp.relative_to(root).as_posix()
                e = Entry(rel, fn, st.st_size, lambda _p=fp: open(_p, "rb"), {"times": _stat_times(st)})
                yield from self._process(e, "", "folder", 0, {})

    # -- zips -------------------------------------------------------------
    def _container_outer(self, e: Entry, prefix: str, outer: dict) -> dict:
        """Provenance of a container entry, handed down to everything found inside it."""
        new_outer = dict(outer)
        for k in ("deleted", "meta_addr", "offset", "partition_index", "partition_offset", "fs_type",
                  "overwrite_status"):
            if e.meta.get(k) is not None:
                new_outer.setdefault(k, e.meta.get(k))
        if "_tsk_file" in e.meta:
            ex = img_mod.finish_image_provenance(e)
            new_outer["offset"] = ex.get("offset")
            if e.meta.get("deleted"):
                new_outer["overwrite_status"] = ex.get("overwrite_status")
        new_outer["container_path"] = prefix + e.path
        return new_outer

    def _nested_zip(self, e: Entry, prefix: str, depth: int, outer: dict) -> Iterator[Candidate]:
        """Open a zip that is itself an entry. Seekable sources (files, images) are used in place;
        zip-in-zip members are spooled to a temp file first (deflate streams are not seekable)."""
        new_outer = self._container_outer(e, prefix, outer)
        tmp = None
        try:
            try:
                if e.meta.get("zip_member") or e.meta.get("assistant_member"):
                    fd, tmp = tempfile.mkstemp(prefix="nested_", suffix=".zip", dir=self.tmpdir)
                    with os.fdopen(fd, "wb") as out, e.opener() as src:
                        shutil.copyfileobj(src, out, 4 * 1024 * 1024)
                    fh = open(tmp, "rb")
                else:
                    fh = e.opener()
            except Cancelled:
                raise
            except Exception as ex:  # noqa: BLE001 - e.g. member cut off by a truncated tar
                self.stats.error(prefix + e.path, f"unreadable: {ex}")
                return
            with fh:
                yield from self._walk_zip(fh, prefix + e.path + "!/", depth + 1, new_outer, e.name)
        except zipfile.BadZipFile as ex:
            self.stats.error(prefix + e.path, f"bad zip: {ex}")
            # still evaluate it as a plain file (could be a misnamed log)
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _walk_zip(self, fh: BinaryIO, prefix: str, depth: int, outer: dict, label: str) -> Iterator[Candidate]:
        try:
            z = zipfile.ZipFile(fh)
        except zipfile.BadZipFile as ex:
            self.stats.error(prefix or label, f"bad zip: {ex}")
            return
        with z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            self.stats.containers.append({"type": "zip", "path": prefix.rstrip("!/") or label,
                                          "members": len(infos)})
            # iOS backup inside this zip?
            ios_prefix = None
            for i in infos:
                nm = i.filename.replace("\\", "/")
                base = nm.rsplit("/", 1)[-1]
                if base in ("Manifest.db", "Manifest.mbdb"):
                    ios_prefix = nm[: len(nm) - len(base)]
                    break
            handled: set[str] = set()
            if ios_prefix is not None:
                yield from self._walk_ios_zip(z, infos, ios_prefix, prefix, depth, outer, handled)
            for info in infos:
                nm = info.filename.replace("\\", "/")
                if nm in handled:
                    continue
                self._check_cancel()
                self.stats.files_seen += 1
                self.stats.bytes_total += info.file_size
                if info.flag_bits & 0x1:
                    self.stats.error(prefix + nm, "encrypted zip member (password required) - not read")
                    continue
                e = Entry(nm, nm.rsplit("/", 1)[-1], info.file_size,
                          lambda _i=info, _z=z: _z.open(_i),
                          head_reader=lambda n, _i=info, _z=z: _zip_head(_z, _i, n),
                          meta={"zip_member": {"compress_type": info.compress_type, "crc32": f"{info.CRC:08x}",
                                          "date_time_local": "%04d-%02d-%02dT%02d:%02d:%02d" % info.date_time,
                                          "compressed_size": info.compress_size}})
                yield from self._process(e, prefix, "zip", depth, outer)

    # -- tars -------------------------------------------------------------
    def _nested_tar(self, e: Entry, prefix: str, depth: int, outer: dict, mode: str) -> Iterator[Candidate]:
        """Walk a tar / tar.gz entry as a forward-only stream (no temp copy of the archive)."""
        new_outer = self._container_outer(e, prefix, outer)
        try:
            fh = e.opener()
        except Exception as ex:  # noqa: BLE001
            self.stats.error(prefix + e.path, f"unreadable: {ex}")
            return
        with fh:
            yield from self._walk_tar(fh, prefix + e.path + "!/", depth + 1, new_outer, e.name, mode)

    def _walk_tar(self, fh: BinaryIO, prefix: str, depth: int, outer: dict, label: str,
                  mode: str) -> Iterator[Candidate]:
        info = {"type": "tar", "path": prefix.rstrip("!/") or label, "members": 0}
        self.stats.containers.append(info)
        try:
            # Decompress outside tarfile: its own r|gz stream re-slices its whole decompressed
            # buffer on every 10 KB read, which is quadratic on highly compressible members
            # (sparse / zero-filled files in phone extractions) and can stall for hours.
            if mode == "r|gz":
                fh = gzip.GzipFile(fileobj=fh, mode="rb")
            elif mode == "r|bz2":
                fh = bz2.BZ2File(fh, "rb")
            elif mode == "r|xz":
                fh = lzma.LZMAFile(fh, "rb")
            t = tarfile.open(fileobj=fh, mode="r|")
        except (tarfile.TarError, EOFError, OSError, zlib.error, lzma.LZMAError) as ex:
            self.stats.error(info["path"], f"bad tar: {ex}")
            return
        with t:
            it = iter(t)
            while True:
                self._check_cancel()
                try:
                    m = next(it)
                    if not m.isfile():
                        continue
                    src = t.extractfile(m)
                    head = src.read(512)
                except StopIteration:
                    break
                except (tarfile.TarError, EOFError, OSError, zlib.error, lzma.LZMAError) as ex:
                    # truncated / damaged archive: keep what was read, say where it stopped
                    info["stopped"] = f"after {info['members']} files: {ex}"
                    self.stats.error(info["path"], f"tar read stopped after {info['members']} files: {ex}")
                    break
                info["members"] += 1
                self.stats.files_seen += 1
                self.stats.bytes_total += m.size
                member = _TarMember(src, head, m.size, self.tmpdir)
                nm = m.name
                while nm.startswith("./"):
                    nm = nm[2:]
                nm = nm.lstrip("/")
                e = Entry(nm, nm.rsplit("/", 1)[-1], m.size, member.open,
                          head_reader=lambda n, _h=head: _h[:n],
                          meta={"times": {k: v for k, v in (("mtime_utc", _iso_ts(m.mtime)),) if v},
                                "tar_member": {"mode": oct(m.mode), "uid": m.uid, "gid": m.gid,
                                               "uname": m.uname or None, "gname": m.gname or None}})
                try:
                    yield from self._process(e, prefix, "tar", depth, outer)
                finally:
                    member.cleanup()

    # -- DJI fc_log.log ring --------------------------------------------------
    def _walk_fclog(self, e: Entry, prefix: str, depth: int, outer: dict) -> Iterator[Candidate]:
        """Split fc_log.log into its logs (one DAT each) using the file's own index."""
        new_outer = self._container_outer(e, prefix, outer)
        where = prefix + e.path
        tmp = None
        try:
            fh = e.opener()
        except Exception as ex:  # noqa: BLE001
            self.stats.error(where, f"unreadable: {ex}")
            return
        try:
            if not fh.seekable():
                fd, tmp = tempfile.mkstemp(prefix="fclog_src_", dir=self.tmpdir)
                with os.fdopen(fd, "wb") as out, fh:
                    shutil.copyfileobj(fh, out, 4 * 1024 * 1024)
                fh = open(tmp, "rb")
            with fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                idx = fclog_index(fh, size)
                info = {"type": "fc_log", "path": where, "logs_in_index": len(idx["entries"]),
                        "write_position": idx["write_position"], "data_start": idx["data_start"],
                        "extracted": 0}
                self.stats.containers.append(info)
                for ent in idx["entries"]:
                    self._check_cancel()
                    num = ent["log_number"]
                    seg_meta = {k: ent.get(k) for k in ("log_number", "index_slot", "start", "end", "length",
                                                        "index_time_raw", "wrapped", "overwritten_from")
                                if ent.get(k) is not None}
                    seg_meta["pieces"] = [[a, b] for a, b in ent["extract"]]
                    seg_meta["index_time_note"] = "fc_log.log index field; time zone and meaning not established"
                    if ent.get("problem"):
                        self.stats.error(where, f"log {num}: {ent['problem']}")
                        continue
                    if not ent["extract"]:
                        rng = ", ".join(f"{a:#x}-{b:#x}" for a, b in ent["surviving"]) or "none"
                        remain = sum(b - a for a, b in ent["surviving"])
                        self.stats.error(where, f"log {num}: start overwritten by later logs in the ring; "
                                                f"{remain:,} bytes remain at {rng} (no DAT header, not extracted)")
                        continue
                    if ent.get("overwritten_from") is not None:
                        self.stats.error(where, f"log {num}: overwritten by later logs after its first "
                                                f"{ent['overwritten_from']:,} bytes; extracted up to that point")
                    seg = _FcLogSegment(fh, ent["extract"], self.tmpdir)
                    n = sum(b - a for a, b in ent["extract"])
                    self.stats.files_seen += 1
                    info["extracted"] += 1
                    se = Entry(f"fc_log_{num:03d}.DAT", f"fc_log_{num:03d}.DAT", n, seg.open,
                               head_reader=seg.head, meta={"fc_log_segment": seg_meta})
                    try:
                        yield from self._process(se, where + "!/", "fc_log", depth + 1, new_outer)
                    finally:
                        seg.cleanup()
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # -- iOS backups ----------------------------------------------------------
    def _ios_candidates(self, man: ios_mod.IosManifest, resolve, prefix: str, depth: int, outer: dict,
                        container_label: str) -> Iterator[Candidate]:
        summary = {"where": container_label, "format": man.format, "files": len(man.files),
                   "encrypted": man.encrypted, "info": man.info, "error": man.error}
        self.ios_manifests.append(summary)
        self.stats.containers.append({"type": "ios_backup", "path": container_label, "format": man.format,
                                      "files": len(man.files), "encrypted": man.encrypted, "error": man.error})
        if man.error:
            self.stats.error(container_label, man.error)
            return
        dji_domains: dict[str, int] = {}
        for f in man.files:
            self._check_cancel()
            hit = resolve(f)
            if hit is None:
                self.stats.skip("ios_payload_missing")
                continue
            opener, size, stored = hit
            self.stats.files_seen += 1
            self.stats.bytes_total += size or 0
            if self.policy.is_dji_app_path(f.domain):
                dji_domains[f.domain] = dji_domains.get(f.domain, 0) + 1
            meta = {"ios": {"domain": f.domain, "relative_path": f.relative_path, "file_id": f.file_id,
                            "stored_as": stored, "manifest": man.format},
                    "times": {k: v for k, v in (("mtime_utc", f.mtime_utc), ("crtime_utc", f.crtime_utc),
                                                ("ctime_utc", f.ctime_utc)) if v}}
            e = Entry(f.logical_path, f.relative_path.rsplit("/", 1)[-1], size, opener, meta)
            yield from self._process(e, prefix, "ios_backup", depth, outer)
        summary["dji_domains"] = dji_domains

    def _walk_ios_folder(self, root: Path, prefix: str, outer: dict) -> Iterator[Candidate]:
        def read_member(name):
            p = root / name
            return p.read_bytes() if p.is_file() else None
        mdb = root / "Manifest.db"
        man = ios_mod.load_manifest(read_member, mdb if mdb.is_file() else None)

        def resolve(f: ios_mod.IosFile):
            for s in f.stored_names():
                p = root / s
                if p.is_file():
                    return (lambda _p=p: open(_p, "rb")), p.stat().st_size, s
            return None
        yield from self._ios_candidates(man, resolve, prefix, 0, outer, str(root))

    def _walk_ios_zip(self, z: zipfile.ZipFile, infos, ios_prefix: str, prefix: str, depth: int,
                      outer: dict, handled: set[str]) -> Iterator[Candidate]:
        names = {i.filename.replace("\\", "/"): i for i in infos}

        def read_member(name):
            i = names.get(ios_prefix + name)
            return z.read(i) if i is not None else None
        tmp = None
        try:
            mi = names.get(ios_prefix + "Manifest.db")
            if mi is not None:
                fd, tmp = tempfile.mkstemp(prefix="Manifest_", suffix=".db", dir=self.tmpdir)
                with os.fdopen(fd, "wb") as out, z.open(mi) as src:
                    shutil.copyfileobj(src, out)
            man = ios_mod.load_manifest(read_member, Path(tmp) if tmp else None)
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        # everything under the backup root is consumed by the iOS view
        for n in names:
            if n.startswith(ios_prefix):
                handled.add(n)

        def resolve(f: ios_mod.IosFile):
            for s in f.stored_names():
                i = names.get(ios_prefix + s)
                if i is not None:
                    return (lambda _i=i: z.open(_i)), i.file_size, ios_prefix + s
            return None
        yield from self._ios_candidates(man, resolve, prefix, depth, outer, (prefix or "") + ios_prefix)

    # -- DJI Assistant export bundles -----------------------------------------------
    def _walk_assistant(self, e: Entry, prefix: str, depth: int, outer: dict, res: dict) -> Iterator[Candidate]:
        """Inflate a DJI Assistant .DAT export and yield its member files (FLY*.DAT etc.)."""
        try:
            src = e.opener()
        except Exception as ex:  # noqa: BLE001
            self.stats.error(prefix + e.path, f"unreadable: {ex}")
            return
        d = zlib.decompressobj()
        buf = bytearray()
        eof = False

        def fill(n):
            nonlocal eof
            while len(buf) < n and not eof:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    buf.extend(d.flush())
                    eof = True
                    break
                buf.extend(d.decompress(chunk))

        new_outer = dict(outer)
        new_outer["container_path"] = prefix + e.path
        if e.meta.get("deleted"):
            new_outer["deleted"] = True
        members = 0
        with src:
            try:
                while True:
                    self._check_cancel()
                    fill(ASSISTANT_HDR)
                    if len(buf) < ASSISTANT_HDR:
                        break
                    hdr = bytes(buf[:ASSISTANT_HDR])
                    del buf[:ASSISTANT_HDR]
                    length = struct.unpack_from("<I", hdr, 1)[0]
                    name = hdr[7:].split(b"\x00", 1)[0].decode("latin-1", "replace")
                    if not name or any(c in name for c in "\\/:") and not name.replace("/", "").strip():
                        break
                    fd, tmp = tempfile.mkstemp(prefix="assist_", dir=self.tmpdir)
                    got = 0
                    with os.fdopen(fd, "wb") as out:
                        while got < length:
                            fill(min(length - got, 4 * 1024 * 1024))
                            if not buf:
                                break
                            take = min(len(buf), length - got)
                            out.write(buf[:take])
                            del buf[:take]
                            got += take
                    members += 1
                    res["members"] = members
                    self.stats.files_seen += 1
                    me = Entry(name, name.rsplit("/", 1)[-1], got, lambda _t=tmp: open(_t, "rb"),
                               {"assistant_member": {"declared_length": length, "index": members}})
                    try:
                        yield from self._process(me, prefix + e.path + "!/", "assistant", depth + 1, new_outer)
                    finally:
                        try:
                            os.unlink(tmp)
                        except OSError:
                            pass
                    if got < length:
                        self.stats.error(prefix + e.path, f"member {name} truncated ({got}/{length})")
                        break
            except zlib.error as ex:
                if members == 0:
                    return  # not actually an Assistant bundle; caller treats it as a plain file
                self.stats.error(prefix + e.path, f"zlib error after {members} members: {ex}")
        if members:
            self.stats.containers.append({"type": "assistant_export", "path": prefix + e.path,
                                          "members": members})

    # -- disk images --------------------------------------------------------------
    def _walk_image(self, src: Source, files: list[Path]) -> Iterator[Candidate]:
        img = img_mod.open_image(src.subkind or "raw", [Path(f) for f in files])
        self.image_meta = {"media_size": img.get_size()}
        if isinstance(img, img_mod.EwfImgInfo):
            self.image_meta.update(img.header_values())
        self.stats.containers.append({"type": "image", "path": str(files[0]), "subkind": src.subkind,
                                      "segments": len(files), "media_size": img.get_size()})
        try:
            for ctx in img_mod.iter_filesystems(img, self.stats):
                self._check_cancel()
                for e in img_mod.walk_fs(ctx, self.stats, self.cancel):
                    yield from self._process(e, "", "image", 0, {})
                    self._check_cancel()
        finally:
            try:
                img.close()
            except Exception:  # noqa: BLE001
                pass


def _zip_head(z: zipfile.ZipFile, info: zipfile.ZipInfo, n: int) -> bytes:
    with z.open(info) as fh:
        return fh.read(n)


def _is_zip_name(name: str) -> bool:
    return name.lower().endswith(".zip")
