"""Ingest: loose files, folders (Android logical dumps), zips, nested zips, DJI Assistant
bundles, dedupe with multiple provenance rows, folder-splitting of dropped device folders."""
from __future__ import annotations

import json

import pytest

from dronedog.case import Case
from dronedog.ingest import Walker, classify, expand_inputs
from dronedog.pipeline import ImportOptions, run_import
from test_ingest_fatbuilder import (assistant_bundle, build_sd_image, fake_core, fake_dat,  # noqa: F401
                                    fake_txt, make_zip)


def _walk(path):
    """Candidates are only readable while the walker is positioned on them (temp spools,
    open zip handles), so read the bytes during iteration."""
    w = Walker()
    out = {}
    for src in expand_inputs([path]):
        for c in w.walk(src):
            with c.opener() as fh:
                c.data = fh.read()
            out[c.inner_path] = c
    return out, w


def test_zip_candidates_and_media_skipped(tmp_path):
    z = tmp_path / "flight_logs.zip"
    z.write_bytes(make_zip({
        "FlightRecord/DJIFlightRecord_2017-06-01_[12-00-00].txt": fake_txt(1),
        "MISC/LOG/FLY001.DAT": fake_dat(1),
        "random/renamed_log.bin": fake_dat(2),          # only the sniffer finds this
        "DCIM/DJI_0001.JPG": b"\xff\xd8" + fake_dat(3),   # media: never sniffed
        "notes.txt": b"hello",
    }))
    got, w = _walk(z)
    assert set(got) == {"FlightRecord/DJIFlightRecord_2017-06-01_[12-00-00].txt", "MISC/LOG/FLY001.DAT",
                        "random/renamed_log.bin"}
    assert got["random/renamed_log.bin"].reasons == ["sniff:dji_dat/dat_v3"]
    assert "name:DJIFlightRecord_*.txt" in got["FlightRecord/DJIFlightRecord_2017-06-01_[12-00-00].txt"].reasons
    assert got["MISC/LOG/FLY001.DAT"].container == "zip"
    assert w.stats.files_seen == 5


def test_nested_zip(tmp_path):
    inner2 = make_zip({"deep/FLY003.DAT": fake_dat(3)})
    inner = make_zip({"FLY002.DAT": fake_dat(2), "more.zip": inner2})
    z = tmp_path / "outer.zip"
    z.write_bytes(make_zip({"exports/inner.zip": inner, "FLY001.DAT": fake_dat(1)}))
    got, _ = _walk(z)
    assert set(got) == {"FLY001.DAT", "exports/inner.zip!/FLY002.DAT",
                        "exports/inner.zip!/more.zip!/deep/FLY003.DAT"}
    c = got["exports/inner.zip!/more.zip!/deep/FLY003.DAT"]
    assert c.data == fake_dat(3)
    assert c.extra["outer"]["container_path"] == "exports/inner.zip!/more.zip"


def test_android_logical_folder_paths(tmp_path):
    root = tmp_path / "android_dump"
    files = {
        "Android/data/dji.go.v5/files/FlightRecord/DJIFlightRecord_2021-01-01_[10-00-00].txt": fake_txt(1),
        "DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/17-06-01-12-00-00_FLY005.DAT": fake_dat(5),
        "DJI/dji.go.v4/FlightRecord/Cache/abc.cache": b"x" * 200,      # app-dir rule picks it up
        "DJI/dji.pilot/FlightRecord/DJIFlightRecord_2020-02-02_[09-09-09].txt": fake_txt(2),
        "Android/data/com.other.app/files/log.txt": b"plain text log",
        "DCIM/Camera/IMG_0001.jpg": b"\xff\xd8" * 100,
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    got, _ = _walk(root)
    assert "Android/data/com.other.app/files/log.txt" not in got
    assert "DCIM/Camera/IMG_0001.jpg" not in got
    v5 = got["Android/data/dji.go.v5/files/FlightRecord/DJIFlightRecord_2021-01-01_[10-00-00].txt"]
    assert v5.app_hint == "dji.go.v5" and v5.container == "folder"
    mc = got["DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/17-06-01-12-00-00_FLY005.DAT"]
    assert mc.app_hint == "dji.go.v4" and "name:*_FLY*.DAT" in mc.reasons
    assert got["DJI/dji.pilot/FlightRecord/DJIFlightRecord_2020-02-02_[09-09-09].txt"].app_hint == "dji.pilot"
    assert "app:DJI app log directory" in got["DJI/dji.go.v4/FlightRecord/Cache/abc.cache"].reasons


def test_assistant_export_bundle(tmp_path):
    p = tmp_path / "DJI_ASSISTANT_EXPORT_FILE_2018-06-01.DAT"
    p.write_bytes(assistant_bundle([("FLY061.DAT", fake_dat(61)), ("FLY062.DAT", fake_dat(62)),
                                    ("cfg.ini", b"a=1\n")]))
    got, w = _walk(p)
    names = sorted(got)
    assert names == ["DJI_ASSISTANT_EXPORT_FILE_2018-06-01.DAT!/FLY061.DAT",
                     "DJI_ASSISTANT_EXPORT_FILE_2018-06-01.DAT!/FLY062.DAT"]
    assert got[names[0]].container == "assistant"
    assert got[names[1]].data == fake_dat(62)


def test_dedupe_keeps_every_provenance(tmp_path, fake_core):
    same = fake_dat(7)
    root = tmp_path / "dump"
    (root / "a/MISC/LOG").mkdir(parents=True)
    (root / "b/FlightRecord").mkdir(parents=True)
    (root / "a/MISC/LOG/FLY007.DAT").write_bytes(same)
    (root / "b/FlightRecord/FLY007.DAT").write_bytes(same)
    (root / "logs.zip").write_bytes(make_zip({"x/FLY007.DAT": same}))
    case = Case.create(tmp_path / "case", name="d", examiner="e")
    s = run_import(case, [root])
    assert s["state"] == "done", s
    assert case.store.one("SELECT COUNT(*) n FROM artifacts")["n"] == 1
    srcs = case.store.query("SELECT * FROM artifact_sources ORDER BY inner_path")
    assert [r["inner_path"] for r in srcs] == ["a/MISC/LOG/FLY007.DAT", "b/FlightRecord/FLY007.DAT", "x/FLY007.DAT"]
    assert len({r["evidence_id"] for r in srcs}) == 2   # folder evidence + the zip split out as its own evidence
    assert len(list(case.extracted_dir.glob("*_FLY007.DAT"))) == 1
    assert case.store.one("SELECT status FROM artifacts")["status"] == "parsed"
    ev = case.store.query("SELECT kind, storage, hash_scope FROM evidence ORDER BY id")
    assert ev == [{"kind": "zip", "storage": "copied", "hash_scope": "file"},
                  {"kind": "folder", "storage": "referenced", "hash_scope": "candidate-manifest"}]
    assert case.verify_custody()["ok"]
    case.close()


def test_dropped_device_folder_splits_images_and_zips(tmp_path):
    dev = tmp_path / "DF999"
    (dev / "SDCard_internal").mkdir(parents=True)
    (dev / "mobile").mkdir()
    img, _ = build_sd_image()
    (dev / "SDCard_internal" / "DF999_internal.001").write_bytes(img[: len(img) // 2])
    (dev / "SDCard_internal" / "DF999_internal.002").write_bytes(img[len(img) // 2:])
    (dev / "SDCard_internal" / "DF999_internal.md5").write_text("x")
    (dev / "mobile" / "flight.zip").write_bytes(make_zip({"FLY001.DAT": fake_dat(1)}))
    (dev / "README.txt").write_text("readme")
    srcs = expand_inputs([dev])
    kinds = sorted((s.kind, s.subkind or "", s.path.name) for s in srcs)
    assert kinds == [("folder", "", "DF999"), ("image", "split_raw", "DF999_internal.001"),
                     ("zip", "", "flight.zip")]
    img_src = [s for s in srcs if s.kind == "image"][0]
    assert len(img_src.files) == 2


def test_one_bad_artifact_does_not_stop_batch(tmp_path, fake_core):
    root = tmp_path / "logs"
    (root / "FlightRecord").mkdir(parents=True)
    (root / "FlightRecord/FLY001.DAT").write_bytes(fake_dat(1))
    (root / "FlightRecord/FLY002.DAT").write_bytes(fake_dat(2)[:300] + b"BOOM" + fake_dat(2)[304:])
    (root / "FlightRecord/DJIFlightRecord_2017-01-01_[00-00-00].txt").write_bytes(fake_txt(3))
    (root / "FlightRecord/FLY003.DAT").write_bytes(b"LOGH" + bytes(2000))
    case = Case.create(tmp_path / "case", name="d", examiner="e")
    s = run_import(case, [root])
    st = {r["name"]: (r["status"], r["error"]) for r in case.store.query("SELECT name, status, error FROM artifacts")}
    assert st["FLY001.DAT"][0] == "parsed"
    assert st["FLY002.DAT"][0] == "failed" and "corrupt" in st["FLY002.DAT"][1]
    assert st["DJIFlightRecord_2017-01-01_[00-00-00].txt"][0] == "parsed"
    assert st["FLY003.DAT"][0] == "encrypted"
    assert s["parsed"] == 2 and s["parse_failed"] == 1 and s["encrypted"] == 1
    fl = case.store.query("SELECT f.source, f.frame_count, a.name FROM flights f JOIN artifacts a ON a.id=f.artifact_id")
    srcs = {r["name"]: r["source"] for r in fl}
    assert srcs["DJIFlightRecord_2017-01-01_[00-00-00].txt"] == "app-txt" and srcs["FLY001.DAT"] == "aircraft-dat"
    enc = [r for r in fl if r["name"] == "FLY003.DAT"][0]
    assert enc["frame_count"] == 0
    parse_entries = [e for e in case.custody.entries() if e["action"] == "artifact.parse"]
    assert any("error" in e["params"] for e in parse_entries)
    ok = [e for e in parse_entries if "error" not in e["params"]]
    assert all(len(e["outputs"][0]["sha256"]) == 64 for e in ok)
    case.close()


def test_explicit_loose_file_always_selected(tmp_path):
    p = tmp_path / "weird_name.log"
    p.write_bytes(b"not recognised")
    got, _ = _walk(p)
    assert list(got) == ["weird_name.log"] and got["weird_name.log"].reasons == ["explicit input file"]


def test_classify(tmp_path):
    (tmp_path / "a.E01").write_bytes(b"EVF\x09\x0d\x0a\xff\x00" + bytes(100))
    (tmp_path / "a.E02").write_bytes(b"EVF")
    s = classify(tmp_path / "a.E01")
    assert s.kind == "image" and s.subkind == "e01" and [f.name for f in s.files] == ["a.E01", "a.E02"]
    z = tmp_path / "x.zip"
    z.write_bytes(make_zip({"a": b"b"}))
    assert classify(z).kind == "zip"
    assert classify(tmp_path).kind == "folder"


def _tar(files: dict, gz: bool = False) -> bytes:
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz" if gz else "w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mtime = 1628771000
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_android_quick_image_tars_inside_zip(tmp_path):
    """Magnet-style Android quick image: a zip holding sdcard.tar.gz and adb-data.tar."""
    sd = _tar({"sdcard/DJI/dji.go.v4/FlightRecord/DJIFlightRecord_2021-08-12_[12-24-18].txt": fake_txt(1),
               "sdcard/DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/21-08-12-12-23-25_FLY087.DAT": fake_dat(7),
               "sdcard/DCIM/Camera/IMG_0001.jpg": b"\xff\xd8" * 300,
               "./sdcard/.hidden/notes.txt": b"x"}, gz=True)
    adb = _tar({"shared/0/DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/21-08-17-04-50-34_FLY088.DAT": fake_dat(8),
                "shared/0/Download/export.zip": make_zip({"FLY009.DAT": fake_dat(9)})})
    z = tmp_path / "samsung Quick Image.zip"
    z.write_bytes(make_zip({"sdcard.tar.gz": sd, "adb-data.tar": adb, "Live Data/package.txt": b"pkgs"}))
    got, w = _walk(z)
    txt = "sdcard.tar.gz!/sdcard/DJI/dji.go.v4/FlightRecord/DJIFlightRecord_2021-08-12_[12-24-18].txt"
    assert set(got) == {
        txt,
        "sdcard.tar.gz!/sdcard/DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/21-08-12-12-23-25_FLY087.DAT",
        "adb-data.tar!/shared/0/DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/21-08-17-04-50-34_FLY088.DAT",
        "adb-data.tar!/shared/0/Download/export.zip!/FLY009.DAT",
    }
    c = got[txt]
    assert c.data == fake_txt(1) and c.container == "tar" and c.app_hint == "dji.go.v4"
    assert c.extra["outer"]["container_path"] == "sdcard.tar.gz"
    assert c.times["mtime_utc"].startswith("2021-08-12")
    assert got["adb-data.tar!/shared/0/Download/export.zip!/FLY009.DAT"].data == fake_dat(9)
    tars = [x for x in w.stats.containers if x["type"] == "tar"]
    assert [(x["path"], x["members"]) for x in tars] == [("sdcard.tar.gz", 4), ("adb-data.tar", 2)]


def test_truncated_tar_keeps_what_was_read(tmp_path):
    data = _tar({"DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/a_FLY001.DAT": fake_dat(1),
                 "filler.bin": b"\0" * 20000,
                 "DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/b_FLY002.DAT": fake_dat(2)})
    p = tmp_path / "adb-data.tar"
    p.write_bytes(data[:len(data) // 2])          # cut inside filler.bin
    got, w = _walk(p)
    assert set(got) == {"adb-data.tar!/DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/a_FLY001.DAT"}
    tar = next(x for x in w.stats.containers if x["type"] == "tar")
    assert "stopped" in tar
    assert any("tar read stopped" in e["error"] for e in w.stats.errors)


def test_truncated_tar_member_is_not_extracted_short(tmp_path):
    data = _tar({"DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/a_FLY001.DAT": fake_dat(1) * 50})
    p = tmp_path / "cut.tar"
    p.write_bytes(data[:2048])                    # header + part of the member only
    w = Walker()
    for src in expand_inputs([p]):
        for c in w.walk(src):
            with pytest.raises(Exception, match="truncated|unexpected end"):
                with c.opener() as fh:
                    fh.read()


def _fclog(logs: list[tuple[int, int, int]], blobs: dict, size: int, data_start: int = 0x1000) -> bytes:
    """Build a fc_log.log ring: logs = [(number, start, end)] in write order; blobs = {offset: bytes}."""
    import struct
    buf = bytearray(b"\xaa" * size)
    hdr = struct.pack("<II", logs[-1][2], len(logs)) + b"\xff" * 24
    ents = b""
    for num, start, end in logs:
        length = end - start if end >= start else (size - start) + (end - data_start)
        ents += struct.pack("<IIII", num, start, end, length) + b"LGY " + struct.pack("<9I", 11, 6, 2020, 14, 25, 26, 0, 6, 4590) + b"\xff" * 8
    buf[:len(hdr) + len(ents)] = hdr + ents
    for off, b in blobs.items():
        buf[off:off + len(b)] = b
    return bytes(buf)


def test_fc_log_ring_split(tmp_path):
    """MISC/LOG/flylog/fc_log.log: logs split by the file's index, the wrapped log rejoined,
    a log whose start was overwritten by later logs reported, not extracted."""
    size, ds = 0x20000, 0x1000
    a, b = fake_dat(1), fake_dat(2)
    wrap = fake_dat(3)
    c = fake_dat(4)
    s_a = ds                                   # log 10: overwritten later by the wrap of log 12
    s_b = 0x8000                               # log 11
    s_w = size - 0x100                         # log 12 wraps: 0x100 bytes at the end, rest from ds
    e_w = ds + len(wrap) - 0x100
    s_c = e_w                                  # log 13 right after the wrap
    data = _fclog([(10, s_a, s_a + 0x3000), (11, s_b, s_b + len(b)), (12, s_w, e_w), (13, s_c, s_c + len(c))],
                  {s_a: a, s_b: b, s_w: wrap[:0x100], ds: wrap[0x100:], s_c: c}, size, ds)
    p = tmp_path / "MISC" / "LOG" / "flylog"
    p.mkdir(parents=True)
    (p / "fc_log.log").write_bytes(data)
    got, w = _walk(tmp_path / "MISC")
    base = "LOG/flylog/fc_log.log!/"
    assert set(got) == {base + "fc_log_011.DAT", base + "fc_log_012.DAT", base + "fc_log_013.DAT"}
    assert got[base + "fc_log_011.DAT"].data == b
    assert got[base + "fc_log_012.DAT"].data == wrap
    assert got[base + "fc_log_013.DAT"].data == c
    seg = got[base + "fc_log_012.DAT"].extra["fc_log_segment"]
    assert seg["wrapped"] and seg["pieces"] == [[s_w, size], [ds, e_w]] and seg["log_number"] == 12
    assert any("log 10: start overwritten" in e["error"] for e in w.stats.errors)
    info = next(x for x in w.stats.containers if x["type"] == "fc_log")
    assert info["logs_in_index"] == 4 and info["extracted"] == 3 and info["data_start"] == ds


def test_truncated_tar_cutting_a_nested_zip(tmp_path):
    data = _tar({"DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/a_FLY001.DAT": fake_dat(1),
                 "Download/big.zip": make_zip({"FLY002.DAT": fake_dat(2), "pad.bin": bytes(range(256)) * 20 + __import__("os").urandom(60000)})})
    import io
    import tarfile
    off = next(m.offset_data for m in tarfile.open(fileobj=io.BytesIO(data)) if m.name.endswith("big.zip"))
    p = tmp_path / "adb-data.tar"
    p.write_bytes(data[:off + 30000])             # cut inside big.zip, past the tar record holding its header
    got, w = _walk(p)
    assert set(got) == {"adb-data.tar!/DJI/dji.go.v4/FlightRecord/MCDatFlightRecords/a_FLY001.DAT"}
    assert any("big.zip" in e["where"] and "unreadable" in e["error"] for e in w.stats.errors)


def test_skip_evidence_hashing_is_recorded(tmp_path, fake_core):
    z = tmp_path / "logs.zip"
    z.write_bytes(make_zip({"MISC/LOG/FLY001.DAT": fake_dat(1)}))
    case = Case.create(tmp_path / "case", name="d", examiner="e")
    s = run_import(case, [z], options=ImportOptions(hash_evidence=False))
    assert s["state"] == "done", s
    ev = case.store.one("SELECT * FROM evidence")
    assert ev["storage"] == "referenced" and ev["hash_scope"] == "not-hashed"
    assert ev["sha256"] is None and ev["size"] == z.stat().st_size
    assert case.store.one("SELECT sha256 FROM artifacts")["sha256"]       # extracted logs still hashed
    add = [json.loads(l) for l in (case.root / "custody.log").read_text(encoding="utf-8").splitlines()
           if '"evidence.add"' in l]
    assert "skipped by examiner" in add[0]["params"]["hashing"]
    assert case.verify_custody()["ok"]
    case.close()
