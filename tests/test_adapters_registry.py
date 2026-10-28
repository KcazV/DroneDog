"""C4 adapters + registry; core fallback; pending-without-core then `case run`."""
from __future__ import annotations

import sys

from pathlib import Path

import pytest

from dronedog import core_bridge
from dronedog.adapters import (AdapterUnavailable, DatCoreAdapter, ParserAdapter, Registry, TxtDjiLogAdapter,
                               flight_source, registry)
from dronedog.case import Case
from dronedog.pipeline import process_pending, run_import
from test_ingest_fatbuilder import FakeCore, fake_core, fake_dat, fake_txt  # noqa: F401


class _A:
    def __init__(self, name, score):
        self.name, self.score = name, score

    def accepts(self, d):
        return self.score

    def parse(self, path, ctx):
        return {}


def test_registry_pick_highest_first_on_tie():
    r = Registry()
    a, b, c = _A("a", 0.5), _A("b", 0.8), _A("c", 0.8)
    for x in (a, b, c):
        r.register(x)
    assert r.pick({})[0] is b
    assert isinstance(a, ParserAdapter)
    with pytest.raises(ValueError):
        r.register(_A("a", 1))
    r2 = Registry()
    r2.register(_A("z", 0.0))
    assert r2.pick({}) == (None, 0.0)


def test_builtin_adapters():
    names = [a.name for a in registry.adapters]
    assert names[:2] == ["txt_djilog", "dat_core"]
    assert registry.pick({"kind": "dji_txt"})[0].name == "txt_djilog"
    assert registry.pick({"kind": "dji_dat"})[0].name == "dat_core"
    assert registry.pick({"kind": "unknown"})[0] is None


def test_adapters_call_core(tmp_path, fake_core):
    p = tmp_path / "FLY001.DAT"
    p.write_bytes(fake_dat(1))
    r = DatCoreAdapter().parse(p, None)
    assert r["adapter"] == "dat_v3" and len(r["frames"]["t_offset_ms"]) == 50
    t = tmp_path / "DJIFlightRecord_x.txt"
    t.write_bytes(fake_txt(1))

    class Ctx:
        keychains_json = None
    assert TxtDjiLogAdapter().parse(t, Ctx())["adapter"] == "txt_djilog"
    assert ("parse_txt", str(t)) in fake_core.calls


def test_flight_source():
    assert flight_source("txt_djilog", {"kind": "dji_txt"}, []) == "app-txt"
    assert flight_source("dat_core", {"kind": "dji_dat"}, [{"inner_path": "p1/MISC/LOG/FLY1.DAT"}]) == "aircraft-dat"
    assert flight_source("dat_core", {"kind": "dji_dat", "subtype": "dat_controller"}, []) == "controller-dat"
    mc = [{"inner_path": "UAVLogical-DJIRCRM331/CFID_DECODED_FLIGHTLOGS/MCDatFlightRecords/"
                         "2026-07-10_18-19-28_FLY001.DAT"}]
    assert flight_source("dat_ref_py", {"kind": "dji_dat", "subtype": "dat_v3"}, mc) == "controller-dat"
    assert flight_source("dat_ref_py", {"kind": "dji_dat", "subtype": "dat_v3"}, [],
                         {"_source_hint": "controller-dat"}) == "controller-dat"


def test_no_core_fallback_then_case_run(tmp_path, monkeypatch):
    # Hide the pure-Python DAT decoder too, so nothing can parse without the core.
    monkeypatch.setitem(sys.modules, "dronedog.adapters.dat_ref_impl", None)
    core_bridge.set_core(None)
    try:
        assert core_bridge.detector_component()[0] == "detect.fallback"
        with pytest.raises(AdapterUnavailable):
            DatCoreAdapter().parse(Path("x"), None)
        root = tmp_path / "logs" / "FlightRecord"
        root.mkdir(parents=True)
        (root / "FLY001.DAT").write_bytes(fake_dat(1))
        (root / "DJIFlightRecord_2017-01-01_[00-00-00].txt").write_bytes(fake_txt(1))
        case = Case.create(tmp_path / "case", name="n", examiner="e")
        s = run_import(case, [tmp_path / "logs"])
        assert s["pending_no_core"] == 2 and s["parsed"] == 0
        rows = case.store.query("SELECT status, detected_kind, error, detection_json FROM artifacts")
        assert all(r["status"] == "pending" and "dronedog_core" in r["error"] for r in rows)
        assert all('"detector": "detect.fallback"' in r["detection_json"] for r in rows)
    finally:
        core_bridge.reset_core()
    # core becomes available -> case run re-detects with the core and parses
    fake = FakeCore()
    core_bridge.set_core(fake)
    try:
        s2 = process_pending(case)
        assert s2["parsed"] == 2 and s2["detected"] == 2
        assert {r["status"] for r in case.store.query("SELECT status FROM artifacts")} == {"parsed"}
        det = [e for e in case.custody.entries() if e["action"] == "artifact.detect"]
        assert det[-1]["component"] == "detect.core" and "fake-0.0.1" in det[-1]["tool_version"]
        assert case.verify_custody()["ok"]
    finally:
        core_bridge.reset_core()
        case.close()


def test_core_detect_exception_falls_back(tmp_path):
    class Broken(FakeCore):
        def detect(self, path):
            raise RuntimeError("bad")
    core_bridge.set_core(Broken())
    try:
        p = tmp_path / "FLY001.DAT"
        p.write_bytes(fake_dat(1))
        d = core_bridge.detect(p)
        assert d["kind"] == "dji_dat" and any("raised RuntimeError" in r for r in d["reasons"])
    finally:
        core_bridge.reset_core()


def test_dat_ref_py_preferred_when_impl_importable(tmp_path, monkeypatch, fake_core):
    import sys
    import types
    from dronedog.adapters import dat_ref_py
    assert registry.pick({"kind": "dji_dat", "subtype": "dat_v3"})[0].name in ("dat_core", "dat_ref_py")
    mod = types.ModuleType("dronedog.adapters.dat_ref_impl")
    mod.parse_dat = lambda p: {"adapter": "dat_ref_py", "adapter_version": "t", "frames": {"t_offset_ms": [0, 1]}}
    monkeypatch.setitem(sys.modules, "dronedog.adapters.dat_ref_impl", mod)
    a, s = registry.pick({"kind": "dji_dat", "subtype": "dat_v3"})
    assert a.name == "dat_ref_py" and s == 0.95
    assert registry.pick({"kind": "dji_dat", "subtype": "dat_encrypted"})[0].name == "dat_core"
    p = tmp_path / "FLY1.DAT"
    p.write_bytes(fake_dat(1))
    r = a.parse(p, None)
    assert r["adapter"] == "dat_ref_py" and r["frames"]["t_offset_ms"] == [0, 1] and r["events"] == []
    assert dat_ref_py._impl() is mod
