"""Disk image access: E01/Ex01 (pyewf), split raw, single raw, raw devices; TSK walking.

Every partition found by the TSK volume system (MBR/GPT/...) is tried as a
filesystem; if there is no volume system, offset 0 is tried. Each filesystem is
walked recursively INCLUDING deleted entries TSK exposes (unallocated names,
$OrphanFiles). For each entry we record partition index/offset, meta address,
first-data-byte image offset and the data runs, and for deleted FAT files we check
the FAT for clusters that have since been reallocated (possible overwrite).
"""
from __future__ import annotations

import bisect
import os
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytsk3

from dronedog.ingest.entries import Entry, RandomReader, TolerantRandomReader, WalkStats, buffered

try:
    import pyewf  # type: ignore
except Exception:  # noqa: BLE001
    pyewf = None

TSK_VERSION = pytsk3.TSK_VERSION_STR if hasattr(pytsk3, "TSK_VERSION_STR") else "?"
SKIP_VIRTUAL = {"$MBR", "$FAT1", "$FAT2", "$FAT3", "$Extend"}
FS_NAMES = {}
for _n in dir(pytsk3):
    if _n.startswith("TSK_FS_TYPE_") and not _n.endswith("_DETECT"):
        try:
            FS_NAMES[int(getattr(pytsk3, _n))] = _n[len("TSK_FS_TYPE_"):].lower()
        except Exception:  # noqa: BLE001
            pass


def lib_versions() -> dict:
    v = {"pytsk3/tsk": TSK_VERSION}
    if pyewf is not None:
        v["pyewf"] = pyewf.get_version()
    return v


# -- image backends -------------------------------------------------------
class EwfImgInfo(pytsk3.Img_Info):
    def __init__(self, segments: list[str]):
        if pyewf is None:
            raise RuntimeError("pyewf (libewf-python) not installed")
        names = pyewf.glob(segments[0]) if hasattr(pyewf, "glob") else segments
        self._h = pyewf.handle()
        self._h.open(names)
        self.segments = names
        super().__init__(url="", type=pytsk3.TSK_IMG_TYPE_EXTERNAL)

    def close(self):
        self._h.close()

    def read(self, offset, size):
        self._h.seek(offset)
        return self._h.read(size)

    def get_size(self):
        return self._h.get_media_size()

    def header_values(self) -> dict:
        out = {}
        try:
            out.update({f"header.{k}": v for k, v in self._h.get_header_values().items()})
        except Exception:  # noqa: BLE001
            pass
        try:
            out.update({f"stored_hash.{k}": v for k, v in self._h.get_hash_values().items()})
        except Exception:  # noqa: BLE001
            pass
        for k in ("media_size", "bytes_per_sector", "number_of_sectors"):
            try:
                out[k] = getattr(self._h, k)
            except Exception:  # noqa: BLE001
                pass
        return out


class SplitRawImgInfo(pytsk3.Img_Info):
    """Concatenation of .001/.002/... segments (or a single raw file)."""

    def __init__(self, segments: list[str]):
        self._fh = [open(s, "rb") for s in segments]
        self._starts = []
        pos = 0
        for fh in self._fh:
            self._starts.append(pos)
            pos += os.fstat(fh.fileno()).st_size
        self._size = pos
        super().__init__(url="", type=pytsk3.TSK_IMG_TYPE_EXTERNAL)

    def close(self):
        for fh in self._fh:
            fh.close()

    def read(self, offset, size):
        out = bytearray()
        while size > 0 and offset < self._size:
            i = bisect.bisect_right(self._starts, offset) - 1
            fh = self._fh[i]
            seg_end = self._starts[i + 1] if i + 1 < len(self._starts) else self._size
            n = min(size, seg_end - offset)
            fh.seek(offset - self._starts[i])
            chunk = fh.read(n)
            if not chunk:
                break
            out += chunk
            offset += len(chunk)
            size -= len(chunk)
        return bytes(out)

    def get_size(self):
        return self._size


def open_image(subkind: str, files: list[Path]):
    if subkind in ("e01", "ex01"):
        return EwfImgInfo([str(f) for f in files])
    if subkind == "raw_device":
        return pytsk3.Img_Info(str(files[0]))
    return SplitRawImgInfo([str(f) for f in files])


def image_reader(img) -> RandomReader:
    return RandomReader(img.read, img.get_size())


# -- helpers ----------------------------------------------------------------
def _iso(ts) -> str | None:
    try:
        if not ts:
            return None
        return datetime.fromtimestamp(int(ts), timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _dec(b) -> str:
    if isinstance(b, bytes):
        return b.decode("utf-8", "replace")
    return str(b)


@dataclass
class FatGeom:
    fs_offset: int
    bps: int
    spc: int
    reserved: int
    nfats: int
    fatsz: int
    first_data_sector: int
    fat_bits: int
    clusters: int


def fat_geometry(img, fs_offset: int) -> FatGeom | None:
    bs = img.read(fs_offset, 512)
    if len(bs) < 512 or bs[510:512] != b"\x55\xaa":
        return None
    bps, spc, reserved, nfats, root_ents, tot16 = struct.unpack_from("<HBHBHH", bs, 11)
    fatsz16 = struct.unpack_from("<H", bs, 22)[0]
    tot32 = struct.unpack_from("<I", bs, 32)[0]
    fatsz32 = struct.unpack_from("<I", bs, 36)[0]
    if not bps or not spc:
        return None
    fatsz = fatsz16 or fatsz32
    tot = tot16 or tot32
    root_secs = ((root_ents * 32) + bps - 1) // bps
    fds = reserved + nfats * fatsz + root_secs
    clusters = (tot - fds) // spc
    bits = 12 if clusters < 4085 else 16 if clusters < 65525 else 32
    return FatGeom(fs_offset, bps, spc, reserved, nfats, fatsz, fds, bits, clusters)


def fat_cluster_allocated(img, g: FatGeom, cluster: int) -> bool | None:
    if cluster < 2 or cluster >= g.clusters + 2:
        return None
    fat0 = g.fs_offset + g.reserved * g.bps
    if g.fat_bits == 32:
        v = struct.unpack("<I", img.read(fat0 + cluster * 4, 4))[0] & 0x0FFFFFFF
    elif g.fat_bits == 16:
        v = struct.unpack("<H", img.read(fat0 + cluster * 2, 2))[0]
    else:
        off = cluster + cluster // 2
        raw = struct.unpack("<H", img.read(fat0 + off, 2))[0]
        v = (raw >> 4) if cluster & 1 else (raw & 0x0FFF)
    return v != 0


def data_runs(f, fs_offset: int, block_size: int) -> list[tuple[int, int]]:
    """[(absolute image offset, length bytes)] for the default data attribute."""
    runs = []
    try:
        for attr in f:
            t = attr.info.type
            if t not in (pytsk3.TSK_FS_ATTR_TYPE_DEFAULT, pytsk3.TSK_FS_ATTR_TYPE_NTFS_DATA):
                continue
            if attr.info.name not in (None, b"", b"$Data", "") and t == pytsk3.TSK_FS_ATTR_TYPE_NTFS_DATA:
                continue  # alternate data stream
            for run in attr:
                if run.flags & getattr(pytsk3, "TSK_FS_ATTR_RUN_FLAG_SPARSE", 0x02) or \
                        run.flags & getattr(pytsk3, "TSK_FS_ATTR_RUN_FLAG_FILLER", 0x01):
                    continue
                runs.append((fs_offset + run.addr * block_size, run.len * block_size))
            break
    except Exception:  # noqa: BLE001
        pass
    return runs


# -- walking ---------------------------------------------------------------
@dataclass
class FsContext:
    img: object
    fs: object
    part_index: int | None
    part_offset: int
    fs_type: str
    block_size: int
    fat: FatGeom | None
    label: str             # 'p1' / 'p0' / 'fs'


def iter_filesystems(img, stats: WalkStats) -> Iterator[FsContext]:
    found = False
    try:
        vs = pytsk3.Volume_Info(img)
        bsz = vs.info.block_size
        idx = 0
        for part in vs:
            if not (part.flags & pytsk3.TSK_VS_PART_FLAG_ALLOC):
                continue
            idx += 1
            off = part.start * bsz
            desc = _dec(part.desc)
            try:
                fs = pytsk3.FS_Info(img, offset=off)
            except Exception as e:  # noqa: BLE001
                stats.containers.append({"type": "partition", "index": idx, "offset": off, "desc": desc,
                                         "length": part.len * bsz, "filesystem": None,
                                         "note": f"no filesystem TSK can open: {str(e).splitlines()[0][:160]}"})
                continue
            found = True
            yield _ctx(img, fs, idx, off, f"p{idx}", stats, desc, part.len * bsz)
    except Exception:  # noqa: BLE001 - no volume system
        pass
    if not found:
        try:
            fs = pytsk3.FS_Info(img, offset=0)
        except Exception as e:  # noqa: BLE001
            stats.error("image", f"no volume system and no filesystem at offset 0: {str(e).splitlines()[0][:200]}")
            return
        yield _ctx(img, fs, 0, 0, "fs", stats, "whole image", img.get_size())


def _ctx(img, fs, idx, off, label, stats, desc, length) -> FsContext:
    ftype = FS_NAMES.get(int(fs.info.ftype), str(fs.info.ftype))
    fat = fat_geometry(img, off) if ftype.startswith("fat") else None
    stats.containers.append({"type": "partition", "index": idx, "offset": off, "desc": desc, "length": length,
                             "filesystem": ftype, "block_size": fs.info.block_size})
    return FsContext(img, fs, idx, off, ftype, fs.info.block_size, fat, label)


def walk_fs(ctx: FsContext, stats: WalkStats, cancel=None, max_depth: int = 64) -> Iterator[Entry]:
    fs = ctx.fs
    root = fs.open_dir(path="/")
    stack = [(root, "", 0, False)]
    visited_alloc_dirs: set[int] = set()
    while stack:
        d, dpath, depth, parent_deleted = stack.pop()
        try:
            it = list(d)
        except Exception as e:  # noqa: BLE001
            stats.error(f"{ctx.label}/{dpath}", f"directory unreadable: {e}")
            continue
        for f in it:
            if cancel is not None and cancel.is_set():
                return
            info = f.info
            name = _dec(info.name.name)
            if name in (".", ".."):
                continue
            if depth == 0 and name in SKIP_VIRTUAL:
                continue
            name_deleted = bool(info.name.flags & pytsk3.TSK_FS_NAME_FLAG_UNALLOC)
            meta = info.meta
            path = f"{dpath}/{name}" if dpath else name
            if meta is None:
                if name_deleted:
                    stats.deleted_seen += 1
                    stats.skip("deleted_name_without_metadata")
                continue
            meta_unalloc = bool(meta.flags & pytsk3.TSK_FS_META_FLAG_UNALLOC)
            deleted = name_deleted or meta_unalloc or parent_deleted or name == "$OrphanFiles"
            mtype = meta.type
            if mtype == pytsk3.TSK_FS_META_TYPE_DIR:
                stats.dirs_seen += 1
                if depth >= max_depth:
                    stats.skip("max_depth")
                    continue
                if not deleted:
                    if meta.addr in visited_alloc_dirs:
                        continue
                    visited_alloc_dirs.add(meta.addr)
                try:
                    sub = f.as_directory()
                except Exception:  # noqa: BLE001
                    stats.skip("dir_open_failed")
                    continue
                stack.append((sub, path, depth + 1, deleted or parent_deleted))
                continue
            if mtype != pytsk3.TSK_FS_META_TYPE_REG:
                continue
            stats.files_seen += 1
            if deleted:
                stats.deleted_seen += 1
            size = int(meta.size)
            stats.bytes_total += size
            em = {
                "deleted": deleted, "meta_addr": int(meta.addr), "fs_type": ctx.fs_type,
                "partition_index": ctx.part_index, "partition_offset": ctx.part_offset,
                "name_unalloc": name_deleted, "meta_unalloc": meta_unalloc,
                "times": {k: v for k, v in (("mtime_utc", _iso(meta.mtime)), ("atime_utc", _iso(meta.atime)),
                                            ("ctime_utc", _iso(meta.ctime)), ("crtime_utc", _iso(meta.crtime)))
                          if v},
                "_tsk_file": f, "_ctx": ctx,
            }
            if ctx.fs_type.startswith("fat") or ctx.fs_type == "exfat":
                em["times_note"] = "FAT/exFAT timestamps are device local time with no zone; shown as if UTC"
            if name_deleted and not meta_unalloc:
                em["overwrite_status"] = "meta_reallocated"

            def opener(_f=f, _size=size):
                return buffered(TolerantRandomReader(lambda off, n: _f.read_random(off, n), _size))

            def head(n, _f=f, _size=size):
                n = min(n, _size)
                return _f.read_random(0, n) if n > 0 else b""

            yield Entry(f"{ctx.label}/{path}", name, size, opener, em, head)


def finish_image_provenance(entry: Entry) -> dict:
    """Compute offset/runs/overwrite status for a selected entry (costly bits done lazily)."""
    m = entry.meta
    f, ctx = m.get("_tsk_file"), m.get("_ctx")
    out = {}
    if f is None or ctx is None:
        return out
    runs = data_runs(f, ctx.part_offset, ctx.block_size)
    img_size = ctx.img.get_size()
    if runs and any(off + ln > img_size for off, ln in runs):
        present = sum(max(0, min(off + ln, img_size) - off) for off, ln in runs if off < img_size)
        out["partial_image"] = (f"file data extends beyond the end of the image ({img_size} bytes): "
                                f"~{present} of {sum(ln for _, ln in runs)} allocated bytes are present")
    if runs:
        out["offset"] = runs[0][0]
        if len(runs) > 1:
            out["runs"] = runs[:64]
            out["fragmented"] = True
    elif entry.size and entry.size < 1024:
        out["resident"] = True
    status = m.get("overwrite_status")
    if m.get("deleted"):
        if status is None and ctx.fat is not None and runs:
            g = ctx.fat
            total = alloc = 0
            for off, ln in runs:
                sec = (off - ctx.part_offset) // g.bps
                first = (sec - g.first_data_sector) // g.spc + 2
                n_cl = max(1, -(-ln // (g.bps * g.spc)))
                for c in range(first, first + n_cl):
                    r = fat_cluster_allocated(ctx.img, g, c)
                    if r is None:
                        continue
                    total += 1
                    alloc += 1 if r else 0
            if total:
                status = "none_detected" if alloc == 0 else ("full" if alloc == total else "partial")
                out["clusters_checked"] = total
                out["clusters_reallocated"] = alloc
            out["recovery_note"] = ("FAT deleted file: content recovered as contiguous clusters from the "
                                    "original first cluster (FAT chain is cleared on delete); "
                                    + {"none_detected": "none of those clusters is allocated now.",
                                       "partial": "some clusters are now allocated to other files - content may be partially overwritten.",
                                       "full": "all clusters are now allocated to other files - content is likely overwritten."}
                                    .get(status, "allocation could not be checked."))
        elif status is None:
            status = "unknown"
            out["recovery_note"] = ("deleted entry recovered via TSK metadata; data blocks may have been "
                                    "reallocated (not checked for this filesystem type)")
        elif status == "meta_reallocated":
            out["recovery_note"] = ("name is deleted but its metadata entry has been reused by another file: "
                                    "content shown is the NEW file's, not the deleted one's")
    else:
        status = "allocated"
    out["overwrite_status"] = status
    return out
