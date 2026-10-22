"""UI bridge (C7) tests: mock backend end to end, real Api forwarding, contract surface."""
import inspect
import json
import sys
import time
import types

import pytest

from dronedog.ui.api import Api
from dronedog.ui.mock_api import MockApi
from dronedog.ui import mock_data as md

C7_METHODS = [
    "app_info", "recent_cases", "case_new", "case_open", "case_info", "pick_paths", "import_paths",
    "job_status", "list_evidence", "list_artifacts", "list_flights", "get_flight", "get_track_geojson",
    "basemaps", "tile", "import_basemap", "export_report", "verify_custody", "list_custody", "reveal_path",
]


def public_methods(obj):
    return {n for n, v in inspect.getmembers(obj, callable) if not n.startswith("_")}


def wait_job(api, job_id, timeout=30):
    t = time.time()
    while time.time() - t < timeout:
        j = api.job_status(job_id)
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def roundtrip(x):
    return json.loads(json.dumps(x, allow_nan=False))


@pytest.fixture
def mock(tmp_path):
    return MockApi(basemap_dir=tmp_path / "maps", speed=50)


def test_both_backends_expose_the_same_c7_surface(tmp_path):
    real = public_methods(Api(basemap_dir=tmp_path))
    fake = public_methods(MockApi(basemap_dir=tmp_path))
    for m in C7_METHODS:
        assert m in real, m
        assert m in fake, m
    assert real == fake


def test_mock_new_case_import_and_listings(mock):
    assert mock.case_info() == {"open": False}
    info = mock.case_new({"name": "Test", "examiner": "E1", "case_number": "42"})
    assert info["open"] and info["counts"]["flights"] == 0
    pushed = []
    mock._jobs.set_sink(pushed.append)
    job = wait_job(mock, mock.import_paths([r"C:\x\y.E01"])["job_id"])
    assert job["state"] == "done" and pushed and pushed[-1]["state"] == "done"
    assert [s["key"] for s in job["stages"]] == ["hashing", "extracting", "detecting", "parsing"]
    assert job["counts"]["flights"] == 5 and job["counts"]["deleted"] >= 2
    assert any(a["deleted"] for a in job["artifacts"])
    statuses = {a["status"] for a in mock.list_artifacts()}
    assert statuses == {"parsed", "encrypted", "unsupported", "failed"}
    flights = roundtrip(mock.list_flights())
    assert len(flights) == 5
    assert any(f["encrypted_unreadable"] for f in flights)
    assert len({f["aircraft_sn"] for f in flights}) == 4   # #1 and #2 are the same aircraft
    ev = mock.list_evidence()
    assert {e["kind"] for e in ev} >= {"folder", "zip", "image"}


def test_mock_get_flight_decimates_and_keeps_columns(mock):
    mock.case_open(md.CASE_ROOT)
    full = mock.get_flight(1, 0)
    assert full["decimation"]["method"] == "none" and full["decimation"]["total"] > 40000
    d = roundtrip(mock.get_flight(1, 3000))
    assert d["decimation"]["returned"] <= 3000
    fr = d["frames"]
    n = len(fr["t_offset_ms"])
    assert all(len(v) == n for v in fr.values())
    for col in ("lat", "lon", "alt_rel_m", "speed_h_ms", "battery_pct", "battery_v", "rc_throttle", "gps_sats", "yaw"):
        assert col in fr
    assert max(fr["alt_rel_m"]) == pytest.approx(max(full["frames"]["alt_rel_m"]))   # peaks survive
    assert d["events"] and {"t_offset_ms", "kind", "severity", "message"} <= set(d["events"][0])
    enc = mock.get_flight(4, 3000)
    assert enc["frames"] == {} and enc["details"]["aircraft_sn"]
    gj = mock.get_track_geojson([1, 3, 4])
    kinds = {(f["properties"]["flight_id"], f["geometry"]["type"]) for f in gj["features"]}
    assert (1, "LineString") in kinds and (4, "Point") in kinds and (4, "LineString") not in kinds


def test_mock_custody_chain_verifies_and_detects_tampering(mock):
    mock.case_open(md.CASE_ROOT)
    assert mock.verify_custody()["ok"]
    entries = mock.list_custody()
    entries[5]["params"]["size"] = 1
    rep = md.verify_entries(entries)
    assert not rep["ok"] and rep["first_bad_seq"] == 6


def test_mock_export_job_logs_custody(mock):
    mock.case_open(md.CASE_ROOT)
    n = len(mock.list_custody())
    job = wait_job(mock, mock.export_report([1, 2], ["pdf", "geojson"])["job_id"])   # pdf = alias of html
    assert job["state"] == "done"
    fmts = {f["format"] for f in job["result"]["files"]}
    assert {"html", "geojson"} <= fmts and "kml" not in fmts and "pdf" not in fmts
    log = mock.list_custody()
    assert len(log) == n + 1 and log[-1]["action"] == "report.export"
    assert mock.verify_custody()["ok"]


def test_mock_rejects_bad_input(mock):
    with pytest.raises(RuntimeError):
        mock.list_flights()
    with pytest.raises(ValueError):
        mock.case_new({"name": "", "examiner": "x"})
    with pytest.raises(FileNotFoundError):
        mock.case_open(r"Z:\nowhere")


# ---------------------------------------------------------------- real Api forwarding

class FakeService:
    def __init__(self):
        self.calls = []

    def case_info(self):
        return {"open": True, "case": {"name": "N", "case_number": "1"}, "root": "C:/c"}

    def list_flights(self):
        return [{"id": 1}]

    def get_flight(self, fid, max_points):
        return {"flight": {"id": fid}, "frames": {"t_offset_ms": list(range(100)), "alt_rel_m": [0.0] * 100},
                "events": []}

    def counts(self):
        return {"flights": 1}

    def list_artifacts(self):
        return [{"id": 1, "name": "FLY001.DAT", "status": "parsed"}]

    def import_paths(self, paths, progress_cb=None):
        def run():
            for stage, cur, tot in (("ingest", 1, 1), ("extract", 1, 2), ("detect", 2, 2), ("parse", 1, 1)):
                progress_cb({"job_id": "svc1", "stage": stage, "current": cur, "total": tot, "message": stage,
                             "ui_stage": {"ingest": "hashing", "extract": "extracting", "detect": "detecting",
                                          "parse": "parsing"}[stage], "progress": cur / tot})
            progress_cb({"job_id": "svc1", "stage": "done", "ui_stage": "done", "message": "done"})
        import threading
        threading.Thread(target=run, daemon=True).start()
        return {"job_id": "svc1"}

    def job_status(self, job_id):
        return {"job_id": job_id, "state": "done", "result": {"flight_ids": [1]}}

    def export_report(self, fids, fmts):
        return {"export_dir": "C:/c/exports/x", "files": []}


@pytest.fixture
def real_api(tmp_path, monkeypatch):
    svc = FakeService()
    mod = types.ModuleType("dronedog.service")
    mod.default_service = lambda: svc
    monkeypatch.setitem(sys.modules, "dronedog.service", mod)
    api = Api(basemap_dir=tmp_path / "maps")
    api._recent.path = tmp_path / "recent.json"
    return api


def test_real_api_forwards_and_runs_jobs(real_api):
    assert real_api.case_info()["open"] is True
    assert real_api.list_flights() == [{"id": 1}]
    g = real_api.get_flight(1, 10)
    assert g["decimation"]["returned"] <= 10
    j = wait_job(real_api, real_api.import_paths(["C:/e"])["job_id"])
    assert j["state"] == "done" and j["result"] == {"flight_ids": [1]}
    assert all(s["state"] == "done" for s in j["stages"]) and j["artifacts"]
    e = wait_job(real_api, real_api.export_report([1], ["json"])["job_id"])
    assert e["state"] == "done" and e["result"]["export_dir"].endswith("x")


def test_real_api_without_service_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "dronedog.service", None)   # import fails
    api = Api(basemap_dir=tmp_path)
    assert api.case_info() == {"open": False}
    with pytest.raises(Exception):
        api.list_flights()
