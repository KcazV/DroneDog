"""C5 case store: layout, schema, evidence intake (hash-on-copy, verify, read-only,
reference-in-place), dedupe, bulk flight storage."""
from __future__ import annotations

import hashlib
import json
import os
import time

import pytest

from dronedog.case import Case, EvidenceError
from dronedog.case import hashing
from dronedog.case.store import FRAME_COLUMN_NAMES, SCHEMA_VERSION
from dronedog.ingest.sources import classify
from test_ingest_fatbuilder import FakeCore, build_sd_image, fake_core, fake_dat  # noqa: F401


@pytest.fixture
def case(tmp_path):
    c = Case.create(tmp_path / "case", name="Test case", examiner="Examiner A", agency="FHPD",
                    case_number="2026-0001")
    yield c
    c.close()


def test_layout_and_schema(case):
    r = case.root
    for d in ("evidence", "extracted", "exports"):
        assert (r / d).is_dir()
    meta = json.loads((r / "case.json").read_text())
    for k in ("id", "name", "examiner", "agency", "case_number", "created_utc", "tool", "tool_version"):
        assert k in meta
    assert meta["tool"] == "DroneDog"
    assert case.store.current_version() == SCHEMA_VERSION
    assert case.store.one("PRAGMA journal_mode")["journal_mode"] == "wal"
    tables = {t["name"] for t in case.store.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"evidence", "artifacts", "artifact_sources", "flights", "frames", "events", "links",
            "schema_version", "evidence_files", "frame_extra", "custody_head"} <= tables
    cols = {c["name"] for c in case.store.query("PRAGMA table_info(frames)")}
    assert set(FRAME_COLUMN_NAMES) <= cols
    # re-open is idempotent
    c2 = Case.open(r)
    assert c2.store.current_version() == SCHEMA_VERSION
    c2.close()


def test_create_refuses_existing(case):
    with pytest.raises(Exception):
        Case.create(case.root, name="x", examiner="y")


def test_hash_on_copy_and_readonly(case, tmp_path):
    data = os.urandom(3 * 1024 * 1024 + 17)
    f = tmp_path / "FLY010.DAT"
    f.write_bytes(data)
    ev = case.add_evidence(classify(f))
    assert ev["storage"] == "copied"
    assert ev["sha256"] == hashlib.sha256(data).hexdigest()
    assert ev["sha1"] == hashlib.sha1(data).hexdigest()
    assert ev["md5"] == hashlib.md5(data).hexdigest()
    copy = case.root / ev["stored_path"]
    assert copy.read_bytes() == data
    assert hashing.is_readonly(copy)
    with pytest.raises(PermissionError):
        open(copy, "ab")
    entries = case.custody.entries()
    add = [e for e in entries if e["action"] == "evidence.add"][-1]
    assert add["inputs"][0]["sha256"] == ev["sha256"] and add["params"]["storage"] == "copied"
    checks = [e["params"]["check"] for e in entries if e["action"] == "evidence.verify"]
    assert "verify-after-copy" in checks
    # same bytes again -> duplicate, not a second copy
    f2 = tmp_path / "other_name.DAT"
    f2.write_bytes(data)
    ev2 = case.add_evidence(classify(f2))
    assert ev2["duplicate_of"] == ev["id"]
    assert case.store.one("SELECT COUNT(*) n FROM evidence")["n"] == 1


def test_verify_after_copy_mismatch_refuses(case, tmp_path, monkeypatch):
    f = tmp_path / "x.DAT"
    f.write_bytes(b"abc" * 1000)
    real = hashing.hash_files

    def corrupt(paths, algos=hashing.DEFAULT_ALGOS, progress=None, cancel=None):
        whole, per = real(paths, algos, progress, cancel)
        per[0].digests["sha256"] = "0" * 64
        return whole, per
    monkeypatch.setattr(hashing, "hash_files", corrupt)
    with pytest.raises(EvidenceError, match="verify-after-copy FAILED"):
        case.add_evidence(classify(f))
    assert case.store.one("SELECT COUNT(*) n FROM evidence")["n"] == 0
    assert not any(case.evidence_dir.iterdir())
    last = [e for e in case.custody.entries() if e["action"] == "evidence.add"][-1]
    assert last["params"]["refused"] is True
    assert case.verify_custody()["ok"]


def test_image_referenced_in_place_split_segments(case, tmp_path):
    img, _ = build_sd_image()
    half = len(img) // 2
    (tmp_path / "sd.001").write_bytes(img[:half])
    (tmp_path / "sd.002").write_bytes(img[half:])
    src = classify(tmp_path / "sd.001")
    assert src.kind == "image" and src.subkind == "split_raw" and len(src.files) == 2
    ev = case.add_evidence(src)
    assert ev["storage"] == "referenced" and ev["hash_scope"] == "segments-concat"
    assert ev["sha256"] == hashlib.sha256(img).hexdigest()       # == media hash
    segs = case.store.query("SELECT * FROM evidence_files WHERE evidence_id=? ORDER BY seq", (ev["id"],))
    assert [s["sha256"] for s in segs] == [hashlib.sha256(img[:half]).hexdigest(),
                                           hashlib.sha256(img[half:]).hexdigest()]
    assert not any(case.evidence_dir.iterdir())                  # nothing duplicated
    assert not hashing.is_readonly(tmp_path / "sd.001")          # source untouched


def test_imager_sidecar_match(case, tmp_path):
    img, _ = build_sd_image()
    p = tmp_path / "card.001"
    p.write_bytes(img)
    (tmp_path / "card.md5").write_text(f"MD5 checksum: {hashlib.md5(img).hexdigest()}\n")
    case.add_evidence(classify(p))
    v = [e["params"] for e in case.custody.entries() if e["action"] == "evidence.verify"
         and e["params"].get("check") == "imager-sidecar"]
    assert v and v[0]["result"] == "match" and "md5" in v[0]["matched"]


def test_copy_option_for_image(case, tmp_path):
    img, _ = build_sd_image()
    p = tmp_path / "card.dd"
    p.write_bytes(img)
    ev = case.add_evidence(classify(p), copy=True)
    assert ev["storage"] == "copied"
    assert (case.root / ev["stored_path"]).read_bytes() == img


def test_streaming_hash_speed(tmp_path):
    p = tmp_path / "big.bin"
    block = os.urandom(8 * 1024 * 1024)
    with open(p, "wb") as fh:
        for _ in range(32):        # 256 MiB
            fh.write(block)
    t = time.perf_counter()
    r = hashing.hash_file(p, hashing.IMAGE_ALGOS)
    dt = time.perf_counter() - t
    h = hashlib.sha256()
    for _ in range(32):
        h.update(block)
    assert r.sha256 == h.hexdigest() and r.size == 256 * 1024 * 1024
    print(f"\nhash sha256+sha1+md5: {r.size / 1e6 / dt:,.0f} MB/s")
    t = time.perf_counter()
    with open(p, "rb") as src:
        r2 = hashing.copy_with_hash(src, tmp_path / "copy.bin")
    dt = time.perf_counter() - t
    assert r2.sha256 == r.sha256
    print(f"copy+hash sha256+sha1: {r2.size / 1e6 / dt:,.0f} MB/s")


def test_store_flight_bulk(case, tmp_path):
    f = tmp_path / "FLY011.DAT"
    f.write_bytes(fake_dat(11))
    ev = case.add_evidence(classify(f))
    from dronedog.ingest.walker import Walker
    cand = next(Walker().walk(classify(f), case.evidence_read_paths(ev)))
    aid = case.extract(ev, cand)["artifact_id"]
    core = FakeCore(rows=150_000)
    res = core.parse_dat(str(case.artifact_path(case.store.one("SELECT * FROM artifacts WHERE id=?", (aid,)))))
    t = time.perf_counter()
    out = case.store_flight(aid, res, "aircraft-dat", "d" * 64)
    dt = time.perf_counter() - t
    print(f"\nstore_flight 150k frames: {dt:.2f}s")
    assert out["frame_count"] == 150_000 and out["extra_columns"] == ["x_vendor_field"]
    fl = case.store.one("SELECT * FROM flights WHERE id=?", (out["flight_id"],))
    assert fl["frame_count"] == 150_000 and json.loads(fl["bbox_json"])["min_lat"] > 38
    row = case.store.one("SELECT * FROM frames WHERE flight_id=? AND idx=5", (out["flight_id"],))
    assert json.loads(row["battery_cells_v"]) == [3.9, 3.91, 3.9, 3.92] and row["motor_on"] == 1
    assert row["lat"] == pytest.approx(38.00005)
    assert case.store.one("SELECT COUNT(*) n FROM frames")["n"] == 150_000
    # re-parse replaces, never duplicates
    out2 = case.store_flight(aid, res, "aircraft-dat", "d" * 64)
    assert out2["replaced"] == [out["flight_id"]]
    assert case.store.one("SELECT COUNT(*) n FROM frames")["n"] == 150_000
