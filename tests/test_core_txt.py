"""Shape tests for the Rust core (dronedog_core) against ARCHITECTURE.md C1-C3.

Uses real DJI TXT samples found under corpus/raw, corpus/_downloads and corpus/samples
(or $DRONEDOG_CORPUS). Tests that need samples skip cleanly when none are present.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
from pathlib import Path

import pytest

dronedog_core = pytest.importorskip("dronedog_core")

REPO = Path(__file__).resolve().parents[1]
CORPUS = Path(os.environ.get("DRONEDOG_CORPUS", REPO / "corpus"))

C2_KEYS = [
    "aircraft_model", "aircraft_name", "aircraft_sn", "fc_sn", "camera_sn", "rc_sn",
    "battery_sn", "app_platform", "app_version", "log_version", "start_time_utc",
    "duration_s", "total_distance_m", "max_height_m", "max_h_speed_ms", "max_v_speed_ms",
    "home_lat", "home_lon", "city", "area", "sub_street", "firmware_version",
    "record_line_count",
]
C3_COLUMNS = [
    "t_offset_ms", "t_utc", "lat", "lon", "alt_rel_m", "alt_msl_m", "vel_n", "vel_e",
    "vel_d", "speed_h_ms", "roll", "pitch", "yaw", "gimbal_pitch", "gimbal_roll",
    "gimbal_yaw", "rc_aileron", "rc_elevator", "rc_throttle", "rc_rudder",
    "rc_mode_switch", "flight_mode", "gps_sats", "gps_health", "battery_pct", "battery_v",
    "battery_cells_v", "battery_temp_c", "home_lat", "home_lon", "distance_m", "height_m",
    "motor_on", "is_flying", "vps_height_m", "distance_app_m", "rc_signal_up_pct",
    "rc_signal_down_pct",
]
RESULT_KEYS = {
    "adapter", "adapter_version", "details", "frames", "events", "records_total",
    "records_decoded", "records_failed", "warnings", "encrypted_unreadable",
}
DETECT_KEYS = {"kind", "subtype", "version", "encrypted", "confidence", "reasons"}
SEVERITIES = {"info", "warn", "critical"}


def _txt_samples() -> list[Path]:
    seen: dict[str, Path] = {}
    for sub in ("raw", "_downloads", "samples"):
        root = CORPUS / sub
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() != ".txt":
                continue
            if not p.name.lower().startswith("djiflightrecord"):
                try:
                    if dronedog_core.detect(str(p))["kind"] != "dji_txt":
                        continue
                except OSError:
                    continue
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            seen.setdefault(h, p)
    return [seen[h] for h in sorted(seen)]


SAMPLES = _txt_samples()
needs_samples = pytest.mark.skipif(not SAMPLES, reason=f"no real DJI TXT samples under {CORPUS}")


def test_module_surface():
    v = dronedog_core.version()
    assert "dronedog_core" in v and "dji-log-parser 0.5.7" in v
    assert list(dronedog_core.C3_COLUMNS) == C3_COLUMNS
    assert list(dronedog_core.C2_KEYS) == C2_KEYS
    assert issubclass(dronedog_core.ParseError, ValueError)


def test_detect_garbage_is_unknown(tmp_path):
    rnd = random.Random(1234)
    for i, n in enumerate([0, 1, 11, 12, 99, 100, 4096, 20000]):
        p = tmp_path / f"blob{i}.bin"
        p.write_bytes(bytes(rnd.getrandbits(8) for _ in range(n)))
        d = dronedog_core.detect(str(p))
        assert set(d) == DETECT_KEYS
        assert d["kind"] == "unknown", d
        assert d["reasons"], "reasons must explain the decision"


def test_errors_are_python_exceptions(tmp_path):
    with pytest.raises(FileNotFoundError):
        dronedog_core.parse_txt(str(tmp_path / "missing.txt"))
    with pytest.raises(FileNotFoundError):
        dronedog_core.detect(str(tmp_path / "missing.txt"))
    junk = tmp_path / "junk.txt"
    junk.write_bytes(b"\x00" * 50)
    with pytest.raises(dronedog_core.ParseError):
        dronedog_core.parse_txt(str(junk))
    bad = tmp_path / "trunc.txt"
    bad.write_bytes((10_000).to_bytes(8, "little") + b"\x90\x01\x0b" + b"\x00" * 200)
    with pytest.raises(dronedog_core.ParseError):
        dronedog_core.parse_txt(str(bad))


def test_parse_dat_stub(tmp_path):
    p = tmp_path / "FLY001.DAT"
    p.write_bytes(b"\x00" * 512)
    r = dronedog_core.parse_dat(str(p))
    assert set(r) == RESULT_KEYS
    assert r["warnings"][0] == "DAT decoding not yet implemented"
    assert r["encrypted_unreadable"] is False
    assert all(len(v) == 0 for v in r["frames"].values())


def _check_result(r: dict, path: Path):
    assert set(r) == RESULT_KEYS
    assert r["adapter"] == "txt_djilog"
    assert "dji-log-parser" in r["adapter_version"]
    for k in C2_KEYS:
        assert k in r["details"], f"{path.name}: details missing {k}"
    frames = r["frames"]
    assert list(frames) == C3_COLUMNS
    lengths = {k: len(v) for k, v in frames.items()}
    assert len(set(lengths.values())) == 1, f"{path.name}: unequal columns {lengths}"
    for la, lo in zip(frames["lat"], frames["lon"]):
        assert (la is None) == (lo is None)
        if la is not None:
            assert not (la == 0 and lo == 0), "lat/lon must never be (0,0)"
            assert -90 <= la <= 90 and -180 <= lo <= 180
    for la, lo in zip(frames["home_lat"], frames["home_lon"]):
        if la is not None:
            assert not (la == 0 and lo == 0)
    for col in ("rc_aileron", "rc_elevator", "rc_throttle", "rc_rudder"):
        assert all(x is None or -1.0 <= x <= 1.0 for x in frames[col])
    for col in ("lat", "lon", "alt_rel_m", "vel_n", "speed_h_ms", "battery_v"):
        assert all(x is None or math.isfinite(x) for x in frames[col])
    assert all(x is None or isinstance(x, list) for x in frames["battery_cells_v"])
    for e in r["events"]:
        assert set(e) == {"t_offset_ms", "kind", "severity", "message", "raw"}
        assert isinstance(e["t_offset_ms"], int)
        assert e["severity"] in SEVERITIES
    assert r["records_decoded"] + r["records_failed"] <= r["records_total"]


@needs_samples
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_real_txt_shape(path: Path):
    d = dronedog_core.detect(str(path))
    assert d["kind"] == "dji_txt" and d["subtype"] == f"txt_v{d['version']}"
    r = dronedog_core.parse_txt(str(path))
    _check_result(r, path)
    if d["version"] >= 13:
        assert d["encrypted"] is True
        assert r["encrypted_unreadable"] is True
        assert all(len(v) == 0 for v in r["frames"].values())
        assert any("encrypted" in w for w in r["warnings"])
        assert r["details"]["log_version"] == d["version"]
    else:
        assert r["encrypted_unreadable"] is False
        assert len(r["frames"]["lat"]) > 0
        assert any(x is not None for x in r["frames"]["lat"])


@needs_samples
def test_parse_never_mutates_input():
    p = SAMPLES[0]
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    dronedog_core.detect(str(p))
    dronedog_core.parse_txt(str(p))
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


@needs_samples
def test_performance_largest_sample():
    import time

    plain = [p for p in SAMPLES if dronedog_core.detect(str(p))["version"] < 13]
    if not plain:
        pytest.skip("no decodable (v<13) samples")
    # Longest clean flight (most frames, <5% failed records).
    best = None
    for p in plain:
        r = dronedog_core.parse_txt(str(p))
        n = len(r["frames"]["t_offset_ms"])
        if r["records_failed"] * 20 <= r["records_total"] and (best is None or n > best[1]):
            best = (p, n)
    big, _ = best
    t = time.perf_counter()
    r = dronedog_core.parse_txt(str(big))
    dt = time.perf_counter() - t
    n = len(r["frames"]["t_offset_ms"])
    # ~10 frames/s: scale to a 30-minute (18 000-frame) flight; must stay under 1 s.
    assert dt * max(1.0, 18_000 / max(n, 1)) < 1.0, f"{big.name}: {n} frames in {dt:.3f}s"


def test_directory_is_rejected(tmp_path):
    with pytest.raises(IsADirectoryError):
        dronedog_core.detect(str(tmp_path))
    with pytest.raises(IsADirectoryError):
        dronedog_core.parse_txt(str(tmp_path))
