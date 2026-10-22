"""Classify examiner-supplied paths into evidence sources.

``classify(path)`` decides what one path is (loose file, folder, mounted drive,
zip, iOS backup, disk image + its segment set, raw device).
``expand_inputs(paths)`` additionally splits dropped folders: every disk image,
zip and iOS backup folder found inside becomes its own evidence item (so each is
hashed and recorded on its own), and the rest of the folder is one 'folder'
evidence item walked with those paths excluded.
"""
from __future__ import annotations

import os
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXTS = {".e01", ".ex01", ".001", ".dd", ".img", ".raw", ".bin", ".dmg_raw", ".mmc", ".ima"}
RAW_CANDIDATE_EXTS = {".bin", ".raw", ".img", ".dd", ".ima", ".mmc", ""}
EWF_SEG_RE = re.compile(r"^(?P<stem>.*)\.(?P<x>[Ee])(?P<x2>[Xx])?(?P<n>\d\d)$")
SPLIT_RE = re.compile(r"^(?P<stem>.*)\.(?P<n>\d{3})$")
WHOLE_DEVICE_RE = re.compile(r"(?i)^(mmcblk\d+|sd[a-z]|nvme\d+n\d+|userdata|blk\d+)(\.\w+)?$")


@dataclass
class Source:
    path: Path                       # what the examiner gave (first segment for sets)
    kind: str                        # C5 evidence kind: file|folder|zip|image|sd
    subkind: str | None = None       # ios_backup, e01, split_raw, raw, raw_device, drive_root
    files: list[Path] = field(default_factory=list)   # all files making up the evidence
    size: int = 0
    exclude: set[str] = field(default_factory=set)    # folder walk: abs paths handled elsewhere
    parent_label: str | None = None  # set when split out of a dropped folder
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.path.name or str(self.path)


def is_raw_device(path: str) -> bool:
    return str(path).startswith("\\\\.\\")


def is_drive_root(p: Path) -> bool:
    s = str(p)
    return bool(re.match(r"^[A-Za-z]:\\?$", s)) or (p.anchor and str(p) == p.anchor)


def ewf_segments(first: Path) -> list[Path]:
    """E01, E02 ... E99, EAA ... ; Ex01 ... likewise. Returns existing segments in order."""
    m = EWF_SEG_RE.match(first.name)
    if not m:
        return [first]
    stem, x, x2 = m.group("stem"), m.group("x"), m.group("x2") or ""
    segs = []
    names = {c.name.lower(): c for c in first.parent.iterdir()} if first.parent.exists() else {}
    # numeric part
    for i in range(1, 100):
        cand = f"{stem}.{x}{x2}{i:02d}"
        hit = names.get(cand.lower())
        if hit is None:
            break
        segs.append(hit)
    else:
        # alphabetic continuation EAA..EZZ (libewf convention)
        base = ord(x.upper())
        for a in range(base, ord("Z") + 1):
            for b in range(ord("A"), ord("Z") + 1):
                ext = f"{chr(a)}{chr(b)}" if not x2 else f"{chr(a)}x{chr(b)}"
                cand = f"{stem}.{ext}"
                hit = names.get(cand.lower())
                if hit is None:
                    return segs
                segs.append(hit)
    return segs or [first]


def split_segments(first: Path) -> list[Path]:
    m = SPLIT_RE.match(first.name)
    if not m:
        return [first]
    stem = m.group("stem")
    segs = []
    for i in range(int(m.group("n")), 1000):
        cand = first.parent / f"{stem}.{i:03d}"
        if not cand.exists():
            break
        segs.append(cand)
    return segs


def looks_like_disk(path: Path) -> str | None:
    """Content check for extension-less / .bin dumps: MBR, GPT or a filesystem boot sector."""
    try:
        with open(path, "rb") as fh:
            s0 = fh.read(4096)
    except OSError:
        return None
    if len(s0) < 1024:
        return None
    if s0[512:520] == b"EFI PART":
        return "gpt"
    if s0[3:11] in (b"NTFS    ", b"EXFAT   "):
        return "fs:" + s0[3:11].decode().strip().lower()
    if s0[54:59] == b"FAT12" or s0[54:59] == b"FAT16" or s0[82:87] == b"FAT32":
        return "fs:fat"
    if s0[510:512] == b"\x55\xaa":
        # plausible MBR: partition entries have boot flag 0x00/0x80 and at least one non-empty type
        ok, any_part = True, False
        for i in range(4):
            e = s0[446 + 16 * i: 462 + 16 * i]
            if e[0] not in (0, 0x80):
                ok = False
            if e[4]:
                lba, n = struct.unpack_from("<II", e, 8)
                if lba and n:
                    any_part = True
        if ok and any_part:
            return "mbr"
    # ext superblock magic at 1024+56
    try:
        with open(path, "rb") as fh:
            fh.seek(1024 + 56)
            if fh.read(2) == b"\x53\xef":
                return "fs:ext"
    except OSError:
        pass
    return None


def zip_has_ios_manifest(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.replace("\\", "/").rsplit("/", 1)[-1] in ("Manifest.db", "Manifest.mbdb"):
                    return n
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def is_ios_backup_dir(p: Path) -> bool:
    return (p / "Manifest.db").is_file() or (p / "Manifest.mbdb").is_file()


def is_zip(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            sig = fh.read(4)
    except OSError:
        return False
    return sig in (b"PK\x03\x04", b"PK\x05\x06") or (path.suffix.lower() == ".zip" and zipfile.is_zipfile(path))


def _image_kind(p: Path, strict: bool = False) -> tuple[str, list[Path]] | None:
    """strict=True (folder scan): split-raw / generic raw must also pass a content check."""
    name = p.name
    low = name.lower()
    if EWF_SEG_RE.match(name):
        m = EWF_SEG_RE.match(name)
        if int(m.group("n")) == 1:
            return ("e01" if not m.group("x2") else "ex01"), ewf_segments(p)
        return None
    if SPLIT_RE.match(name):
        m = SPLIT_RE.match(name)
        if int(m.group("n")) in (0, 1):
            if strict and not looks_like_disk(p):
                return None
            return "split_raw", split_segments(p)
        return None
    ext = p.suffix.lower()
    if ext in (".dd", ".img", ".raw", ".ima", ".mmc") or WHOLE_DEVICE_RE.match(low):
        if looks_like_disk(p) or (ext in (".dd", ".img", ".raw") and not strict):
            return "raw", [p]
    if ext in RAW_CANDIDATE_EXTS and p.stat().st_size >= 1024 * 1024 and looks_like_disk(p):
        return "raw", [p]
    return None


def is_image_segment_tail(p: Path) -> bool:
    """True for .E02.., .002.. (non-first segments)."""
    m = EWF_SEG_RE.match(p.name)
    if m:
        return int(m.group("n")) != 1
    m = SPLIT_RE.match(p.name)
    if m:
        return int(m.group("n")) > 1
    return False


def _has_first_segment(p: Path) -> bool:
    m = EWF_SEG_RE.match(p.name)
    if m:
        return any((p.parent / f"{m.group('stem')}.{m.group('x')}{m.group('x2') or ''}01").exists()
                   for _ in (0,)) or (p.parent / f"{m.group('stem')}.E01").exists()
    m = SPLIT_RE.match(p.name)
    if m:
        return (p.parent / f"{m.group('stem')}.001").exists() or (p.parent / f"{m.group('stem')}.000").exists()
    return False


def classify(path: str | os.PathLike) -> Source:
    sp = str(path)
    if is_raw_device(sp):
        return Source(Path(sp), "sd", "raw_device", [Path(sp)], 0)
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(sp)
    if p.is_dir():
        if is_ios_backup_dir(p):
            return Source(p, "folder", "ios_backup", [], 0)
        return Source(p, "sd" if is_drive_root(p) else "folder", "drive_root" if is_drive_root(p) else None, [], 0)
    ik = _image_kind(p)
    if ik:
        sub, segs = ik
        return Source(p, "image", sub, segs, sum(s.stat().st_size for s in segs))
    size = p.stat().st_size
    if is_zip(p):
        return Source(p, "zip", "ios_backup" if zip_has_ios_manifest(p) else None, [p], size)
    return Source(p, "file", None, [p], size)


def expand_inputs(paths) -> list[Source]:
    """Classify each input; split dropped folders into images/zips/iOS backups + remainder."""
    out: list[Source] = []
    seen: set[str] = set()
    for raw in paths:
        src = classify(raw)
        if src.kind not in ("folder", "sd") or src.subkind in ("ios_backup", "raw_device"):
            key = os.path.normcase(str(src.path.resolve() if not is_raw_device(str(src.path)) else src.path))
            if key not in seen:
                seen.add(key)
                out.append(src)
            continue
        inner: list[Source] = []
        exclude: set[str] = set()
        other_files = 0
        for root, dirs, files in os.walk(src.path):
            rp = Path(root)
            # iOS backup subfolders become their own source
            keep = []
            for d in dirs:
                dp = rp / d
                if is_ios_backup_dir(dp):
                    s = classify(dp)
                    s.parent_label = str(src.path)
                    inner.append(s)
                    exclude.add(os.path.normcase(str(dp)))
                else:
                    keep.append(d)
            dirs[:] = keep
            for f in files:
                fp = rp / f
                try:
                    if is_image_segment_tail(fp) and _has_first_segment(fp):
                        exclude.add(os.path.normcase(str(fp)))
                        continue
                    ik = _image_kind(fp, strict=True)
                    if ik or is_zip(fp):
                        s = classify(fp)
                        s.parent_label = str(src.path)
                        inner.append(s)
                        for seg in s.files:
                            exclude.add(os.path.normcase(str(seg)))
                        continue
                except OSError:
                    pass
                other_files += 1
        # segment tails of images only count as excluded when their head was found
        src.exclude = exclude
        for s in inner:
            key = os.path.normcase(str(s.path.resolve()))
            if key not in seen:
                seen.add(key)
                out.append(s)
        if other_files:
            key = os.path.normcase(str(src.path.resolve()))
            if key not in seen:
                seen.add(key)
                out.append(src)
    return out
