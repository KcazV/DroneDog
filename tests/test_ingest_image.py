"""Raw disk images through pytsk3: MBR volume system, whole-image FS fallback, split raw,
deleted FAT entries (flagged, inode, offsets, overwrite check), zip inside an image."""
from __future__ import annotations

import json

import pytest

from dronedog.case import Case
from dronedog.ingest import Walker, classify
from dronedog.pipeline import ImportJob, run_import
from test_ingest_fatbuilder import build_sd_image, fake_core, fake_dat, fake_txt  # noqa: F401


def _walk(path):
    w = Walker()
    out = {}
    for c in w.walk(classify(path)):
        with c.opener() as fh:
            c.data = fh.read()
        out[c.inner_path] = c
    return out, w


@pytest.mark.parametrize("mbr", [True, False])
def test_fat_image_walk_with_deleted(tmp_path, mbr):
    img, info = build_sd_image(with_mbr=mbr)
    p = tmp_path / "sd.dd"
    p.write_bytes(img)
    got, w = _walk(p)
    pre = "p1" if mbr else "fs"
    expect = {f"{pre}/MISC/LOG/FLY001.DAT", f"{pre}/MISC/LOG/_LY002.DAT", f"{pre}/MISC/LOG/_LY003.DAT",
              f"{pre}/MISC/LOG/FLY004.DAT", f"{pre}/FLIGHTS.ZIP!/inner/FLY009.DAT", f"{pre}/SNIFFED.BIN"}
    assert set(got) == expect
    part_off = 2048 * 512 if mbr else 0
    live = got[f"{pre}/MISC/LOG/FLY001.DAT"]
    assert not live.deleted and live.overwrite_status == "allocated"
    assert live.partition_index == (1 if mbr else 0) and live.partition_offset == part_off
    assert live.fs_type == "fat16" and live.meta_addr
    fb = info["builder"]
    assert live.offset == part_off + fb.cluster_offset(info["f1"].cluster)
    assert img[live.offset:live.offset + 64] == fake_dat(1, 7000)[:64]
    assert live.data == fake_dat(1, 7000)

    d2 = got[f"{pre}/MISC/LOG/_LY002.DAT"]
    assert d2.deleted and d2.overwrite_status == "none_detected"
    assert d2.data == fake_dat(2, 5000)                       # fully recovered
    assert "deleted-name:?LY*.DAT" in d2.reasons
    assert "contiguous clusters" in d2.extra["recovery_note"]

    d3 = got[f"{pre}/MISC/LOG/_LY003.DAT"]
    assert d3.deleted and d3.overwrite_status == "full"       # clusters reused by FLY004
    assert d3.extra["clusters_reallocated"] == d3.extra["clusters_checked"] > 0

    z = got[f"{pre}/FLIGHTS.ZIP!/inner/FLY009.DAT"]
    assert z.container == "zip" and z.extra["outer"]["offset"] > part_off
    assert z.partition_index == (1 if mbr else 0) and z.data == fake_dat(9, 4000)
    assert got[f"{pre}/SNIFFED.BIN"].reasons == ["sniff:dji_txt/txt_v8"]
    assert w.stats.deleted_candidates == 2
    parts = [c for c in w.stats.containers if c["type"] == "partition"]
    assert parts[0]["filesystem"] == "fat16" and parts[0]["offset"] == part_off


def test_split_raw_image_pipeline(tmp_path, fake_core):
    img, _ = build_sd_image()
    third = len(img) // 3
    segs = [img[:third], img[third:2 * third], img[2 * third:]]
    for i, s in enumerate(segs, 1):
        (tmp_path / f"card.{i:03d}").write_bytes(s)
    case = Case.create(tmp_path / "case", name="img", examiner="e")
    events = []
    s = run_import(case, [tmp_path / "card.001"], events.append)
    assert s["state"] == "done", s
    ev = case.store.one("SELECT * FROM evidence")
    assert ev["kind"] == "image" and ev["subkind"] == "split_raw" and ev["storage"] == "referenced"
    srcs = case.store.query("SELECT * FROM artifact_sources ORDER BY inner_path")
    assert len(srcs) == 6
    dele = [r for r in srcs if r["deleted"]]
    assert {r["overwrite_status"] for r in dele} == {"none_detected", "full"}
    assert all(r["meta_addr"] for r in dele) and all(r["offset"] for r in dele)
    st = {r["name"]: r["status"] for r in case.store.query("SELECT name, status FROM artifacts")}
    assert st["FLY001.DAT"] == "parsed" and st["_LY002.DAT"] == "parsed" and st["SNIFFED.BIN"] == "parsed"
    stages = {e["stage"] for e in events}
    assert {"ingest", "extract", "detect", "parse", "done"} <= stages
    walk = [e for e in case.custody.entries() if e["component"] == "ingest.walk"][0]
    assert walk["params"]["deleted_candidates"] == 2 and "pytsk3/tsk" in walk["params"]["libraries"]
    ex = [e for e in case.custody.entries() if e["action"] == "artifact.extract" and
          e["params"].get("provenance", {}).get("deleted")]
    assert ex and ex[0]["params"]["provenance"]["meta_addr"]
    assert case.verify_custody()["ok"]
    case.close()


def test_import_job_thread_and_cancel(tmp_path, fake_core):
    img, _ = build_sd_image()
    (tmp_path / "a.dd").write_bytes(img)
    case = Case.create(tmp_path / "case", name="job", examiner="e")
    job = ImportJob(case, [tmp_path / "a.dd"])
    job.start()
    assert job.wait(60)
    st = job.status()
    assert st["state"] == "done" and st["summary"]["parsed"] >= 4
    # cancelled before start of work: state cancelled, custody still valid
    (tmp_path / "b.dd").write_bytes(img[:-512] + b"\x01" * 512)
    job2 = ImportJob(case, [tmp_path / "b.dd"])
    job2.cancel()
    job2.start()
    job2.wait(60)
    assert job2.status()["state"] == "cancelled"
    assert case.verify_custody()["ok"]
    case.close()


def test_no_filesystem_image_reports_error(tmp_path):
    p = tmp_path / "junk.dd"
    p.write_bytes(b"\x00" * (2 * 1024 * 1024))
    w = Walker()
    assert list(w.walk(classify(p))) == []
    assert w.stats.errors and "no filesystem" in w.stats.errors[0]["error"]
