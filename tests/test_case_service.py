"""dronedog.service: the functions the UI bridge forwards to (C7)."""
from __future__ import annotations

import json
import time

from dronedog.case import hashing
from dronedog.service import Service
from test_ingest_fatbuilder import build_sd_image, fake_core, fake_dat, fake_txt, make_zip  # noqa: F401


def _wait(svc, jid, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        st = svc.job_status(jid)
        if st["state"] not in ("queued", "running"):
            return st
        time.sleep(0.05)
    raise TimeoutError


def test_service_end_to_end(tmp_path, fake_core):
    svc = Service()
    info = svc.case_new({"name": "Demo", "case_number": "26-0001", "examiner": "Det. A", "agency": "FHPD",
                         "location": str(tmp_path)})
    assert info["open"] and info["case"]["tool"] == "DroneDog" and (tmp_path / "26-0001" / "case.json").exists()
    img, _ = build_sd_image()
    (tmp_path / "sd.dd").write_bytes(img)
    (tmp_path / "phone.zip").write_bytes(make_zip({"FlightRecord/DJIFlightRecord_2017-06-01_[12-00-00].txt":
                                                   fake_txt(1)}))
    events = []
    jid = svc.import_paths([str(tmp_path / "sd.dd"), str(tmp_path / "phone.zip")], events.append)["job_id"]
    st = _wait(svc, jid)
    assert st["state"] == "done", st
    assert st["counts"]["flights"] >= 5 and st["counts"]["deleted"] == 2
    assert {e["ui_stage"] for e in events} >= {"hashing", "extracting", "detecting", "parsing", "done"}
    assert len(st["result"]["flight_ids"]) == st["counts"]["flights"]

    ev = svc.list_evidence()
    assert [e["kind"] for e in ev] == ["image", "zip"] and ev[0]["verified"] is True
    arts = svc.list_artifacts()
    deleted = [a for a in arts if a["deleted"]]
    assert len(deleted) == 2 and all(a["sources"][0]["meta_addr"] for a in deleted)
    fl = svc.list_flights()
    assert fl and all(f["frame_count"] == 50 for f in fl) and fl[0]["bbox"]
    g = svc.get_flight(fl[0]["id"], max_points=20)
    assert g["decimation"]["total"] == 50 and g["decimation"]["returned"] <= 20
    assert len(g["frames"]["lat"]) == g["decimation"]["returned"] and g["events"]
    full = svc.get_flight(fl[0]["id"], max_points=0)
    assert len(full["frames"]["t_offset_ms"]) == 50 and full["frames"]["battery_cells_v"][1] == [3.9, 3.91, 3.9, 3.92]
    gj = svc.get_track_geojson([f["id"] for f in fl])
    lines = [x for x in gj["features"] if x["geometry"]["type"] == "LineString"]
    assert len(lines) == len(fl) and len(lines[0]["geometry"]["coordinates"]) == 49
    out = svc.export_report([f["id"] for f in fl], ["json", "geojson", "kml", "pdf"])
    fmts = {f["format"] for f in out["files"]}
    assert fmts == {"json", "geojson", "kml", "html"}
    for f in out["files"]:
        assert hashing.is_readonly(f["path"])
    b = json.load(open([f["path"] for f in out["files"] if f["format"] == "json"][0], encoding="utf-8"))
    assert b["case"]["name"] == "Demo" and len(b["flights"]) == len(fl)
    assert "<LineString>" in open([f["path"] for f in out["files"] if f["format"] == "kml"][0]).read()
    assert svc.verify_custody()["ok"]
    assert svc.list_custody()[-1]["action"] == "report.export"
    root = info["root"]
    svc.case_open(root)
    assert svc.case_info()["counts"]["flights"] == len(fl)
