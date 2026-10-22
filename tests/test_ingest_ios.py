"""iOS backups: Manifest.db (folder + zipped) and Manifest.mbdb hashed-name mapping."""
from __future__ import annotations

import hashlib
import io
import plistlib
import sqlite3
import struct
import zipfile

import pytest

from dronedog.case import Case
from dronedog.ingest import Walker, classify
from dronedog.ingest.ios import parse_manifest_mbdb
from dronedog.pipeline import run_import
from test_ingest_fatbuilder import fake_core, fake_dat, fake_txt  # noqa: F401

FILES = [
    # (domain, relativePath, data, expected_selected)
    ("AppDomain-com.dji.go", "Documents/FlightRecords/DJIFlightRecord_2017-06-01_[12-00-00].txt", fake_txt(1), True),
    ("AppDomain-com.dji.go", "Documents/FlightRecords/MCDatFlightRecords/17-06-01-12-00-00_FLY001.DAT",
     fake_dat(1), True),
    ("AppDomain-com.dji.golite", "Library/Caches/FlightRecord/tmp_record.bin", b"opaque bytes" * 20, True),
    ("AppDomain-com.dji.go", "Library/Preferences/com.dji.go.plist", b"bplist00" + bytes(50), False),
    ("AppDomain-com.apple.mobilesafari", "Library/Safari/History.db", b"SQLite format 3\0" + bytes(100), False),
    ("HomeDomain", "Library/SMS/sms.db", b"SQLite format 3\0" + bytes(100), False),
    # DJI log in an unexpected domain: found by name + sniff anyway
    ("AppDomainGroup-group.com.example.share", "Inbox/DJIFlightRecord_2018-01-01_[01-01-01].txt", fake_txt(2), True),
]


def _fid(domain, rel):
    return hashlib.sha1(f"{domain}-{rel}".encode()).hexdigest()


def _file_blob(size: int) -> bytes:
    # NSKeyedArchiver-shaped MBFile blob with Size/LastModified/Birth
    objs = ["$null", {"Size": size, "LastModified": 1496318400, "Birth": 1496318000,
                      "$class": plistlib.UID(2)}, {"$classname": "MBFile"}]
    return plistlib.dumps({"$archiver": "NSKeyedArchiver", "$version": 100000,
                           "$top": {"root": plistlib.UID(1)}, "$objects": objs}, fmt=plistlib.FMT_BINARY)


def _manifest_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")
    con.execute("CREATE TABLE Properties (key TEXT PRIMARY KEY, value BLOB)")
    for dom, rel, data, _ in FILES:
        con.execute("INSERT INTO Files VALUES (?,?,?,?,?)", (_fid(dom, rel), dom, rel, 1, _file_blob(len(data))))
    con.execute("INSERT INTO Files VALUES (?,?,?,?,?)", (_fid("AppDomain-com.dji.go", "Documents"),
                                                        "AppDomain-com.dji.go", "Documents", 2, None))
    con.commit()
    con.close()


def _plists():
    info = plistlib.dumps({"Device Name": "Examiner iPad", "Product Type": "iPad6,11",
                           "Serial Number": "DMPTEST123", "Product Version": "10.3.2"})
    man = plistlib.dumps({"IsEncrypted": False, "Version": "10.0"})
    return info, man


def build_backup_folder(root):
    root.mkdir(parents=True)
    _manifest_db(root / "Manifest.db")
    info, man = _plists()
    (root / "Info.plist").write_bytes(info)
    (root / "Manifest.plist").write_bytes(man)
    for dom, rel, data, _ in FILES:
        fid = _fid(dom, rel)
        (root / fid[:2]).mkdir(exist_ok=True)
        (root / fid[:2] / fid).write_bytes(data)
    return root


def _expected():
    return {f"{d}/{r}" for d, r, _, sel in FILES if sel}


def test_ios_folder_mapping(tmp_path):
    root = build_backup_folder(tmp_path / "00008030-001A2B3C4D5E")
    src = classify(root)
    assert src.kind == "folder" and src.subkind == "ios_backup"
    w = Walker()
    got = {c.inner_path: c for c in w.walk(src)}
    assert set(got) == _expected()
    c = got["AppDomain-com.dji.go/Documents/FlightRecords/DJIFlightRecord_2017-06-01_[12-00-00].txt"]
    rel = "Documents/FlightRecords/DJIFlightRecord_2017-06-01_[12-00-00].txt"
    assert c.ios["domain"] == "AppDomain-com.dji.go" and c.ios["relative_path"] == rel
    assert c.ios["file_id"] == _fid("AppDomain-com.dji.go", rel)
    assert c.ios["stored_as"] == f"{c.ios['file_id'][:2]}/{c.ios['file_id']}"
    assert c.app_hint == "com.dji.go" and c.container == "ios_backup"
    assert c.times["mtime_utc"].startswith("2017-06-01")
    assert "app:DJI app log directory" in got[
        "AppDomain-com.dji.golite/Library/Caches/FlightRecord/tmp_record.bin"].reasons
    man = w.ios_manifests[0]
    assert man["info"]["Serial Number"] == "DMPTEST123" and man["dji_domains"]["AppDomain-com.dji.go"] == 3


def test_ios_zipped_backup_in_pipeline(tmp_path, fake_core):
    root = build_backup_folder(tmp_path / "bk" / "UDID1234")
    z = tmp_path / "ios_backup.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_file():
                zf.write(p, "Backup/" + p.relative_to(root).as_posix())
        zf.writestr("extraction_report.txt", "report")
    src = classify(z)
    assert src.kind == "zip" and src.subkind == "ios_backup"
    case = Case.create(tmp_path / "case", name="ios", examiner="e")
    s = run_import(case, [z])
    assert s["state"] == "done", s
    rows = case.store.query("SELECT * FROM artifact_sources")
    assert {r["inner_path"] for r in rows} == _expected()
    r = [r for r in rows if r["inner_path"].endswith("FLY001.DAT")][0]
    assert r["ios_domain"] == "AppDomain-com.dji.go" and r["ios_file_id"] and r["container"] == "ios_backup"
    assert case.store.one("SELECT COUNT(*) n FROM artifacts WHERE status='parsed'")["n"] == 3
    ev = case.store.one("SELECT * FROM evidence")
    import json
    meta = json.loads(ev["meta_json"])
    assert meta["ios_backups"][0]["info"]["Device Name"] == "Examiner iPad"
    case.close()


def _mbdb(records):
    def s(v):
        if v is None:
            return b"\xff\xff"
        b = v.encode()
        return struct.pack(">H", len(b)) + b
    out = b"mbdb\x05\x00"
    for dom, path, size, mode in records:
        out += s(dom) + s(path) + s(None) + s(None) + s(None)
        out += struct.pack(">HQIIIIIQBB", mode, 1, 501, 501, 1496318400, 1496318400, 1496318400, size, 0, 0)
    return out


def test_mbdb_parse(tmp_path):
    data = _mbdb([("AppDomain-com.dji.go", "Documents", 0, 0x41ED),
                  ("AppDomain-com.dji.go", "Documents/FlightRecords/DJIFlightRecord_2016-01-01_[00-00-00].txt",
                   4000, 0x81A4)])
    files = parse_manifest_mbdb(data)
    assert len(files) == 1
    f = files[0]
    assert f.file_id == _fid("AppDomain-com.dji.go", f.relative_path) and f.size == 4000
    # folder backup using mbdb + flat file names
    root = tmp_path / "old"
    root.mkdir()
    (root / "Manifest.mbdb").write_bytes(data)
    (root / f.file_id).write_bytes(fake_txt(9))
    got = [c.inner_path for c in Walker().walk(classify(root))]
    assert got == [f.logical_path]


def test_encrypted_backup_reported(tmp_path):
    root = tmp_path / "enc"
    root.mkdir()
    (root / "Manifest.db").write_bytes(b"\x8a\x13" + bytes(4000))   # encrypted -> not SQLite
    (root / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": True}))
    w = Walker()
    assert list(w.walk(classify(root))) == []
    assert "encrypted" in w.stats.errors[0]["error"]
