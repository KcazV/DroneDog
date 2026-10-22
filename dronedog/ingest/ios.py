"""iOS (iTunes/Finder) backup support: map hashed file names to domain/relativePath.

* iOS 10+: ``Manifest.db`` (SQLite, table ``Files(fileID, domain, relativePath,
  flags, file)``), payload stored at ``<fileID[:2]>/<fileID>``.
* iOS <= 9: ``Manifest.mbdb`` (binary), payload stored flat at ``<fileID>`` where
  fileID = sha1(domain + '-' + relativePath).

Encrypted backups (Manifest.plist IsEncrypted) cannot be read without the backup
password; they are reported, not attacked.
"""
from __future__ import annotations

import hashlib
import plistlib
import sqlite3
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class IosFile:
    file_id: str
    domain: str
    relative_path: str
    size: int | None = None
    mtime_utc: str | None = None
    crtime_utc: str | None = None
    ctime_utc: str | None = None
    mode: int | None = None
    inode: int | None = None

    @property
    def logical_path(self) -> str:
        return f"{self.domain}/{self.relative_path}"

    def stored_names(self) -> list[str]:
        return [f"{self.file_id[:2]}/{self.file_id}", self.file_id]


@dataclass
class IosManifest:
    format: str                       # 'manifest.db' | 'manifest.mbdb'
    files: list[IosFile] = field(default_factory=list)
    info: dict = field(default_factory=dict)       # Info.plist / Manifest.plist summary
    encrypted: bool = False
    error: str | None = None


def _ts(v) -> str | None:
    try:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        v = int(v)
        if v <= 0:
            return None
        return datetime.fromtimestamp(v, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _file_blob_meta(blob: bytes | None) -> dict:
    """Decode the NSKeyedArchiver MBFile blob for Size / timestamps."""
    if not blob:
        return {}
    try:
        pl = plistlib.loads(blob)
        objs = pl["$objects"]
        top = pl["$top"]["root"]
        root = objs[top.data if isinstance(top, plistlib.UID) else int(top)]
        out = {}
        for k in ("Size", "LastModified", "Birth", "LastStatusChange", "Mode", "InodeNumber"):
            v = root.get(k)
            if isinstance(v, plistlib.UID):
                v = objs[v.data]
            out[k] = v
        return out
    except Exception:  # noqa: BLE001
        return {}


def parse_manifest_db(path: Path) -> list[IosFile]:
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(Files)")}
        has_blob = "file" in cols
        q = "SELECT fileID, domain, relativePath, flags" + (", file" if has_blob else ", NULL") + " FROM Files"
        out = []
        for fid, dom, rel, flags, blob in con.execute(q):
            if flags is not None and int(flags) != 1:  # 1 file, 2 dir, 4 symlink
                continue
            m = _file_blob_meta(blob)
            out.append(IosFile(fid, dom or "", rel or "", m.get("Size"), _ts(m.get("LastModified")),
                               _ts(m.get("Birth")), _ts(m.get("LastStatusChange")), m.get("Mode"),
                               m.get("InodeNumber")))
        return out
    finally:
        con.close()


def _mbdb_str(b: bytes, i: int) -> tuple[str | None, int]:
    (n,) = struct.unpack_from(">H", b, i)
    i += 2
    if n == 0xFFFF:
        return None, i
    return b[i:i + n].decode("utf-8", "replace"), i + n


def parse_manifest_mbdb(data: bytes) -> list[IosFile]:
    if data[:4] != b"mbdb":
        raise ValueError("not an mbdb file")
    i = 6
    out = []
    while i < len(data):
        domain, i = _mbdb_str(data, i)
        path, i = _mbdb_str(data, i)
        _link, i = _mbdb_str(data, i)
        _dhash, i = _mbdb_str(data, i)
        _ekey, i = _mbdb_str(data, i)
        mode, inode, _uid, _gid, mtime, _atime, ctime, size, _prot, nprops = struct.unpack_from(">HQIIIIIQBB", data, i)
        i += struct.calcsize(">HQIIIIIQBB")
        for _ in range(nprops):
            _k, i = _mbdb_str(data, i)
            _v, i = _mbdb_str(data, i)
        if (mode & 0xF000) != 0x8000:  # regular files only
            continue
        domain = domain or ""
        path = path or ""
        fid = hashlib.sha1(f"{domain}-{path}".encode("utf-8")).hexdigest()
        out.append(IosFile(fid, domain, path, size, _ts(mtime), None, _ts(ctime), mode, inode))
    return out


def read_plist_summary(info_bytes: bytes | None, manifest_plist: bytes | None) -> tuple[dict, bool]:
    info: dict = {}
    encrypted = False
    for blob, keys in ((info_bytes, ("Device Name", "Display Name", "Product Type", "Product Version",
                                     "Serial Number", "Unique Identifier", "IMEI", "Phone Number",
                                     "Last Backup Date", "Build Version")),
                       (manifest_plist, ("IsEncrypted", "Version", "Date", "SystemDomainsVersion"))):
        if not blob:
            continue
        try:
            pl = plistlib.loads(blob)
        except Exception:  # noqa: BLE001
            continue
        for k in keys:
            if k in pl:
                v = pl[k]
                info[k] = _ts(v) if isinstance(v, datetime) else v if isinstance(v, (str, int, float, bool)) else str(v)
        if "Lockdown" in pl and isinstance(pl["Lockdown"], dict):
            for k in ("DeviceName", "ProductType", "ProductVersion", "SerialNumber", "UniqueDeviceID"):
                if k in pl["Lockdown"]:
                    info.setdefault(f"Lockdown.{k}", str(pl["Lockdown"][k]))
        if pl.get("IsEncrypted"):
            encrypted = True
    return info, encrypted


def load_manifest(read_member, manifest_db_path: Path | None) -> IosManifest:
    """read_member(name) -> bytes|None for small root files (Info.plist, Manifest.plist, Manifest.mbdb).
    manifest_db_path: a local filesystem path to Manifest.db if present (sqlite needs a file)."""
    info, enc = read_plist_summary(read_member("Info.plist"), read_member("Manifest.plist"))
    if manifest_db_path is not None:
        man = IosManifest("manifest.db", info=info, encrypted=enc)
        try:
            man.files = parse_manifest_db(manifest_db_path)
        except sqlite3.DatabaseError as e:
            man.error = (f"Manifest.db unreadable ({e}); backup is encrypted - the backup password is "
                         f"required" if enc else f"Manifest.db unreadable: {e}")
        return man
    mbdb = read_member("Manifest.mbdb")
    man = IosManifest("manifest.mbdb", info=info, encrypted=enc)
    if mbdb is None:
        man.error = "no Manifest.db / Manifest.mbdb"
        return man
    try:
        man.files = parse_manifest_mbdb(mbdb)
    except Exception as e:  # noqa: BLE001
        man.error = f"Manifest.mbdb unreadable: {e}"
    return man
