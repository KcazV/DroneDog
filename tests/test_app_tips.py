"""DJI GO 4 phone-side flight-tips logs: detector, adapter, pipeline + correlation."""
from __future__ import annotations

import json

from dronedog.adapters import registry
from dronedog.adapters.app_tips_json import AppTipsJsonAdapter, parse_tips, sniff
from dronedog.case import Case
from dronedog.pipeline import run_import
from test_ingest_fatbuilder import fake_core, fake_txt, make_zip  # noqa: F401

SN = "FAKE123"
TIPS = (b'[["2017-06-01 06:00:00","Tips","Taking Off"]],'
        b'[["2017-06-01 06:00:02","","Home Point Recorded. RTH Altitude:  30m."]],'
        b'[["2017-06-01 06:01:30","Cannot enter TapFly Mode.","Max Flight Distance Reached."]],'
        b'[["2017-06-01 06:02:10","Warning","Low Battery. Returning home."],'
        b'["2017-06-01 06:03:00","Tips","Landing"]]')
NAME = "2017-06-01 06_00_00-%s.dat" % SN


def _write(tmp_path, data=TIPS, name=NAME):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_sniff_accepts_tips_and_cites_filename(tmp_path):
    p = _write(tmp_path)
    d = sniff(p, p.read_bytes()[:512])
    assert d["kind"] == "dji_app_tips" and d["subtype"] == "go4_tips_json"
    assert d["confidence"] >= 0.9
    assert any("comma-separated JSON arrays" in r for r in d["reasons"])
    assert any("filename matches" in r and SN in r for r in d["reasons"])
    # a single well-formed JSON array also works, at lower confidence without the name
    q = _write(tmp_path, b"[" + TIPS + b"]", "tips.json")
    assert sniff(q, q.read_bytes()[:512])["confidence"] < d["confidence"]


def test_sniff_rejects_lookalikes(tmp_path):
    for data in (b'[["2017-06-01 06:00:00","Tips"]]',          # entry too short
                 b'[[["2017-06-01","Tips","x"]]]',               # bad timestamp
                 b'[1, 2, 3]', b'{"a": 1}', b"BUILD junk", b"\x00" * 64,
                 b'[["2017-06-01 06:00:00","Tips","x"]],[[ broken'):
        p = _write(tmp_path, data, "x.dat")
        assert sniff(p, data[:512]) is None, data


def test_parse_events_times_and_provenance(tmp_path):
    r = parse_tips(_write(tmp_path))
    d, ev = r["details"], r["events"]
    assert r["adapter"] == "app_tips_json" and r["frames"] == {} and not r["encrypted_unreadable"]
    assert [e["t_offset_ms"] for e in ev] == [0, 2000, 90000, 130000, 180000]
    assert [e["kind"] for e in ev] == ["Tips", "tip", "Cannot enter TapFly Mode.", "Warning", "Tips"]
    assert [e["severity"] for e in ev] == ["info", "info", "warn", "warn", "info"]
    assert json.loads(ev[3]["raw"])[2] == "Low Battery. Returning home."
    assert d["aircraft_sn"] == SN and d["x"]["sn_basis"] == "filename"
    assert d["start_time_utc"] is None and d["start_time_local"] == "2017-06-01T06:00:00"
    assert d["x"]["timezone"] == "unknown (device local)" and d["duration_s"] == 180.0
    assert any("no UTC offset" in w for w in r["warnings"])


def test_registry_routes_tips_to_adapter():
    adapter, score = registry.pick({"kind": "dji_app_tips", "subtype": "go4_tips_json"})
    assert isinstance(adapter, AppTipsJsonAdapter) and score > 0


def test_pipeline_detects_parses_and_links_to_txt(tmp_path, fake_core):
    # iOS-style app sandbox paths; the fake core reports the TXT as starting 12:00:00Z
    z = tmp_path / "phone.zip"
    z.write_bytes(make_zip({
        "AppDomain-com.dji.go/Documents/FlightLogs/" + NAME: TIPS,
        "AppDomain-com.dji.go/Documents/FlightRecords/DJIFlightRecord_2017-06-01_[06-00-00].txt":
            fake_txt(1)}))
    case = Case.create(tmp_path / "case", name="t", examiner="e")
    s = run_import(case, [str(z)])
    assert s["state"] == "done", s
    arts = {a["name"]: a for a in case.store.query("SELECT * FROM artifacts")}
    tips_art = next(a for n, a in arts.items() if n.endswith(NAME))
    assert tips_art["detected_kind"] == "dji_app_tips" and tips_art["status"] == "parsed"
    assert '"detector": "detect.python.app_tips"' in tips_art["detection_json"]
    fl = {f["artifact_id"]: f for f in case.store.query("SELECT * FROM flights")}
    tf = fl[tips_art["id"]]
    assert tf["source"] == "app-txt" and tf["frame_count"] == 0
    # back-filled from the TXT: local 06:00:00 + (12:00:00Z - 06:00:00) = 12:00:00Z
    assert tf["start_utc"] == "2017-06-01T12:00:00.000Z"
    d = json.loads(tf["details_json"])
    assert d["x"]["utc_offset_s"] == -6 * 3600 and "R1 match" in d["x"]["utc_offset_basis"]
    links = case.store.query("SELECT * FROM links WHERE kind='app-tips'")
    assert len(links) == 1
    reasons = json.loads(links[0]["reasons_json"])
    assert reasons[0].startswith("R1: tips filename local time 2017-06-01 06:00:00 equals")
    assert any("device UTC offset -06:00" in r for r in reasons)
    assert any("serial matches (FAKE123" in r for r in reasons)
    evs = case.store.query("SELECT * FROM events WHERE flight_id=?", (tf["id"],))
    assert len(evs) == 5 and {e["severity"] for e in evs} == {"info", "warn"}
