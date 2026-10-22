"""Tests for dronedog.analysis.explain using real DJI log lines from the corpus."""

import pytest

from dronedog.analysis.explain import CATEGORIES, RULES, explain, explain_many

# (raw line, kind, expected rule, expected title substring, category, importance, expected values subset)
CASES = [
    ("23663 [L-FLYMODE]SWITCH2HOVER!", "fly_log_text", "flymode.hover",
     "Switched to hover (position hold)", "flight", "normal", {}),
    ("[L-FLYMODE]GPS2ATTI!Init atti dynscale scale 0.000000", None, "flymode.gps2atti",
     "GPS mode to ATTI", "flight", "high", {}),
    ("20 [L-HOME]Update. lati:46.5214217 longti: 6.5673021 alti:355.46", None, "home.update",
     "Home point updated", "home_point", "high", {"lat": 46.5214217, "lon": 6.5673021, "alt": 355.46}),
    ("[L-HOME]First. lati:46.5214286 longti: 6.5673037 alti:355.61", None, "home.first",
     "Home point first recorded", "home_point", "high", {"lat": 46.5214286, "lon": 6.5673037}),
    ("[L-HOME]Update RTK. lati:46.5213889 longti: 6.5672863 alti:465.70 alti_F:2", None, "home.update_rtk",
     "RTK", "home_point", "high", {"lat": 46.5213889, "alt": 465.70}),
    ("[L-HOME][InitHP-2]: lati:40.0825682 longti:-88.2006590 alti:192.51(0) srcTp:2 setTp:2", None, "home.init_hp",
     "Home point initialised", "home_point", "high", {"lon": -88.200659}),
    ("41.2907681 -72.9621732 -23.95 First Home Point", "fly_log_text", "home.legacy_first",
     "Home point first recorded", "home_point", "high", {"lat": 41.2907681, "lon": -72.9621732}),
    ("[L-HOME]target go-home height(20) is <= 500m and will be stored", None, "home.rth_height_stored",
     "Return-to-home altitude set to 20 m", "configuration", "normal", {"height": 20}),
    ("19095 [L-TAKEOFF]alti: 63.761677 tors: -44.675697", None, "takeoff.alt",
     "Take-off recorded", "flight", "high", {"alt": 63.761677, "yaw": -44.675697}),
    ("[L-FMU/MOTOR]Start. reason:ACT.Takeoff", None, "motor.start",
     "Motors started (automatic take-off command)", "motors", "high", {"reason": "ACT.Takeoff"}),
    ("[L-FMU/MOTOR] Stop. reason:landing.RC_Thr", None, "motor.stop",
     "Motors stopped", "motors", "high", {}),
    ("[L-FMU/MOTOR]Total start times: 1, time: 178.94", None, "motor.total",
     "Motor run #1", "motors", "normal", {"count": 1, "seconds": 178.94}),
    ("[L-FMU/FSM]state changed. cur: in air", None, "fsm.state",
     "Aircraft airborne", "flight", "high", {}),
    ("[L-FMU/FSM]state changed. cur: motor off", None, "fsm.state",
     "Motors off", "motors", "high", {}),
    ("[L-RC]rc_connect:0=>1", None, "rc.connect",
     "Remote-controller link connected", "radio_link", "high", {}),
    ("[L-RC]rc_resume @ [124130 ms]", None, "rc.resume",
     "resumed", "radio_link", "high", {"ms": 124130}),
    ("[L-RC]Emergency break. rc vib!", None, "rc.emergency_brake",
     "Emergency brake", "flight", "high", {}),
    ("[L-RC]rc cmd:STD_GO_HOME", None, "rc.cmd",
     "return-to-home", "radio_link", "high", {"cmd": "STD_GO_HOME"}),
    ("[L-BATTERY]old:SAFE_FLY=>>>>new:GOHOME_CNTDOWN", None, "battery.state",
     "SAFE_FLY -> GOHOME_CNTDOWN", "battery", "high", {"from": "SAFE_FLY", "to": "GOHOME_CNTDOWN"}),
    ("[L-BATTERY]cntdown->10", None, "battery.countdown",
     "countdown: 10", "battery", "high", {"count": 10}),
    ("[L-BATTERY]Serial number   :718", None, "battery.serial",
     "Battery serial number: 718", "battery", "normal", {"serial": "718"}),
    ("[L-BATTERY]bat time 1618827888 (2021 4 19 10 24 48)", None, "battery.time",
     "2021-04-19 10:24:48 UTC", "battery", "normal", {"utc": "2021-04-19 10:24:48"}),
    ("[L-BATTERY]Design cap      :5870", None, "battery.design_cap",
     "5870 mAh", "battery", "normal", {"capacity_mah": 5870}),
    ("[L-FMU/VERSION]Mc  ID  :0UYKFBE00200NV", None, "version.fc_serial",
     "0UYKFBE00200NV", "system", "normal", {"serial": "0UYKFBE00200NV"}),
    ("[L-ACTIVATION]idx:0 id:2 get info,sta:1,date:2020-3-5,time:20-54-10, proId:0UYKFBE0020", None, "activation.info",
     "Activation record: 2020-3-5", "activation", "normal", {"date": "2020-3-5", "product_id": "0UYKFBE0020"}),
    ("[L-FDI][IMU]0 cali ver ok 4 4 9 9", None, "fdi.imu_ok",
     "IMU 0 check passed", "imu", "low", {}),
    ("[L-FDI]NS(0) GPS(1): fault on , stuck", None, "fdi.ns_fault",
     "Navigation fault raised: GPS", "gps", "high", {"src": "GPS"}),
    ("[L-FDI][FDI CTRL] event: impact turn on", None, "fdi.ctrl_event",
     "Fault detector enabled: impact", "safety", "low", {}),
    ("[L-FMU/LED]action changed. batt warning!:battery_status_data.capacity_level1_status(1)", None, "led.action",
     "LED: low-battery warning", "battery", "high", {}),
    ("[L-FMU/LED]action changed. forearm LED outer ctrl off:Ctrl forearm LED off by cam(2)", None, "led.action",
     "front arm lights", "system", "low", {}),
    ("[L-GPS]<GPS INFO>GPS ecdsa_verify OK", None, "gps.signature_ok",
     "GPS module signature verified", "gps", "low", {}),
    ("{gps_sign}INFO:GPS ecdsa_verify OK", None, "gps.signature_ok",
     "GPS module signature verified", "gps", "low", {}),
    ("[L-COMPASS][read_mag_cali_pt]lat:0.81195, lon:0.11462", None, "compass.cali_location",
     "compass-calibration location", "compass", "high", {"lat_rad": 0.81195}),
    ("[L-MIS][WP_UPLOAD]0: Llh(0.81195163,0.11461895,23.30) D:0.21", None, "mis.wp_upload",
     "Waypoint 0 uploaded", "navigation", "normal", {"alt": 23.3}),
    ("[L-CAMERA]cam success(9 36 30 777015902)(6.567274,46.521394,467.845540) (0.009494,0.01", None,
     "camera.shot_position", "Photo taken at 46.521394, 6.567274", "camera_gimbal", "normal",
     {"lat": 46.521394, "lon": 6.567274}),
    ("[L-GEO][record_flysafe_info]drone relation OUT_NFZ => NFZ_UNKNOWN", None, "geo.relation",
     "No-fly-zone relation", "safety", "normal", {"from": "OUT_NFZ"}),
    ("[L-CFG]get rc lost act. 2", None, "cfg.rc_lost_action",
     "return-to-home", "safety", "normal", {"act": 2}),
    ("[L-N_MIS]req abort all for exit landing for ground not smooth", None, "nmis.abort_all",
     "Landing aborted", "safety", "high", {}),
    ("[L-N_MIS]GoHome Check Failed: motor off!", None, "nmis.gohome_failed",
     "Return-to-home request rejected", "flight", "high", {}),
    ("{root}INFO:reset by power.", "fly_log_text", "boot.reset_cause",
     "power-on", "system", "normal", {"cause": "power"}),
    ("0 eeprom load 12  448   52   56", "fly_log_text", "storage.eeprom_load",
     "EEPROM", "storage", "low", {"block": 12}),
    ("[L-RC][FATAL]rc raw data not in range?", None, "rc.raw_out_of_range",
     "out of range", "radio_link", "normal", {}),
    ("Op.module\tL-GPS", "module_name", "module.listed",
     "Log module listed: [L-GPS]", "system", "low", {"module": "L-GPS"}),
    # app-log (TXT) derived events
    ("Aircraft airborne", "takeoff", "txt.takeoff", "Take-off", "flight", "high", {}),
    ("Aircraft on ground", "landing", "txt.landing", "Landing", "flight", "high", {}),
    ("Flight mode changed from GPSAtti to GoHome", "rth", "txt.mode_change",
     "P-GPS -> Return-to-home", "flight", "high", {"to": "GoHome"}),
    ("Flight controller action: OutOfControlGoHome", "signal_loss_rth", "txt.fc_action",
     "Signal-lost return-to-home", "radio_link", "high", {"action": "OutOfControlGoHome"}),
    ("Battery voltage warning level 1 (charge 30%)", "low_battery", "txt.low_battery",
     "Low-battery warning level 1 (30% charge)", "battery", "high", {"level": 1, "pct": 30}),
    ("OSD flag set: not_enough_force", "aircraft_fault", "txt.osd_flag",
     "Not enough thrust", "motors", "high", {"flag": "not_enough_force"}),
    ("Strong wind. Fly with caution", "app_warning", "txt.app_warning",
     "App warning: Strong wind", "safety", "normal", {}),
]


@pytest.mark.parametrize("line,kind,rule,title,category,importance,values", CASES)
def test_real_lines(line, kind, rule, title, category, importance, values):
    x = explain(line, kind)
    assert x is not None
    assert x["rule"] == rule
    assert title in x["title"]
    assert len(x["title"]) <= 60
    assert x["category"] == category
    assert x["importance"] == importance
    assert x["detail"] and "{" not in x["detail"].replace("{gps_sign}", "").replace("{root}", "")
    for k, v in values.items():
        got = x["values"].get(k)
        if isinstance(v, float):
            assert got == pytest.approx(v, abs=1e-6), k
        else:
            assert got == v, k


def test_param_dump_simple():
    x = explain("[     5.000000] => g_config.fdi.gps_max_horizontal_pos_over_count_0", "sys_cfg")
    assert x["category"] == "configuration"
    assert x["values"]["value"] == 5
    assert "Configuration parameter g_config.fdi.gps_max_horizontal_pos_over_count_0 = 5" in x["detail"]
    assert "fault-detection" in x["detail"].lower()


def test_param_dump_full_known_and_changed():
    x = explain("[           20][           30][           20][          500][0x3d] => "
                "go_home_height,g_config.go_home.fixed_go_home_altitude", "sys_cfg")
    assert x["title"] == "Return-to-home altitude = 20 m"
    assert x["values"]["default"] == 30 and x["values"]["max"] == 500
    assert "differs from the default" in x["detail"]


def test_param_dump_unknown_is_low():
    x = explain("[            0] => fix_send_index[ 0]_0", "sys_cfg")
    assert x["rule"] == "cfg.param"
    assert x["importance"] == "low"
    assert x["title"].startswith("Configuration parameter fix_send_index")


def test_param_enum_decoded():
    x = explain("[            2][            2][            0][            2][0x3d] => "
                "rc_lost_action,g_config.fail_safe.protect_action", "sys_cfg")
    assert x["values"]["meaning"] == "return-to-home"


def test_unknown_line_with_tag_falls_back():
    x = explain("1234 [L-GPS]some brand new message 42")
    assert x["rule"] == "fallback:tag:L-GPS"
    assert x["title"] == "GPS module message"
    assert x["category"] == "gps"
    assert "no specific interpretation" in x["detail"]


def test_unknown_tag_still_explained():
    x = explain("[L-WHATEVER]xyz")
    assert x["rule"].startswith("fallback:tag:")


def test_unknown_kind_fallback_and_none():
    assert explain("completely opaque text", "fly_log_text")["rule"] == "fallback:kind:fly_log_text"
    assert explain("completely opaque text") is None
    assert explain("") is None
    assert explain("   ") is None
    assert explain(None) is None  # type: ignore[arg-type]


def test_explain_many_copies_and_never_raises():
    evs = [{"t_offset_ms": 1, "kind": "fly_log_text", "severity": "info",
            "message": "23663 [L-FLYMODE]SWITCH2HOVER!", "raw": "x"},
           {"t_offset_ms": 2, "kind": None, "message": None, "raw": None},
           {"kind": 5, "message": 12345}]
    out = explain_many(evs)
    assert len(out) == 3
    assert "explain" not in evs[0]
    assert out[0]["explain"]["rule"] == "flymode.hover"
    assert out[0]["message"] == evs[0]["message"]
    assert out[1]["explain"] is None
    assert explain_many([]) == []


def test_every_rule_example_matches_its_rule():
    for r in RULES:
        x = explain(r.example, (r.kinds or (None,))[0])
        assert x is not None and x["rule"] == r.id, r.id
        assert x["category"] in CATEGORIES
        assert len(x["title"]) <= 60
