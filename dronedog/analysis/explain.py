"""Plain-English explanations for DJI flight-log event lines.

Forensic examiners see raw flight-controller text such as ``[L-FLYMODE]SWITCH2HOVER!`` or
``[L-HOME]Update. lati:46.52 longti:6.56 alti:355.46``. This module keeps the original text
untouched and attaches a cautious, plain-English explanation to it::

    >>> explain("23663 [L-FLYMODE]SWITCH2HOVER!")["title"]
    'Switched to hover (position hold)'

Design rules (see docs/EVENT_GLOSSARY.md, generated from the table below):

* The rule table was built from every event line in the DroneDog test corpus (DJI .DAT
  flight-controller logs and DJI app .txt logs), grouped by normalised pattern and ranked
  by frequency and forensic importance.
* Explanations say only what the line itself establishes. Where a token is undocumented,
  the explanation says so instead of guessing.
* Unknown lines with a recognised module tag get an honest tag-level explanation
  ("GPS module message"); ``None`` is returned only when nothing at all can be said.

Public API: :func:`explain`, :func:`explain_many`, :func:`glossary_markdown`.
Run ``python -m dronedog.analysis.explain > docs/EVENT_GLOSSARY.md`` to regenerate the
glossary.

Terminology references (used as reference only, no code copied): DatCon / CsvView
documentation, DJI flight-controller log conventions, dji-firmware-tools wiki, and the
enum names exposed by the dji-log-parser crate for app logs.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

__all__ = ["explain", "explain_many", "glossary_markdown", "CATEGORIES", "RULES"]

CATEGORIES = (
    "flight", "navigation", "home_point", "battery", "gps", "sensors", "compass", "imu",
    "radio_link", "motors", "camera_gimbal", "safety", "system", "configuration",
    "activation", "storage", "other",
)
IMPORTANCE = ("high", "normal", "low")
TITLE_MAX = 60

# ---------------------------------------------------------------------------
# Line normalisation
# ---------------------------------------------------------------------------
# DAT text records start with a flight-controller tick (decimal, or hex in V1 logs) and
# sometimes a source prefix such as "<FLY>", "<SD>", "d77<fly-ctrl>" or "{root}INFO:".
_TICK_DEC = re.compile(r"^\s*\d+\s+(?=\S)")
_TICK_HEX = re.compile(r"^\s*[0-9a-fA-F]{1,8}\s*(?=<)")
_SRC_PREFIX = re.compile(r"^<(?P<src>FLY|SD|fly-[\w-]+|raw-root|info-root|[a-z]+-[\w-]+)>\s*")
_TASK_PREFIX = re.compile(r"^\{(?P<task>[a-z_0-9]+)\}(?:INFO|WARN|ERROR|ERR|DEBUG|FATAL)?:\s*")
_TAG = re.compile(r"^\[(?P<tag>L-[A-Z0-9_/ -]+?)\]")


def _clean(message: str) -> Tuple[str, str, str]:
    """Return (tag, task, body) for a raw event message."""
    s = message.replace("\r", "").strip()
    s = _TICK_DEC.sub("", s, count=1)
    s = _TICK_HEX.sub("", s, count=1)
    m = _SRC_PREFIX.match(s)
    if m:
        s = s[m.end():]
    task = ""
    m = _TASK_PREFIX.match(s)
    if m:
        task = m.group("task")
        s = s[m.end():]
    tag = ""
    m = _TAG.match(s)
    if m:
        tag = m.group("tag")
        s = s[m.end():]
    return tag, task, s.strip()


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
# Captured groups whose names are listed here stay strings even when they look numeric
# (identifiers must not lose leading zeros or be reformatted).
_STRING_KEYS = {
    "serial", "barcode", "product_id", "version", "svn", "date", "time", "name", "board",
    "mode", "from", "to", "reason", "source", "desc", "state", "status", "cmd", "action",
    "flag", "cause", "module", "what", "who", "link", "dev", "code", "mask", "hash",
    "commit", "build", "when", "check", "det", "sensor", "region", "result", "type_name",
    "ctrl", "onoff", "src", "message", "raw_value", "default", "min", "max", "type_code",
    "id_text", "op", "ev", "why", "kind",
}


def _num(key: str, text: Optional[str]) -> Any:
    if text is None:
        return None
    t = text.strip()
    if key in _STRING_KEYS:
        return t
    if _INT.match(t):
        try:
            return int(t)
        except ValueError:
            return t
    if _FLOAT.match(t):
        try:
            v = float(t)
            return v if math.isfinite(v) else t
        except ValueError:
            return t
    return t


def _rad2deg(v: Any) -> Optional[float]:
    try:
        return round(float(v) * 180.0 / math.pi, 6)
    except (TypeError, ValueError):
        return None


def _iso_date8(s: str) -> str:
    s = str(s)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def _utc_from_epoch(v: Any) -> Optional[str]:
    try:
        return _dt.datetime.fromtimestamp(int(v), tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # noqa: D401
        return "?"


def _fmt(template: str, disp: Dict[str, Any]) -> str:
    try:
        return template.format_map(_SafeDict(disp))
    except (ValueError, IndexError, KeyError):
        return template


def _clip(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 3].rstrip() + "..."


def _humanize(name: str) -> str:
    s = re.sub(r"\[(\d+)\]", r" \1", name)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = s.replace("_", " ").replace(".", " ")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------
Post = Callable[[Dict[str, Any], Dict[str, Any]], Optional[Dict[str, Any]]]


@dataclass(frozen=True)
class Rule:
    id: str
    regex: "re.Pattern[str]"
    title: str
    detail: str
    category: str
    importance: str
    tags: Optional[Tuple[str, ...]]
    kinds: Optional[Tuple[str, ...]]
    post: Optional[Post]
    example: str


RULES: List[Rule] = []


def R(rule_id: str, pattern: str, title: str, detail: str, category: str, importance: str,
      example: str, tags: Optional[Iterable[str]] = None, kinds: Optional[Iterable[str]] = None,
      post: Optional[Post] = None, flags: int = 0) -> None:
    """Register a rule. ``tags`` lists allowed module tags ('' = untagged line)."""
    assert category in CATEGORIES, (rule_id, category)
    assert importance in IMPORTANCE, (rule_id, importance)
    RULES.append(Rule(rule_id, re.compile(pattern, flags), title, detail, category, importance,
                      tuple(tags) if tags is not None else None,
                      tuple(kinds) if kinds is not None else None, post, example))


def _t(*tags: str) -> Tuple[str, ...]:
    """Allowed tags: the given ones, untagged, and the debug wrappers."""
    return tuple(tags) + ("", "L-DBG1", "L-DBG2")


LAT = r"(?P<lat>-?\d+(?:\.\d+)?)"
LON = r"(?P<lon>-?\d+(?:\.\d+)?)"
ALT = r"(?P<alt>-?\d+(?:\.\d+)?)"
NUM = r"-?\d+(?:\.\d+)?"

# ---- reference tables ------------------------------------------------------
# Flight-mode names used by DJI app logs (dji-log-parser FlightMode); display names are the
# ones the DJI app shows.
FLIGHT_MODES: Dict[str, Tuple[str, str]] = {
    "Manual": ("Manual", "manual mode: no stabilisation assistance"),
    "Atti": ("ATTI", "attitude mode: the aircraft self-levels and holds height but does not hold position, so it drifts with the wind"),
    "AttiCourseLock": ("ATTI course lock", "attitude mode with course lock"),
    "AttiHover": ("ATTI hover", "attitude-mode hover (no position hold)"),
    "Hover": ("P-GPS hover", "hovering with satellite position hold"),
    "GPSBlake": ("P-GPS braking", "GPS mode while braking"),
    "GPSAtti": ("P-GPS", "normal GPS-assisted flight (positioning mode): the aircraft holds position when the sticks are released"),
    "GPSCourseLock": ("Course Lock", "GPS course-lock intelligent orientation mode"),
    "GPSHomeLock": ("Home Lock", "GPS home-lock intelligent orientation mode"),
    "GPSHotPoint": ("Point of Interest", "orbiting a point of interest (hotpoint)"),
    "AssistedTakeoff": ("Assisted take-off", "assisted take-off"),
    "AutoTakeoff": ("Auto take-off", "automatic take-off"),
    "AutoLanding": ("Auto landing", "automatic landing"),
    "AttiLanding": ("ATTI landing", "landing in attitude mode"),
    "GPSWaypoint": ("Waypoint mission", "flying a waypoint mission"),
    "GoHome": ("Return-to-home", "returning to the home point"),
    "ClickGo": ("Click-Go", "Click-Go"),
    "Joystick": ("Joystick", "joystick (virtual stick/SDK) control"),
    "GPSAttiWristband": ("Wristband", "wristband control"),
    "Cinematic": ("Cinematic", "cinematic mode"),
    "AttiLimited": ("ATTI limited", "limited attitude mode"),
    "Draw": ("Draw", "Draw intelligent flight mode"),
    "GPSFollowMe": ("Follow Me", "Follow Me intelligent flight mode"),
    "ActiveTrack": ("ActiveTrack", "ActiveTrack subject tracking"),
    "TapFly": ("TapFly", "TapFly"),
    "Pano": ("Panorama", "panorama capture"),
    "Farming": ("Farming", "agricultural spraying mode"),
    "FPV": ("FPV", "FPV mode"),
    "GPSSport": ("Sport", "Sport mode: higher speeds; obstacle sensing may be disabled on many models"),
    "GPSNovice": ("Beginner", "beginner (novice) mode"),
    "ConfirmLanding": ("Landing confirmation", "the aircraft is near the ground and waiting for the pilot to confirm landing (landing protection)"),
    "TerrainTracking": ("Terrain follow", "terrain following"),
    "NaviAdvGoHome": ("Advanced RTH", "advanced return-to-home"),
    "NaviAdvLanding": ("Advanced landing", "advanced landing"),
    "Tripod": ("Tripod", "Tripod (slow, precise) mode"),
    "TrackHeadlock": ("QuickShot/ActiveTrack", "ActiveTrack/QuickShot (track head-lock)"),
    "EngineStart": ("Motors starting", "the motors have just been started"),
    "GPSGentle": ("Gentle", "gentle GPS mode"),
}
_HIGH_MODES = {"GoHome", "AutoLanding", "AttiLanding", "ConfirmLanding", "AssistedTakeoff",
               "AutoTakeoff", "EngineStart", "Atti", "AttiHover", "AttiCourseLock", "AttiLimited",
               "Manual", "NaviAdvGoHome", "NaviAdvLanding"}

# Flight-controller actions (why the aircraft did something automatically), app logs.
FLIGHT_ACTIONS: Dict[str, Tuple[str, str, str]] = {
    "WarningPowerGoHome": ("Low-battery return-to-home", "Return-to-home started because the battery reached the low-battery warning level.", "battery"),
    "WarningPowerLanding": ("Low-battery landing", "Landing started because the battery reached the low-battery warning level.", "battery"),
    "SmartPowerGoHome": ("Smart low-battery return-to-home", "Return-to-home started because the flight controller calculated that the remaining battery was only just sufficient to return home (Smart RTH).", "battery"),
    "SmartPowerLanding": ("Smart low-battery landing", "Landing started because the flight controller calculated that the remaining battery was only sufficient to land.", "battery"),
    "LowVoltageLanding": ("Low-voltage landing", "Landing started because the battery voltage was low.", "battery"),
    "LowVoltageGoHome": ("Low-voltage return-to-home", "Return-to-home started because the battery voltage was low.", "battery"),
    "SeriousLowVoltageLanding": ("Critical-battery forced landing", "Forced landing started because the battery reached the critical (serious low) level.", "battery"),
    "RCOnekeyGoHome": ("RTH from remote-controller button", "Return-to-home was started from the remote controller's RTH button.", "flight"),
    "RCAssistantTakeoff": ("Assisted take-off from remote controller", "An assisted take-off was started from the remote controller.", "flight"),
    "RCAutoTakeoff": ("Auto take-off from remote controller", "An automatic take-off was started from the remote controller.", "flight"),
    "RCAutoLanding": ("Auto landing from remote controller", "An automatic landing was started from the remote controller.", "flight"),
    "AppAutoGoHome": ("RTH from the app", "Return-to-home was started from the mobile app.", "flight"),
    "AppAutoLanding": ("Auto landing from the app", "An automatic landing was started from the mobile app.", "flight"),
    "AppAutoTakeoff": ("Auto take-off from the app", "An automatic take-off was started from the mobile app.", "flight"),
    "OutOfControlGoHome": ("Signal-lost return-to-home (failsafe)", "Return-to-home started because the remote-controller signal was lost (failsafe RTH).", "radio_link"),
    "ApiAutoTakeoff": ("Auto take-off from SDK app", "An automatic take-off was commanded through the DJI SDK (third-party app or onboard computer).", "flight"),
    "ApiAutoLanding": ("Auto landing from SDK app", "An automatic landing was commanded through the DJI SDK.", "flight"),
    "ApiAutoGoHome": ("RTH from SDK app", "Return-to-home was commanded through the DJI SDK.", "flight"),
    "AvoidGroundLanding": ("Landing (ground avoidance)", "Landing triggered by the ground-avoidance logic.", "safety"),
    "AirportAvoidLanding": ("Forced landing in no-fly zone", "Landing triggered because the aircraft was inside or approaching an airport/no-fly zone.", "safety"),
    "TooCloseGoHomeLanding": ("RTH too close: landing instead", "Return-to-home was requested very close to the home point, so the aircraft landed in place.", "flight"),
    "TooFarGoHomeLanding": ("RTH too far: landing instead", "Return-to-home was requested too far from the home point, so the aircraft landed instead.", "flight"),
    "AppWPMission": ("Waypoint mission from app", "A waypoint mission started from the app.", "navigation"),
    "WPAutoTakeoff": ("Waypoint mission auto take-off", "Automatic take-off as part of a waypoint mission.", "navigation"),
    "GoHomeAvoid": ("RTH obstacle avoidance", "Obstacle avoidance during return-to-home.", "safety"),
    "PGoHomeFinish": ("Return-to-home finished", "The return-to-home sequence finished.", "flight"),
    "VertLowLimitLanding": ("Landing at low height limit", "Automatic landing started when the aircraft descended to its lower height limit near the ground.", "flight"),
    "BatteryForceLanding": ("Battery forced landing", "Forced landing triggered by the battery protection.", "battery"),
    "MCProtectGoHome": ("Flight-controller protective RTH", "Return-to-home triggered by flight-controller protection logic.", "safety"),
    "MotorblockLanding": ("Landing: motor blocked", "Landing triggered because a motor was detected as blocked.", "motors"),
    "AppRequestForceLanding": ("Forced landing requested by app", "A forced landing was requested by the app.", "flight"),
    "FakeBatteryLanding": ("Landing: battery not genuine", "Landing triggered because the battery failed authentication.", "battery"),
    "RTHComingObstacleLanding": ("RTH obstacle: landing", "Landing triggered by an obstacle during return-to-home.", "safety"),
    "IMUErrorRTH": ("IMU error return-to-home", "Return-to-home triggered by an IMU error.", "imu"),
}

GO_HOME_STATUS = {
    "Standby": "not returning", "Preascending": "pre-ascent", "Align": "turning towards home",
    "Ascending": "climbing to RTH altitude", "Cruise": "flying back towards home",
    "Braking": "braking", "Bypassing": "avoiding an obstacle",
}

NON_GPS_CAUSES = {
    "Already": "already not using GPS", "Forbid": "GPS use not allowed",
    "GpsNumNonEnough": "too few satellites", "GpsHdopLarge": "poor satellite geometry (HDOP too large)",
    "GpsPositionNonMatch": "GPS position did not agree with other sensors",
    "SpeedErrorLarge": "speed error too large", "YawErrorLarge": "heading error too large",
    "CompassErrorLarge": "compass error too large",
}

OSD_FLAGS: Dict[str, Tuple[str, str, str]] = {
    "compass_error": ("Compass error", "The aircraft reported a compass error (e.g. magnetic interference or calibration problem).", "compass"),
    "barometer_dead_in_air": ("Barometer failure in flight", "The aircraft reported that its barometer stopped working while airborne.", "sensors"),
    "motor_blocked": ("Motor blocked", "The aircraft reported a blocked motor.", "motors"),
    "not_enough_force": ("Not enough thrust", "The aircraft reported it could not produce enough thrust (DJI app: 'not enough force'), e.g. damaged propeller or motor, overload, or weak battery.", "motors"),
    "propeller_catapult": ("Propeller came off", "The aircraft reported a propeller ejected/loose (propeller catapult flag).", "motors"),
    "accelerometer_over_range": ("Accelerometer over range", "The accelerometer reading exceeded its measuring range (violent motion or impact).", "imu"),
    "vibrating": ("Excessive vibration", "The aircraft reported abnormal vibration.", "imu"),
    "out_of_limit": ("Flight limit reached", "The aircraft reported it reached a configured height or distance limit.", "safety"),
    "obstacle_sensor_error": ("Obstacle/ultrasonic sensor error", "The aircraft reported an error in its downward ultrasonic/obstacle sensor.", "sensors"),
}

# Battery protection state machine ([L-BATTERY]old:X=>>>>new:Y).
BATTERY_STATES = {
    "ON_GROUND": "on the ground", "SAFE_FLY": "normal flight (no battery action)",
    "GOHOME_CNTDOWN": "countdown before low-battery return-to-home",
    "GOHOME": "low-battery return-to-home", "LANDING": "low-battery landing",
    "FORCE_LANDING": "forced landing",
}

# FDI navigation-source abbreviations and fault reasons.
NS_SOURCES = {
    "GPS": ("GPS", "gps"), "RTK": ("RTK GNSS", "gps"), "VO": ("vision positioning", "sensors"),
    "ULTRASONIC": ("ultrasonic height sensor", "sensors"), "FUSION": ("sensor-fusion estimate", "navigation"),
    "BARO": ("barometer", "sensors"), "MAG": ("compass", "compass"), "MAGN": ("compass", "compass"),
    "IMU": ("IMU", "imu"), "COMPASS": ("compass", "compass"),
}
FDI_REASONS = {
    "conformity": "its data disagreed with the other navigation sources",
    "disconnect": "no data was received (disconnected)",
    "stuck_vel": "its velocity output stopped changing (stuck)",
    "stuck": "its output stopped changing (stuck)",
    "abrupt_pos": "its position jumped abruptly",
    "no_atti": "no attitude solution was available",
    "not_converge": "the estimate had not converged",
}

MOTOR_START_REASONS = {
    "ACT.Takeoff": "automatic take-off command",
    "MR_REQ_RC_NORMAL": "remote-controller stick command",
}
MOTOR_STOP_REASONS = {
    "ACT.landing": "landing completed",
    "landing.RC_Thr": "landed with the throttle stick held down",
}
RC_CMDS = {
    "STD_ASSI_TAKEOFF": "assisted take-off", "STD_START_MOTOR": "start motors",
    "STD_GO_HOME": "return-to-home", "STD_STOP_MOTOR": "stop motors",
    "STD_AUTO_LANDING": "auto landing", "STD_AUTO_TAKEOFF": "auto take-off",
}
RC_LOST_ACTIONS = {0: "hover", 1: "land", 2: "return-to-home"}

CTRL_SOURCES = {
    "RC_NORMAL": "remote-controller sticks", "RC_COMMAND": "remote-controller command",
    "MC_PROTECT": "flight-controller protection logic", "APP": "mobile app",
}
CTRL_MODES = {
    "ATTI": "ATTI (attitude) control", "ATTI_HOLD": "GPS position hold",
    "ASST_TAKEOFF": "assisted take-off", "AUTO_TAKEOFF_HOLD": "automatic take-off",
    "AUTO_LANDING": "automatic landing", "ENGINE_START": "motor start",
    "GO_HOME": "return-to-home", "GPS_ATTI": "GPS position hold",
}

# ---- post-processing helpers ------------------------------------------------

def _p_home(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    return {}


def _p_rad_ll(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    lat, lon = _rad2deg(v.get("lat_rad")), _rad2deg(v.get("lon_rad"))
    if lat is not None:
        v["lat"] = lat
        d["lat"] = f"{lat:.6f}"
    if lon is not None:
        v["lon"] = lon
        d["lon"] = f"{lon:.6f}"
    return {}


def _p_e6_ll(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    try:
        v["lat"] = v.pop("lat_e6") / 1e6
        v["lon"] = v.pop("lon_e6") / 1e6
    except (KeyError, TypeError):
        return {}
    d["lat"], d["lon"] = f"{v['lat']:.6f}", f"{v['lon']:.6f}"
    if v["lat"] == 0 and v["lon"] == 0:
        return {"_title": "Empty fly-safe zone slot from app", "_importance": "low",
                "_detail": "An app-supplied fly-safe database slot was printed with zero coordinates, i.e. the slot is empty."}
    return {}


def _p_date8(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    if "date" in v:
        v["date"] = d["date"] = _iso_date8(v["date"])
    return {}


def _p_mode_change(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ("from", "to", "mode"):
        if k in v:
            name, desc = FLIGHT_MODES.get(v[k], (v[k], "mode name as recorded by the app"))
            d[k + "_name"], d[k + "_desc"] = name, desc
    if v.get("to") in _HIGH_MODES or v.get("from") == "GoHome":
        out["_importance"] = "high"
    return out


def _p_action(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    a = v.get("action", "")
    if a in FLIGHT_ACTIONS:
        t, det, cat = FLIGHT_ACTIONS[a]
        return {"_title": t, "_detail": f"{det} (App-log flight action '{a}'.)", "_category": cat}
    return {"_title": f"Flight-controller action: {a}",
            "_detail": f"The app log recorded flight-controller action '{a}', which is not in DroneDog's table; the name is shown as recorded."}


def _p_osd_flag(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    f = v.get("flag", "")
    if f in OSD_FLAGS:
        t, det, cat = OSD_FLAGS[f]
        return {"_title": t, "_detail": det + f" (App-log OSD flag '{f}' became set.)", "_category": cat}
    return {}


def _p_nongps(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    c = v.get("cause", "")
    d["cause_text"] = NON_GPS_CAUSES.get(c, f"cause '{c}' as recorded" if c else "cause not recorded")
    return {}


def _p_rth_status(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    s = v.get("status", "")
    d["status_text"] = GO_HOME_STATUS.get(s, f"status '{s}' as recorded")
    return {}


def _p_motor_start_blocked(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    d["cause_text"] = _humanize(v.get("cause", "")).lower()
    return {}


def _p_motor_start(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    r = v.get("reason", "")
    d["reason_text"] = MOTOR_START_REASONS.get(r, f"reason code '{r}'")
    return {}


def _p_motor_stop(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    r = v.get("reason", "")
    d["reason_text"] = MOTOR_STOP_REASONS.get(r, f"reason code '{r}'")
    return {}


def _p_fsm(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    s = v.get("state", "").strip().lower()
    if s == "in air":
        return {"_title": "Aircraft airborne (flight state: in air)", "_importance": "high", "_category": "flight",
                "_detail": "The flight controller's state machine changed to 'in air': it considers the aircraft to have taken off."}
    if s == "on ground":
        return {"_title": "Aircraft on ground (flight state)", "_importance": "high", "_category": "flight",
                "_detail": "The flight controller's state machine changed to 'on ground'. This is logged when the motors start on the ground and again on landing."}
    if s == "motor off":
        return {"_title": "Motors off (flight state)", "_importance": "high", "_category": "motors",
                "_detail": "The flight controller's state machine changed to 'motor off': the motors are stopped."}
    return {}


def _p_rc_connect(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    if v.get("to") == "1":
        return {"_title": "Remote-controller link connected",
                "_detail": "The flight controller's remote-controller connection flag changed from 0 to 1: the controller link is up."}
    if v.get("to") == "0":
        return {"_title": "Remote-controller link lost",
                "_detail": "The flight controller's remote-controller connection flag changed from 1 to 0: the controller link was lost."}
    return {}


def _p_rc_cmd(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    c = v.get("cmd", "")
    d["cmd_text"] = RC_CMDS.get(c, _humanize(c).lower())
    return {}


def _p_rc_lost(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    a = v.get("act")
    d["act_text"] = RC_LOST_ACTIONS.get(a, f"code {a}") if isinstance(a, int) else str(a)
    v["action_text"] = d["act_text"]
    d["op_text"] = "set to" if v.get("op") == "set" else "read back as"
    return {}


def _p_ms(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(v.get("ms"), int):
        d["s"] = f"{v['ms'] / 1000:.1f}"
    return {}


def _p_battery_state(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    f, t = v.get("from", ""), v.get("to", "")
    d["from_text"] = BATTERY_STATES.get(f, f)
    d["to_text"] = BATTERY_STATES.get(t, t)
    if t in ("GOHOME_CNTDOWN", "GOHOME", "LANDING", "FORCE_LANDING"):
        return {"_importance": "high"}
    return {}


def _p_bat_time(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    u = _utc_from_epoch(v.get("epoch"))
    if u:
        v["utc"] = d["utc"] = u
    return {}


def _p_bat_ver(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    if str(v.get("version", "")).strip("v") == "255.255.255.255":
        return {"_detail": "Battery firmware version field from the flight controller's start-up banner. "
                           "The value v255.255.255.255 means no version was reported at that moment (all bytes 0xFF)."}
    return {}


def _p_fdi_ns(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    src = v.get("src", "")
    name, cat = NS_SOURCES.get(src, (src, "sensors"))
    d["src_text"] = name
    d["reason_text"] = FDI_REASONS.get(v.get("reason", ""), f"reason '{v.get('reason', '')}'")
    on = v.get("onoff") == "on"
    d["onoff_text"] = "raised" if on else "cleared"
    out: Dict[str, Any] = {"_category": cat}
    if on and src in ("GPS", "FUSION"):
        out["_importance"] = "high"
    return out


def _p_led(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    desc = v.get("desc", "")
    low = desc.lower()
    for keys, title, detail, imp, cat in LED_PATTERNS:
        if any(k in low for k in keys):
            return {"_title": title, "_detail": detail + f" (LED condition '{desc.strip()}'.)",
                    "_importance": imp, "_category": cat}
    return {"_detail": f"The aircraft's status-LED controller switched to the pattern for condition '{desc.strip()}'. "
                       "LED conditions mirror flight-controller states; this one is not in DroneDog's table."}


LED_PATTERNS: List[Tuple[Tuple[str, ...], str, str, str, str]] = [
    (("battery warning", "batt warning", "battery_status"), "LED: low-battery warning",
     "The status LEDs switched to the low-battery warning pattern.", "high", "battery"),
    (("rc completely lost", "rc lost"), "LED: remote-controller signal lost",
     "The status LEDs switched to the pattern for a completely lost remote-controller signal.", "high", "radio_link"),
    (("fly limit", "airport"), "LED: flight-limit / no-fly-zone warning",
     "The status LEDs switched to the flight-limit pattern (the firmware's own label gives airports as an example).", "high", "safety"),
    (("near hori", "near vert"), "LED: near height/distance limit",
     "The status LEDs switched to the pattern shown when the aircraft is near its horizontal (distance) or vertical (height) limit.", "high", "safety"),
    (("set home",), "LED: home point recorded signal",
     "The status LEDs switched to the 'home point set' pattern shown when the home point is recorded.", "normal", "home_point"),
    (("compass out of range", "compass"), "LED: compass warning",
     "The status LEDs switched to the compass-problem pattern (compass reading out of range).", "normal", "compass"),
    (("no atti",), "LED: attitude not available",
     "The status LEDs switched to the 'no attitude' pattern: the attitude estimate is not yet initialised/converged (normal for a short time after power-on).", "normal", "imu"),
    (("prevent motor on", "cannot start motor"), "LED: motor start prevented",
     "The status LEDs show that starting the motors is currently prevented.", "normal", "motors"),
    (("temperature not ready",), "LED: IMU warming up",
     "The status LEDs show that the IMU temperature is not yet ready after start-up (warm-up).", "low", "imu"),
    (("forearm led",), "LED: front arm lights switched off by camera",
     "The front (forearm) LEDs were switched off at the camera's request; DJI aircraft can turn the front LEDs off while recording or shooting.", "low", "system"),
    (("test led",), "LED: start-up self-test",
     "The status LEDs ran their start-up test pattern.", "low", "system"),
    (("normal flash", "normal led"), "LED: normal status pattern",
     "The status LEDs returned to their normal flashing pattern.", "low", "system"),
    (("farm app stop motor",), "LED: app motor stop/start indication",
     "LED condition related to the agricultural app's motor stop/start request.", "low", "system"),
    (("on_ctrl", "not_ctrl", "select", "pre_sel"), "LED: control/selection indication",
     "LED pattern for the aircraft's control/selection state (used when a controller selects or controls the aircraft).", "low", "system"),
    (("ced", "back ce"), "LED: status pattern change",
     "LED pattern change for an internal LED condition (label 'ced'); its meaning is not publicly documented.", "low", "system"),
]


def _p_ctrl(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    src = v.get("source", "")
    key = next((k for k in CTRL_SOURCES if src.startswith(k)), None)
    d["source_text"] = CTRL_SOURCES[key] if key else f"requester '{src}'"
    if src.startswith("APP_"):
        d["source_text"] = f"app/ground-station request ({src})"
    mode = v.get("mode", "")
    d["mode_text"] = CTRL_MODES.get(mode, _humanize(mode).lower())
    if mode in ("AUTO_LANDING", "ASST_TAKEOFF", "AUTO_TAKEOFF_HOLD", "ENGINE_START", "GO_HOME", "ATTI") \
            or src.startswith("MC_PROTECT"):
        return {"_importance": "high"}
    return {}


def _p_ctrl_reset(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    r = v.get("reason", "")
    if "motor stopped" in r:
        return {"_importance": "normal"}
    return {}


def _p_abort(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    why = v.get("why", "")
    if "ground not smooth" in why:
        return {"_title": "Landing aborted: ground not suitable", "_importance": "high", "_category": "safety",
                "_detail": "The flight controller aborted its automatic landing because it judged the ground below not smooth/level enough (landing protection). The aircraft stays airborne until the pilot acts."}
    if "app_exit_landing" in why:
        return {"_title": "Landing cancelled from the app", "_importance": "high", "_category": "flight",
                "_detail": "The automatic landing was cancelled by a request from the mobile app."}
    if "reach target height" in why:
        return {"_title": "Take-off target height reached", "_category": "flight",
                "_detail": "Automatic tasks were ended because the aircraft reached its take-off target height (end of auto take-off)."}
    if "assit takeoff finished" in why:
        return {"_title": "Assisted take-off finished", "_category": "flight",
                "_detail": "Automatic tasks were ended because the assisted take-off finished."}
    if "motor stopped" in why:
        return {"_title": "Automatic tasks ended: motors stopped", "_importance": "low"}
    return {}


def _p_geo_rel(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    names = {"OUT_NFZ": "outside any no-fly zone", "NFZ_UNKNOWN": "relation unknown (e.g. no valid position yet)",
             "IN_NFZ": "inside a no-fly zone"}
    f, t = v.get("from", ""), v.get("to", "")
    d["from_text"], d["to_text"] = names.get(f, f), names.get(t, t)
    if "IN" in (f.split("_")[0], t.split("_")[0]) or "NEAR" in f + t or "LIMIT" in f + t:
        return {"_importance": "high"}
    return {}


def _p_conn(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    who = v.get("who", "")
    cur = v.get("cur")
    d["who_text"] = "DJI Assistant (PC software)" if who == "assistant" else "mobile app"
    d["cur_text"] = {1: "connected", 0: "not connected"}.get(cur, f"state {cur}")
    if who == "app":
        return {"_importance": "normal", "_category": "radio_link"}
    return {}


def _p_reset(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    c = v.get("cause", "")
    if c == "power":
        return {"_title": "Flight controller started (power-on reset)",
                "_detail": "The flight controller recorded that it started because power was applied (a normal power-on)."}
    if c == "soft":
        return {"_title": "Flight controller restarted by software",
                "_detail": "The flight controller recorded that its last reset was a software-initiated restart (for example after a firmware update or a commanded reboot)."}
    if c == "watchdog":
        return {"_title": "Flight controller reset by watchdog", "_importance": "high",
                "_detail": "The flight controller recorded that its last reset was caused by its watchdog timer, i.e. the software stopped responding and the processor was restarted automatically."}
    return {}


def _p_fdi_ctrl(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    det = v.get("det", "").strip()
    if not det:
        return {"_title": "Fault-detection controller enabled",
                "_detail": "The flight controller's fault-detection (FDI) control monitor was switched on (start-up message)."}
    return {}


def _p_pwm(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    m = v.get("mode", "")
    if m == "CTRL":
        return {"_title": "Motor outputs active (actuator mode CTRL)",
                "_detail": "The motor output driver switched to active control: motor commands are now being sent (accompanies motor start)."}
    if m == "IDLE":
        return {"_title": "Motor outputs idle (actuator mode IDLE)",
                "_detail": "The motor output driver switched to idle: no active motor control (accompanies power-on and motor stop)."}
    return {}


def _p_param_change(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    name = v.get("name", "")
    info = _param_info(name)
    out: Dict[str, Any] = {}
    if info:
        v["friendly"] = info[0]
        d["friendly_note"] = f" ({info[0]})"
    else:
        d["friendly_note"] = ""
    if "app" in v.get("source", "").lower():
        out["_importance"] = "normal"
    return out


def _p_cam(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    return {}


def _p_sif(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    v["date"] = d["date"] = _iso_date8(v.get("date", ""))
    t = str(v.get("time", ""))
    if len(t) == 6 and t.isdigit():
        v["time"] = d["time"] = f"{t[0:2]}:{t[2:4]}:{t[4:6]}"
    return {}


def _p_activation(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    d["pid_note"] = f", product ID {v['product_id']}" if v.get("product_id") else ""
    t = v.get("time", "")
    if re.match(r"^\d+-\d+-\d+$", t):
        d["time"] = t.replace("-", ":")
    return {}


def _p_real_name(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    u = _utc_from_epoch(v.get("epoch"))
    d["utc_note"] = f" (the number converts to {u} UTC if read as a Unix time)" if u else ""
    if u:
        v["utc_if_epoch"] = u
    return {}


def _p_esc_fault(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    d["onoff_text"] = "raised" if v.get("onoff") == "on" else "cleared"
    return {}


def _p_fault_null(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    return {}


def _p_nmis_gohome(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    return {}


def _p_tag_module(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    mod = v.get("module", "")
    info = TAG_INFO.get(mod)
    d["module_desc"] = f" ({info[1]})" if info else ""
    return {}


def _p_imu_heading(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    if v.get("state") == "drift":
        return {"_title": "Heading estimate drifting", "_importance": "normal",
                "_detail": "The IMU heading estimator reported state 'drift': the heading estimate is flagged as drifting (not anchored)."}
    return {}


def _p_esc_status(v: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
    if v.get("status") == "0":
        return {"_importance": "low"}
    return {}


# ---------------------------------------------------------------------------
# Rules, ordered roughly by subsystem. Example strings are real corpus lines.
# ---------------------------------------------------------------------------

# ---- app-log (TXT) derived events -------------------------------------------
R("txt.takeoff", r"^Aircraft airborne$", "Take-off: aircraft left the ground",
  "DroneDog derived this from the app log: the aircraft's 'on ground' flag changed to false, i.e. it took off.",
  "flight", "high", "Aircraft airborne", kinds=("takeoff",))
R("txt.takeoff_at_start", r"^Aircraft already airborne at log start$", "Aircraft already airborne when log began",
  "The first record of the app log shows the aircraft already in the air, so the take-off itself is not in this log.",
  "flight", "high", "Aircraft already airborne at log start", kinds=("takeoff",))
R("txt.landing", r"^Aircraft on ground$", "Landing: aircraft on the ground",
  "DroneDog derived this from the app log: the aircraft's 'on ground' flag changed to true, i.e. it landed (or was set down).",
  "flight", "high", "Aircraft on ground", kinds=("landing",))
R("txt.motor_start", r"^Motors started$", "Motors started",
  "DroneDog derived this from the app log: the 'motors on' flag changed from off to on.",
  "motors", "high", "Motors started", kinds=("motor_start",))
R("txt.motor_running_at_start", r"^Motors already running at log start$", "Motors already running when log began",
  "The first record of the app log shows the motors already running, so the motor start is not in this log.",
  "motors", "high", "Motors already running at log start", kinds=("motor_start",))
R("txt.motor_stop", r"^Motors stopped$", "Motors stopped",
  "DroneDog derived this from the app log: the 'motors on' flag changed from on to off.",
  "motors", "high", "Motors stopped", kinds=("motor_stop",))
R("txt.initial_mode", r"^Initial flight mode (?P<mode>\w+(?:\(\d+\))?)$", "Flight mode at log start: {mode_name}",
  "The first record of the app log shows flight mode '{mode}' ({mode_desc}).",
  "flight", "normal", "Initial flight mode GPSAtti", post=_p_mode_change)
R("txt.mode_change", r"^Flight mode changed from (?P<from>[\w()]+) to (?P<to>[\w()]+)$",
  "Flight mode: {from_name} -> {to_name}",
  "The app log shows the flight mode changing from '{from}' ({from_desc}) to '{to}' ({to_desc}).",
  "flight", "normal", "Flight mode changed from GPSAtti to GPSSport", post=_p_mode_change)
R("txt.rth_start", r"^Return-to-home started \(status (?P<status>.+)\)$", "Return-to-home started",
  "The app log's return-to-home status left 'Standby': the aircraft began returning to its home point (first status: {status}, {status_text}).",
  "flight", "high", "Return-to-home started (status Cruise)", post=_p_rth_status)
R("txt.rth_end", r"^Return-to-home status back to Standby$", "Return-to-home ended",
  "The app log's return-to-home status returned to 'Standby': the return-to-home sequence ended (completed, cancelled or replaced by landing).",
  "flight", "high", "Return-to-home status back to Standby")
R("txt.rth_status", r"^Return-to-home status (?P<status>.+)$", "Return-to-home phase: {status}",
  "The app log's return-to-home status changed to '{status}' ({status_text}).",
  "flight", "normal", "Return-to-home status Ascending", post=_p_rth_status)
R("txt.fc_action", r"^Flight controller action: (?P<action>[\w()]+)$", "Flight-controller action: {action}",
  "The app log recorded a flight-controller action.", "flight", "high",
  "Flight controller action: OutOfControlGoHome", post=_p_action)
R("txt.low_battery", r"^Battery voltage warning level (?P<level>\d+) \(charge (?P<pct>\d+)%\)$",
  "Low-battery warning level {level} ({pct}% charge)",
  "The app log's battery warning level rose to {level} while the battery reported {pct}% charge. Level 1 is DJI's low-battery warning, level 2 the critical (serious low) warning.",
  "battery", "high", "Battery voltage warning level 1 (charge 30%)")
R("txt.gps_drop", r"^GPS health dropped from (?P<from_level>\d+) to (?P<level>\d+) \((?P<sats>\d+) satellites\)$",
  "GPS signal weakened (level {from_level} -> {level})",
  "The app log's GPS signal level (0-5 bars) dropped from {from_level} to {level} with {sats} satellites in use.",
  "gps", "high", "GPS health dropped from 4 to 2 (7 satellites)")
R("txt.gps_recovered", r"^GPS health recovered to (?P<level>\d+) \((?P<sats>\d+) satellites\)$",
  "GPS signal recovered (level {level})",
  "The app log's GPS signal level (0-5 bars) recovered to {level} with {sats} satellites in use.",
  "gps", "high", "GPS health recovered to 4 (12 satellites)")
R("txt.gps_lost", r"^GPS positioning no longer used \(cause: (?P<cause>[^)]*)\)$", "GPS positioning lost",
  "The app log shows the aircraft stopped using GPS for positioning ({cause_text}). Without GPS the aircraft cannot hold position and drifts with the wind (ATTI behaviour).",
  "gps", "high", "GPS positioning no longer used (cause: GpsNumNonEnough)", post=_p_nongps)
R("txt.rc_signal_drop", r"^RC (?P<name>\w+) signal dropped to 0% \(was (?P<was>\d+)%\)$", "Remote-controller {name} signal lost",
  "The app log's remote-controller {name} signal strength fell to 0% (previously {was}%).",
  "radio_link", "high", "RC uplink signal dropped to 0% (was 80%)")
R("txt.rc_signal_back", r"^RC (?P<name>\w+) signal restored to (?P<pct>\d+)%$", "Remote-controller {name} signal restored",
  "The app log's remote-controller {name} signal strength came back to {pct}%.",
  "radio_link", "high", "RC uplink signal restored to 75%")
R("txt.rc_lost", r"^OSD reports RC link lost \(rc_outcontrol\)$", "Remote-controller link lost",
  "The aircraft's OSD record set its 'RC out of control' flag: the aircraft was no longer receiving the remote controller.",
  "radio_link", "high", "OSD reports RC link lost (rc_outcontrol)")
R("txt.rc_back", r"^OSD reports RC link back \(rc_outcontrol cleared\)$", "Remote-controller link restored",
  "The aircraft's OSD 'RC out of control' flag cleared: the remote-controller link was restored.",
  "radio_link", "high", "OSD reports RC link back (rc_outcontrol cleared)")
R("txt.osd_flag", r"^OSD flag set: (?P<flag>\w+)$", "Aircraft fault flag: {flag}",
  "The app log's OSD record set fault flag '{flag}'.", "safety", "high",
  "OSD flag set: not_enough_force", post=_p_osd_flag)
R("txt.motor_start_blocked", r"^Motor start blocked: (?P<cause>[\w()]+)$", "Motor start blocked: {cause}",
  "The aircraft refused to start its motors; the app log gives the reason '{cause}' ({cause_text}).",
  "motors", "high", "Motor start blocked: CompassError", post=_p_motor_start_blocked)
R("txt.app_rth_alt", r"^Return to home altitude:\s*(?P<height>\d+)\s*M$", "App: return-to-home altitude {height} m",
  "Tip shown by the DJI app to the pilot: the return-to-home altitude is {height} m.",
  "configuration", "normal", "Return to home altitude:30M", kinds=("app_tip", "app_warning"))
R("txt.app_tip", r"^(?P<message>.+)$", "App tip: {message}",
  "Message the DJI flight app displayed to the pilot as a tip, recorded verbatim (it may be in the app's display language).",
  "other", "normal", "Taking off", kinds=("app_tip",))
R("txt.app_warning", r"^(?P<message>.+)$", "App warning: {message}",
  "Warning the DJI flight app displayed to the pilot, recorded verbatim (it may be in the app's display language).",
  "safety", "normal", "Strong wind. Fly with caution", kinds=("app_warning",))
R("txt.app_serious_warning", r"^(?P<message>.+)$", "App serious warning: {message}",
  "Serious warning the DJI flight app displayed to the pilot, recorded verbatim (it may be in the app's display language).",
  "safety", "high", "Motor overloaded", kinds=("app_serious_warning",))

# ---- flight mode ([L-FLYMODE]) ----------------------------------------------
R("flymode.hover", r"^SWITCH2HOVER!", "Switched to hover (position hold)",
  "The flight-mode logic put the aircraft into its hover/hold state, holding its current position and height when positioning is available. "
  "It is logged routinely (for example after take-off, after an automatic manoeuvre ends, or when motors stop); the line itself does not state the cause.",
  "flight", "normal", "23663 [L-FLYMODE]SWITCH2HOVER!", tags=_t("L-FLYMODE"))
R("flymode.gps2atti", r"^GPS2ATTI!", "Switched from GPS mode to ATTI mode",
  "The flight controller changed from GPS-assisted control (position hold) to ATTI (attitude-only: it stays level and holds height but drifts with the wind). "
  "This appears at power-on before satellite positioning is usable; during flight it means GPS positioning was lost or rejected.",
  "flight", "high", "[L-FLYMODE]GPS2ATTI!Init atti dynscale scale 0.000000", tags=_t("L-FLYMODE"))
R("flymode.req_force_landing", r"^Req force landing!", "Forced landing requested",
  "A forced (immediate) landing was requested of the flight-mode logic.",
  "flight", "high", "[L-FLYMODE]Req force landing!", tags=_t("L-FLYMODE"))
R("flymode.recv_force_landing", r"^Recv force landing cmd!\((?P<a>\d+),(?P<b>\d+),(?P<c>\d+)\)", "Forced-landing command received",
  "The flight-mode logic received a forced-landing command (internal arguments {a},{b},{c}, not documented).",
  "flight", "high", "[L-FLYMODE]Recv force landing cmd!(0,0,1)", tags=_t("L-FLYMODE"))
R("flymode.reset_force_landing", r"^reset all force landing cmd", "Forced-landing requests cleared",
  "All pending forced-landing commands were reset (cleared).",
  "flight", "normal", "[L-FLYMODE]reset all force landing cmd", tags=_t("L-FLYMODE"))
R("flymode.rc_force_landing", r"^rc2\w+ sets force landing!", "Remote controller set forced landing",
  "The remote-controller interface set a forced landing (for example the pilot holding the landing control).",
  "flight", "high", "[L-FLYMODE]rc2wm220 sets force landing!", tags=_t("L-FLYMODE"))
R("flymode.app_force_landing", r"^App sets force landing!", "App set forced landing",
  "The mobile app commanded a forced landing.",
  "flight", "high", "[L-FLYMODE]App sets force landing!", tags=_t("L-FLYMODE"))
R("flymode.rc_assisted_takeoff", r"^MR_REQ_R_RC_ASST_TAKEOFF", "Assisted take-off requested from remote controller",
  "The flight-mode logic received a remote-controller request for assisted take-off (the aircraft climbs away from the ground under automatic control after motor start).",
  "flight", "high", "[L-FLYMODE]MR_REQ_R_RC_ASST_TAKEOFF", tags=_t("L-FLYMODE"))
R("flymode.ctrl_request", r"\[Ctrl<(?P<n>\d+)>\]\s*REQ_(?P<source>[A-Z0-9_]+)\s+(?P<mode>[A-Z0-9_]+)\s+ctrl_(?P<ctrl>\w+)",
  "Control mode: {mode_text}",
  "The flight controller granted control-mode request '{mode}' (handler ctrl_{ctrl}) from {source_text}; control slot {n}.",
  "flight", "normal", "[L-FLYMODE][Ctrl<1>] REQ_RC_NORMAL ATTI_HOLD ctrl_gps_atti", tags=_t("L-FLYMODE"), post=_p_ctrl)
R("flymode.ctrl_reset", r"^CTRL reset all by (?P<reason>.+?)\s*$", "Control state reset: {reason}",
  "The flight-mode logic reset its control requests because of: {reason}.",
  "flight", "low", "[L-FLYMODE]CTRL reset all by rc mode switch", tags=_t("L-FLYMODE"), post=_p_ctrl_reset)
R("flymode.rc_mode_switch", r"^(?:\[RC\] )?mode switch changed!?", "Flight-mode switch moved on remote controller",
  "The flight-mode switch on the remote controller changed position (for example between P/S/A or N/S/T positions).",
  "flight", "normal", "[L-FLYMODE][RC] mode switch changed", tags=_t("L-FLYMODE", "L-RC"))
R("flymode.landing_reset", r"^\[LANDING\] reset landing status", "Landing status reset",
  "The flight-mode logic reset its landing state.",
  "flight", "low", "[L-FLYMODE][LANDING] reset landing status", tags=_t("L-FLYMODE"))

# ---- home point ([L-HOME]) ---------------------------------------------------
_HOME_ALT_NOTE = "The altitude field ({alt} m) is the flight controller's altitude reference at that point; the log does not state its datum."
R("home.first", r"^First\.\s*lati:\s*" + LAT + r"\s+longti:\s*" + LON + r"\s+alti:\s*" + ALT,
  "Home point first recorded",
  "The flight controller recorded the home point (the return-to-home destination) for the first time in this power cycle at latitude {lat}, longitude {lon}. " + _HOME_ALT_NOTE,
  "home_point", "high", "[L-HOME]First. lati:46.5214286 longti: 6.5673037 alti:355.61", tags=_t("L-HOME"))
R("home.update_rtk", r"^Update RTK\.\s*lati:\s*" + LAT + r"\s+longti:\s*" + LON + r"\s+alti:\s*" + ALT +
  r"(?:\s+alti_F:\s*(?P<alti_f>\d+))?(?:\s+curalt:\s*(?P<curalt>-?[\d.]+))?",
  "Home point updated (RTK position)",
  "The home point was updated using the RTK (high-precision GNSS) position: latitude {lat}, longitude {lon}, altitude {alt} m (RTK altitude reference).",
  "home_point", "high", "[L-HOME]Update RTK. lati:46.5213889 longti: 6.5672863 alti:465.70 alti_F:2", tags=_t("L-HOME"))
R("home.update", r"^Update\.\s*lati:\s*" + LAT + r"\s+longti:\s*" + LON + r"\s+alti:\s*" + ALT +
  r"(?:\s+curalt:\s*(?P<curalt>-?[\d.]+))?",
  "Home point updated",
  "The flight controller updated the home point to latitude {lat}, longitude {lon}. DJI aircraft normally refresh the home point at take-off, and the pilot can reset it from the app or controller. " + _HOME_ALT_NOTE,
  "home_point", "high", "[L-HOME]Update. lati:46.5214217 longti: 6.5673021 alti:355.46", tags=_t("L-HOME"))
R("home.init_hp", r"^\[InitHP-(?P<n>\d+)\]:\s*lati:\s*" + LAT + r"\s+longti:\s*" + LON + r"\s+alti:\s*" + ALT +
  r"\((?P<q>\d+)\)\s*srcTp:(?P<src_type>\d+)\s*setTp:(?P<set_type>\d+)",
  "Home point initialised",
  "The flight controller initialised the home point at latitude {lat}, longitude {lon}, altitude {alt} m (source type {src_type}, set type {set_type}; these type codes are not publicly documented).",
  "home_point", "high", "[L-HOME][InitHP-2]: lati:40.0825682 longti:-88.2006590 alti:192.51(0) srcTp:2 setTp:2", tags=_t("L-HOME"))
R("home.upd_hp", r"^\[UpdHP\]:\s*lati:\s*" + LAT + r"\s+longti:\s*" + LON + r"\s+alti:\s*" + ALT +
  r"\((?P<q>\d+)\)\s*srcTp:(?P<src_type>\d+)\s*setTp:(?P<set_type>\d+)",
  "Home point updated",
  "The flight controller updated the home point to latitude {lat}, longitude {lon}, altitude {alt} m (source type {src_type}, set type {set_type}; type codes not publicly documented).",
  "home_point", "high", "[L-HOME][UpdHP]: lati:40.0825683 longti:-88.2006590 alti:192.51(1) srcTp:2 setTp:3 cnt", tags=_t("L-HOME"))
R("home.legacy_first", r"^" + LAT + r"\s+" + LON + r"\s+" + ALT + r"\s+First Home Point", "Home point first recorded",
  "Older-firmware home-point line: the first home point of this power cycle was recorded at latitude {lat}, longitude {lon} (altitude field {alt} m, datum not stated).",
  "home_point", "high", "41.2907681 -72.9621732 -23.95 First Home Point")
R("home.legacy", r"^" + LAT + r"\s+" + LON + r"\s+" + ALT + r"\s+Home Point", "Home point recorded",
  "Older-firmware home-point line: the home point was (re)recorded at latitude {lat}, longitude {lon} (altitude field {alt} m, datum not stated).",
  "home_point", "high", "41.2907101 -72.9622394 -22.98 Home Point")
R("home.rth_height_stored", r"^target go-home height\((?P<height>\d+(?:\.\d+)?)\) is <= 500m and will be stored",
  "Return-to-home altitude set to {height} m",
  "A new return-to-home altitude of {height} m was accepted (it is within the 500 m maximum) and stored. On RTH the aircraft climbs to this height before flying home.",
  "configuration", "normal", "[L-HOME]target go-home height(20) is <= 500m and will be stored", tags=_t("L-HOME"))
R("home.rth_height_result", r"^set go home height, result:(?P<result>-?\d+)", "Return-to-home altitude setting result {result}",
  "Result code of the request to set the return-to-home altitude (0 conventionally indicates success).",
  "configuration", "low", "[L-HOME]set go home height, result:0", tags=_t("L-HOME"))
R("home.takeoff_height_coord", r"^takeoff height coordinate changed \((?P<value>-?[\d.]+)\) based on:(?P<basis>\d+)",
  "Take-off height reference changed",
  "The take-off height reference was changed (value {value}, basis code {basis}; the basis codes are not publicly documented).",
  "home_point", "normal", "[L-HOME]takeoff height coordinate changed (10.072708) based on:2", tags=_t("L-HOME"))
R("home.coord_rtk", r"^homepoint coor changed \((?P<dx>-?[\d.]+),(?P<dy>-?[\d.]+)\) based on RTK",
  "Home-point coordinates adjusted using RTK",
  "The home-point coordinates were shifted based on the RTK position (offset values {dx}, {dy} as logged; units not stated).",
  "home_point", "normal", "[L-HOME]homepoint coor changed (10.271335,-42.824013) based on RTK:", tags=_t("L-HOME"))

# ---- take-off ([L-TAKEOFF]) --------------------------------------------------
R("takeoff.alt", r"^alti:\s*(?P<alt>-?[\d.]+)\s+tors:\s*(?P<yaw>-?[\d.]+)", "Take-off recorded (altitude {alt} m)",
  "The take-off module recorded the altitude at take-off ({alt} m, the flight controller's altitude reference) and the heading ('tors', DJI's term for the yaw axis) of {yaw} degrees. DatCon uses this line as the take-off altitude.",
  "flight", "high", "[L-TAKEOFF]alti: 63.761677 tors: -44.675697", tags=_t("L-TAKEOFF"))
R("takeoff.rtk_alt", r"^RTK alti:\s*(?P<alt>-?[\d.]+)", "Take-off RTK altitude {alt} m",
  "The take-off module recorded the RTK (high-precision GNSS) altitude at take-off: {alt} m.",
  "flight", "high", "[L-TAKEOFF] RTK alti: 445.702484", tags=_t("L-TAKEOFF"))
R("takeoff.legacy_alt", r"^\[TO\.ALT\s*\]\s*(?P<alt>-?[\d.]+)", "Take-off recorded (altitude {alt} m)",
  "Older-firmware take-off line: altitude at take-off {alt} m (flight-controller altitude reference).",
  "flight", "high", "[TO.ALT ] -42.983475")
R("takeoff.auto_enter", r"^enter auto takeoff plan", "Automatic take-off started",
  "The flight controller started its automatic take-off sequence (typically commanded from the app).",
  "flight", "high", "[L-TAKEOFF]enter auto takeoff plan", tags=_t("L-TAKEOFF"))
R("takeoff.auto_exit", r"^exit auto takeoff plan", "Automatic take-off sequence ended",
  "The automatic take-off sequence ended (normally because the target height was reached).",
  "flight", "normal", "[L-TAKEOFF]exit auto takeoff plan!", tags=_t("L-TAKEOFF"))
R("takeoff.assist_enter", r"^enter assit takeoff plan", "Assisted take-off started",
  "The flight controller started an assisted take-off (automatic climb away from the ground after the pilot's command).",
  "flight", "high", "[L-TAKEOFF]enter assit takeoff plan", tags=_t("L-TAKEOFF"))
R("takeoff.assist_exit", r"^exit assit takeoff plan", "Assisted take-off sequence ended",
  "The assisted take-off sequence ended.",
  "flight", "normal", "[L-TAKEOFF]exit assit takeoff plan", tags=_t("L-TAKEOFF"))

# ---- motors ([L-FMU/MOTOR], legacy M.Start) ----------------------------------
R("motor.start", r"^Start\.\s*reason:\s*(?P<reason>\S+)", "Motors started ({reason_text})",
  "The flight controller started the motors; reason code '{reason}' ({reason_text}). DatCon uses this line as the motor-start time.",
  "motors", "high", "[L-FMU/MOTOR]Start. reason:ACT.Takeoff", tags=_t("L-FMU/MOTOR"), post=_p_motor_start)
R("motor.stop", r"^Stop\.\s*reason:\s*(?P<reason>\S+)", "Motors stopped ({reason_text})",
  "The flight controller stopped the motors; reason code '{reason}' ({reason_text}). DatCon uses this line as the motor-stop time.",
  "motors", "high", "[L-FMU/MOTOR] Stop. reason:ACT.landing", tags=_t("L-FMU/MOTOR"), post=_p_motor_stop)
R("motor.legacy_start", r"^\[M\.\s*Start\]\s*(?P<reason>\S+)", "Motors started ({reason_text})",
  "Older-firmware motor-start line; reason code '{reason}' ({reason_text}).",
  "motors", "high", "[M.Start]ACT.Takeoff", post=_p_motor_start)
R("motor.legacy_stop", r"^\[M\.\s*Stop\]\s*(?P<reason>\S+)", "Motors stopped ({reason_text})",
  "Older-firmware motor-stop line; reason code '{reason}' ({reason_text}).",
  "motors", "high", "[M. Stop]landing.RC_Thr", post=_p_motor_stop)
R("motor.total", r"^Total start times:\s*(?P<count>\d+),\s*time:\s*(?P<seconds>[\d.]+)",
  "Motor run #{count} ended ({seconds} s total motor time)",
  "Logged when the motors stop: this was motor start number {count} since power-on, and the motors have run for {seconds} s in total since power-on (in the corpus the value equals the summed start-to-stop intervals).",
  "motors", "normal", "[L-FMU/MOTOR]Total start times: 1, time: 357.78", tags=_t("L-FMU/MOTOR"))
R("motor.legacy_total", r"^Motor Start\s+(?P<count>\d+)\s+Total\s+(?P<total>[\d.]+)", "Motor start #{count}",
  "Older-firmware motor counter line: motor start number {count}; the 'Total' field is {total} (unlabelled in the log).",
  "motors", "normal", "Motor Start  3 Total  793.84")
R("motor.safe_near_ground", r"^safe_near_grd:(?P<state>true|false)", "Near-ground safety flag: {state}",
  "Internal motor-safety flag indicating whether the aircraft is considered safely near the ground ({state}). It toggles around take-off and landing.",
  "motors", "low", "[L-FMU/MOTOR]safe_near_grd:true", tags=_t("L-FMU/MOTOR"))
R("motor.esc_disconnect", r"^fault (?P<onoff>on|off)\s*,\s*esc\((?P<esc>\d+)\) is disconnect",
  "ESC {esc} disconnect fault {onoff_text}",
  "The motor manager {onoff_text} a fault for electronic speed controller (ESC) {esc} not communicating. In the corpus a raise/clear pair is common during power-on while ESCs come up.",
  "motors", "normal", "[L-FMU/MOTOR]fault on , esc(1) is disconnect", tags=_t("L-FMU/MOTOR"), post=_p_esc_fault)
R("motor.force_disable_flag", r"^read motor force disable flag successfully", "Motor force-disable flag read",
  "Start-up message: the stored motor force-disable flag was read successfully.",
  "motors", "low", "[L-FMU/MOTOR]read motor force disable flag successfully!", tags=_t("L-FMU/MOTOR"))
R("motor.esc_err", r"^esc err, status:(?P<status>\w+)", "ESC error status {status}",
  "The motor manager reported ESC error status code {status} (0 means no error bits set).",
  "motors", "normal", "[L-FMU/MOTOR]esc err, status:0", tags=_t("L-FMU/MOTOR"), post=_p_esc_status)

# ---- flight state machine ([L-FMU/FSM]) --------------------------------------
R("fsm.state", r"^state changed\.\s*cur:\s*(?P<state>.+?)\s*$", "Flight state: {state}",
  "The flight controller's state machine changed to '{state}'.",
  "flight", "normal", "[L-FMU/FSM]state changed. cur: in air", tags=_t("L-FMU/FSM"), post=_p_fsm)
R("fsm.near_ground", r"^near ground", "Aircraft near the ground",
  "The flight state machine judged the aircraft to be near the ground (logged around take-off, landing and while on the ground).",
  "flight", "normal", "[L-FMU/FSM]near ground", tags=_t("L-FMU/FSM"))
R("fsm.not_near_ground", r"^not near ground", "Aircraft no longer near the ground",
  "The flight state machine judged the aircraft to be clear of the ground (it has climbed away).",
  "flight", "normal", "[L-FMU/FSM]not near ground", tags=_t("L-FMU/FSM"))

# ---- remote controller ([L-RC]) -----------------------------------------------
R("rc.raw_out_of_range", r"^\[FATAL\]rc raw data not in range", "Remote-control input data out of range",
  "The flight controller flagged raw remote-control channel data as outside the expected range. Despite the 'FATAL' label this line repeats many times in ordinary flights in the corpus; on its own it does not show a loss of control.",
  "radio_link", "normal", "[L-RC][FATAL]rc raw data not in range?", tags=_t("L-RC"))
R("rc.lock_cmd", r"^set rc lock cmd!", "Remote-controller lock command",
  "The flight controller applied a remote-controller 'lock' command; in the corpus this accompanies waypoint-mission camera actions (see '[L-MIS][WP] rc lock param').",
  "radio_link", "low", "[L-RC]set rc lock cmd!", tags=_t("L-RC"))
R("rc.lock_cmd_ok", r"^set rc lock cmd (?P<value>\d+) success!", "Remote-controller lock command applied",
  "The remote-controller lock command (value {value}) was applied successfully.",
  "radio_link", "low", "[L-RC]set rc lock cmd 0 success!", tags=_t("L-RC"))
R("rc.start_landing", r"^Start landing\.\s*rc vib!", "Landing started (controller vibrates)",
  "The flight controller started a landing and told the remote controller to vibrate to alert the pilot.",
  "flight", "high", "[L-RC]Start landing. rc vib!", tags=_t("L-RC"))
R("rc.emergency_brake", r"^Emergency break\.\s*rc vib!", "Emergency brake (controller vibrates)",
  "The flight controller logged an emergency brake: the aircraft is commanded to stop and hover, and the remote controller vibrates as an alert. The line does not say whether the pilot (pause/brake button) or an automatic protection triggered it.",
  "flight", "high", "[L-RC]Emergency break. rc vib!", tags=_t("L-RC"))
R("rc.connect", r"^rc_connect:(?P<from>\d)=>(?P<to>\d)", "Remote-controller connection {from} -> {to}",
  "The flight controller's remote-controller connection flag changed from {from} to {to}.",
  "radio_link", "high", "[L-RC]rc_connect:0=>1", tags=_t("L-RC"), post=_p_rc_connect)
R("rc.resume", r"^rc_resume @ \[(?P<ms>\d+) ms\]", "Remote-controller input resumed",
  "Remote-controller input became active again at {s} s on the flight-controller clock. In the corpus this appears once per power cycle when the controller link is first established; it is also logged after an interruption.",
  "radio_link", "high", "[L-RC]rc_resume @ [124130 ms]", tags=_t("L-RC"), post=_p_ms)
R("rc.resume_lock", r"^(?P<op>set|clr) rc_resume_lock", "Remote-controller resume lock {op}",
  "Internal flag guarding remote-controller resume handling was set or cleared ('{op}').",
  "radio_link", "low", "[L-RC]set rc_resume_lock", tags=_t("L-RC"))
R("rc.cmd", r"^rc cmd:(?P<cmd>\w+)", "Remote-controller command: {cmd_text}",
  "The remote controller sent command '{cmd}' ({cmd_text}) to the flight controller.",
  "radio_link", "high", "[L-RC]rc cmd:STD_GO_HOME", tags=_t("L-RC"), post=_p_rc_cmd)
R("rc.func_disable", r"^rc_func_disable @ \[(?P<ms>\d+) ms\]", "Remote-controller functions disabled",
  "Remote-controller functions were disabled at {s} s on the flight-controller clock.",
  "radio_link", "normal", "[L-RC]rc_func_disable @ [863317 ms]", tags=_t("L-RC"), post=_p_ms)

# ---- battery ([L-BATTERY], legacy) -------------------------------------------
R("battery.state", r"^old:(?P<from>\w+)=+>+new:(?P<to>\w+)", "Battery protection state: {from} -> {to}",
  "The battery-protection state machine moved from {from} ({from_text}) to {to} ({to_text}).",
  "battery", "normal", "[L-BATTERY]old:SAFE_FLY=>>>>new:GOHOME_CNTDOWN", tags=_t("L-BATTERY"), post=_p_battery_state)
R("battery.countdown", r"^cntdown->(?P<count>\d+)", "Low-battery RTH countdown: {count}",
  "Countdown before an automatic low-battery return-to-home (the DJI app shows a 10-second countdown the pilot can cancel). In the corpus it counts 10..1 at one-second intervals after the state change SAFE_FLY -> GOHOME_CNTDOWN and is followed by GOHOME.",
  "battery", "high", "[L-BATTERY]cntdown->10", tags=_t("L-BATTERY"))
R("battery.alarm_level", r"^get alarm level (?P<level>\d+): (?P<pct>\d+)%, func:(?P<func>\d+)",
  "Low-battery warning level {level} threshold: {pct}%",
  "The low-battery warning level {level} threshold read from settings is {pct}% (action code {func}).",
  "battery", "normal", "[L-BATTERY]get alarm level 1: 30%, func:0", tags=_t("L-BATTERY"))
R("battery.power_off_state", r"^power off\((?P<a>\d+)\)\s*-->\s*\((?P<b>[\d.]+)\)", "Battery power-off status message",
  "Battery power-off status line; the numbers ({a} -> {b}) are internal codes that are not publicly documented.",
  "battery", "low", "[L-BATTERY]power off(3) --> (1.0)", tags=_t("L-BATTERY"))
R("battery.comm_init", r"^-+comm_bat_init-+", "Battery communication initialised",
  "Start-up message: the flight controller initialised communication with the smart battery.",
  "battery", "low", "[L-BATTERY]-------comm_bat_init-------", tags=_t("L-BATTERY"))
R("battery.auth", r"^bat_auth_\w+?_(?P<what>init|stop)", "Battery authentication routine {what}",
  "Start-up message from the battery authentication routine (DJI checks that the battery is genuine).",
  "battery", "low", "[L-BATTERY]bat_auth_origa_init", tags=_t("L-BATTERY"))
R("battery.rth_speeds", r"^(?:\[smart_battery\])?this fireware calc gohme speed:(?P<gohome_speed>[\d.]+) - land speed:(?P<land_speed>[\d.]+)",
  "Smart-RTH battery estimate speeds",
  "The firmware uses a return speed of {gohome_speed} and a landing speed of {land_speed} (values consistent with m/s) when estimating the battery needed to return home and land.",
  "battery", "low", "[L-BATTERY]this fireware calc gohme speed:7.800000 - land speed:1.500000", tags=_t("L-BATTERY"))
R("battery.time", r"^bat time (?P<epoch>\d+) \((?P<y>\d+) (?P<mo>\d+) (?P<d>\d+) (?P<h>\d+) (?P<mi>\d+) (?P<s>\d+)\)",
  "Battery clock time: {utc} UTC",
  "The smart battery's clock read {epoch} seconds since 1970 ({utc} UTC; the bracketed fields show the same date/time).",
  "battery", "normal", "[L-BATTERY]bat time 1618827888 (2021 4 19 10 24 48)", tags=_t("L-BATTERY"), post=_p_bat_time)
R("battery.rtc_ok", r"^bat rtc set ok", "Battery clock set",
  "The battery's real-time clock was set successfully.",
  "battery", "low", "[L-BATTERY]bat rtc set ok", tags=_t("L-BATTERY"))
R("battery.name", r"^Battery name\s*:(?P<name>.*)$", "Battery cell/manufacturer name",
  "Battery identification from the start-up banner: name field '{name}'.",
  "battery", "normal", "[L-BATTERY]Battery name    :ATL  NVT", tags=_t("L-BATTERY"))
R("battery.design_cap", r"^Design cap\s*:\s*(?P<capacity_mah>\d+)", "Battery design capacity: {capacity_mah} mAh",
  "The smart battery reports a design capacity of {capacity_mah} mAh.",
  "battery", "normal", "[L-BATTERY]Design cap      :5870", tags=_t("L-BATTERY"))
R("battery.mfg_date", r"^manufacture Date\s*:\s*(?P<date>[\d/.-]+)", "Battery manufacture date: {date}",
  "The smart battery reports manufacture date {date}.",
  "battery", "normal", "[L-BATTERY]manufacture Date:2018/11/15", tags=_t("L-BATTERY"))
R("battery.serial", r"^Serial number\s*:\s*(?P<serial>\S+)", "Battery serial number: {serial}",
  "The smart battery reports serial number {serial}. Useful to tie a physical battery to this flight; DatCon reports this field as the battery serial.",
  "battery", "normal", "[L-BATTERY]Serial number   :718", tags=_t("L-BATTERY"))
R("battery.app_version", r"^bat app version:\((?P<version>[\d.]+)\)", "Battery firmware version {version}",
  "The smart battery's firmware (application) version is {version}.",
  "battery", "low", "[L-BATTERY]bat app version:(2.0.7.54)", tags=_t("L-BATTERY"))
R("battery.cycles", r"^cycle count\s*:\s*(?P<cycles>\d+)", "Battery charge-cycle count: {cycles}",
  "The smart battery reports {cycles} charge cycles.",
  "battery", "normal", "[L-BATTERY]cycle count    :8", tags=_t("L-BATTERY"))
R("battery.ack_poweroff", r"^(?P<a>[\d.]+) \((?P<code>[0-9a-fx.]+)\) acked poweroff", "Battery acknowledged power-off",
  "The battery acknowledged a power-off request (internal fields {a} / {code}).",
  "battery", "low", "[L-BATTERY]3.6 (0.b) acked poweroff", tags=_t("L-BATTERY"))
R("battery.barcode", r"^(?:\[BAT\])?(?:Battery )?barcode(?:\[\d+\])?\s*:\s*(?P<barcode>\S+)", "Battery barcode: {barcode}",
  "The smart battery reports barcode {barcode} (a battery identifier).",
  "battery", "normal", "[L-BATTERY]Battery barcode:0DQAFBG0320081", tags=_t("L-BATTERY"), flags=re.I)
R("battery.upgrade_done", r"^finish battery upgrading", "Battery firmware update finished",
  "The battery firmware update procedure finished.",
  "battery", "normal", "[L-BATTERY]finish battery upgrading", tags=_t("L-BATTERY"))
R("battery.power_off_msg", r"^\[!\]get power off msg! motor:(?P<motor>\d+)", "Battery power-off request received",
  "The flight controller received a power-off message from the battery (motor state flag {motor}; 0 = motors not running).",
  "battery", "normal", "[L-BATTERY][!]get power off msg! motor:0", tags=_t("L-BATTERY"))
R("battery.legacy_power_off", r"^Battery power off", "Battery power off",
  "Older-firmware line: the battery reported power off.",
  "battery", "normal", "Battery power off")
R("battery.legacy_smart_cfg", r"^\[BATTERY\]:reset default smart cfg - L1:(?P<l1>\d+)\s+L2:(?P<l2>\d+)", "Smart-battery warning settings reset to defaults",
  "The smart-battery warning configuration was reset to defaults (L1 code {l1}, L2 code {l2}).",
  "battery", "low", "[BATTERY]:reset default smart cfg - L1:1  L2:2")
R("battery.legacy_info", r"^(?:Battery info:|Manufacturer name\s*:|Device name\s*:|Device chemistry\s*:|\[BAT\]read )(?P<value>.*)$",
  "Battery information field",
  "Older-firmware battery information banner line (for example manufacturer, gauge chip or chemistry).",
  "battery", "low", "Device chemistry :LION")
R("battery.dev_sn", r"^(?P<dev>\d+)-BAT_LOG:\[bat\]sn:(?P<serial>\w+),(?P<code>0x[0-9a-fA-F]+)", "Battery serial number: {serial}",
  "The battery log (device {dev}) reports battery serial number {serial} (code {code}).",
  "battery", "normal", "[L-DEV]11-BAT_LOG:[bat]sn:6JYPL5KDA0037U,0x9030027", tags=_t("L-DEV"))
R("battery.dev_auth", r"^(?P<dev>\d+)-(?:BAT_LOG:\[auth process\]|G:\[auth process\]|ss\]|ocess\]|tep|\s*\d+ result|ult)?:?\s*(?:step:\s*(?P<step>\d+)\s*)?result:\s*(?P<result>\d+) err: (?P<err>\d+)",
  "Battery authentication step result",
  "Battery authentication log line from device {dev} (step {step}, result {result}, error {err}). Some of these lines are truncated fragments in the log.",
  "battery", "low", "[L-DEV]11-BAT_LOG:[auth process]: step: 4 result: 0 err: 0", tags=_t("L-DEV"))
R("battery.api_gohome_status", r"^set battery go home status:(?P<status>\d+)", "Battery go-home status set ({status})",
  "The SDK/API interface set the battery go-home status to code {status} (codes not publicly documented).",
  "battery", "normal", "[L-API]set battery go home status:2", tags=_t("L-API"))

# ---- firmware / identity -----------------------------------------------------
R("version.fc_serial", r"^Mc\s+ID\s*:\s*(?P<serial>\S+)", "Flight-controller serial number: {serial}",
  "Serial number of the flight controller ('Mc ID'), identifying the aircraft's main controller. DatCon reports this field as the aircraft's mcID(SN).",
  "system", "normal", "[L-FMU/VERSION]Mc  ID  :0UYKFBE00200NV")
R("version.fc_firmware", r"^Mc\s+Ver\s*:\s*(?P<version>\S+)", "Flight-controller firmware {version}",
  "Firmware version of the flight controller ('Mc Ver').",
  "system", "normal", "[L-FMU/VERSION]Mc  Ver :v3.2.44.7")
R("version.battery_fw", r"^Bat\s+Ver\s*:\s*(?P<version>\S+)", "Battery firmware version field: {version}",
  "Battery firmware version field from the flight controller's start-up banner.",
  "battery", "low", "[L-FMU/VERSION]Bat Ver :v255.255.255.255", post=_p_bat_ver)
R("version.svn", r"^svn\s+Ver\s*:\s*(?P<svn>\S+)", "Firmware source revision {svn}",
  "Source-control revision identifier of the flight-controller firmware build.",
  "system", "low", "[L-FMU/VERSION]svn Ver :ff21b233464aeeb77c0a8ddf7ee22122826284eb")
R("version.build_time", r"^Time\s*:\s*(?:commit:(?P<commit>[\d-]+ [\d:]+)\s*/build:(?P<build>[\d-]+ [\d:]+)|(?P<when>.+))",
  "Firmware build time",
  "Build/commit time of the flight-controller firmware (this is when the firmware was compiled, not the flight time).",
  "system", "low", "[L-FMU/VERSION]Time    :commit:2017-11-15 22:11:15 /build:2017-11-16 10:29:44")
R("version.battery_sn", r"^Bat\s+SN\s*:\s*(?P<serial>.+?)\s*$", "Battery serial number(s): {serial}",
  "Serial number(s) of the battery or batteries installed, from the flight controller's start-up banner.",
  "battery", "normal", "[L-FMU/VERSION]Bat SN  :0DQAFBG0320081")
R("version.release", r"^FC - RELEASE FIRMWARE", "Release (production) firmware",
  "The flight controller reports that it is running a release (production) firmware build.",
  "system", "low", "[L-FMU/VERSION]FC - RELEASE FIRMWARE")
R("version.startup", r"^startup:(?P<seconds>[\d.]+)", "Flight-controller start-up ({seconds})",
  "Start-up banner line from older firmware; the value {seconds} is an unlabelled start-up time figure.",
  "system", "low", "<FLY>startup:3.116482")
R("version.board", r'^Board:"(?P<board>[^"]+)"', "Flight-controller board: {board}",
  "Hardware board identifier of the flight controller ('{board}').",
  "system", "low", '<FLY>Board:"ag405rtk"')
R("version.svn_banner", r"^(?:Firmware svn info:|Author\s*:|Revision\s*:|Modified\s*:)", "Firmware build information",
  "Firmware source-control banner line (author/revision/modified flag of the firmware build).",
  "system", "low", "Revision   :3337")

# ---- activation / registration ----------------------------------------------
R("activation.info", r"^(?:idx|send index):(?P<idx>\d+)\s+(?:send )?id:(?P<id>\d+) get info,\s*(?:sta|state):(?P<state>\d+),\s*date:(?P<date>[\d-]+),\s*time:(?P<time>[\d-]+),?\s*(?:proId:(?P<product_id>\S+))?",
  "Activation record: {date}",
  "The DJI activation module reported a stored activation record: state {state}, date {date}, time {time}{pid_note}. DJI aircraft must be activated through a DJI app/account before first flight; the log does not state the time zone.",
  "activation", "normal", "[L-ACTIVATION]idx:0 id:2 get info,sta:1,date:2020-3-5,time:20-54-10, proId:0UYKFBE0020",
  tags=_t("L-ACTIVATION"), post=_p_activation)
R("activation.get_version", r"^get version", "Activation module version query",
  "The activation module queried a version (start-up message).",
  "activation", "low", "[L-ACTIVATION]get version", tags=_t("L-ACTIVATION"))
R("sys.real_name", r"^set_real_name_info,data:\s*(?P<epoch>\d+),\s*flag:\s*(?P<flag>\w+)", "Real-name registration info set",
  "The flight controller stored real-name (owner registration) information: data value {epoch}{utc_note}, flag {flag}.",
  "activation", "normal", "[L-SYS]set_real_name_info,data: 1594384270, flag: ab, len:14 14", tags=_t("L-SYS"), post=_p_real_name)
R("sys.real_name_flag", r"^real_name_viechle_flag_init", "Real-name registration flag initialised",
  "Start-up message: the aircraft's real-name registration flag was initialised.",
  "activation", "low", "[L-DBG1]real_name_viechle_flag_init:aa")

# ---- app / link --------------------------------------------------------------
R("sys.app_lost", r"^app lost connection!", "Mobile app connection lost",
  "The flight controller lost its connection to the mobile app (the app link via the remote controller or Wi-Fi).",
  "radio_link", "high", "[L-SYS]app lost connection!", tags=_t("L-SYS"))
R("sys.app_connected", r"^app connected!", "Mobile app connected",
  "The flight controller established a connection with the mobile app.",
  "radio_link", "normal", "[L-SYS]app connected!", tags=_t("L-SYS"))
R("sys.conn_changed", r"^(?P<who>assistant|app) connect changed:last\((?P<last>\d+)\) != current\((?P<cur>\d+)\)",
  "{who_text}: {cur_text}",
  "The connection state of the {who_text} changed from {last} to {cur} (255 = not yet known, 0 = not connected, 1 = connected).",
  "system", "low", "assistant connect changed:last(255) != current(0)", post=_p_conn)
R("sys.math_lib", r"^MATH_LIB VERSION", "Math library version",
  "Start-up message: version of the flight controller's math library.",
  "system", "low", "[L-SYS]MATH_LIB VERSION: v1.0.0.1", tags=_t("L-SYS"))
R("sys.user_priv", r"^set user priv (?P<value>\w+)", "User privilege level set",
  "The flight controller set its user privilege value to {value} (internal access level).",
  "system", "low", "[L-SYS]set user priv 0xff", tags=_t("L-SYS"))

# ---- compass ----------------------------------------------------------------
R("compass.cali_location", r"^\[read_mag_cali_pt\]lat:\s*(?P<lat_rad>-?[\d.]+),\s*lon:\s*(?P<lon_rad>-?[\d.]+)",
  "Last compass-calibration location",
  "Stored location of the most recent compass calibration, logged in radians ({lat_rad}, {lon_rad}), i.e. about latitude {lat}, longitude {lon}. Five decimals in radians limit precision to roughly +/-30 m.",
  "compass", "high", "[L-COMPASS][read_mag_cali_pt]lat:0.81195, lon:0.11462", tags=_t("L-COMPASS"), post=_p_rad_ll)
R("compass.cali_date", r"^\[read_mag_cali_pt\]height:\s*(?P<height>-?[\d.]+),\s*date:\s*(?P<date>\d{8})",
  "Last compass calibration: {date}",
  "The most recent compass calibration is stored as dated {date}, at height value {height} (as logged).",
  "compass", "high", "[L-COMPASS][read_mag_cali_pt]height:404.0, date:20210322", tags=_t("L-COMPASS"), post=_p_date8)
R("compass.cali_invalid", r"^\[(?:cur|frame) cali data\].*invalid", "Compass calibration record not valid",
  "The compass module reports that one of its stored calibration records is invalid; at start-up it then loads calibration from another store (see neighbouring 'read app cali data' lines).",
  "compass", "low", "[L-COMPASS][cur cali data]fmu:0,user:0,current cali data invalid", tags=_t("L-COMPASS"))
R("compass.cali_load", r"^\[read (?:app cali|load) data\]", "Compass calibration data loaded",
  "Start-up message: the compass module loaded stored calibration values (scale/bias or record metadata).",
  "compass", "low", "[L-COMPASS][read app cali data]fmu:0,user:0,bias:[-315.975 -17.736 -14.316]", tags=_t("L-COMPASS"))
R("compass.config", r"^(?:enable:\(\d\),w_fstop|sync_topology_verify_config)", "Compass module configuration",
  "Start-up message describing the compass module configuration.",
  "compass", "low", "[L-COMPASS]enable:(1),w_fstop(10.000000),w_fs(150.000000),auto_cali(1)", tags=_t("L-COMPASS"))
R("compass.legacy_cali_init", r"^compass calibration init!", "Compass calibration module initialised",
  "Start-up message: compass calibration module initialised (this does not mean a calibration was performed).",
  "compass", "low", "compass calibration init!")
R("compass.chip", r"^(?:IST83\w+|ist83xx)\b.*", "Compass sensor chip message",
  "Message from the magnetometer (compass) chip driver (IST83xx series).",
  "compass", "low", "IST8303[2:0x18]:Init...")

# ---- fault detection ([L-FDI]) ------------------------------------------------
R("fdi.ns_fault", r"^NS\((?P<ns>\d+)\)\s+(?P<src>[A-Z]+)\((?P<idx>\d+)\):\s*fault (?P<onoff>on|off)\s*,\s*(?P<reason>\w+)",
  "Navigation fault {onoff_text}: {src_text} ({reason})",
  "Fault detection {onoff_text} a fault on {src_text} #{idx} feeding navigation system {ns}: {reason_text}.",
  "sensors", "normal", "[L-FDI]NS(0) RTK(0): fault on , conformity", tags=_t("L-FDI"), post=_p_fdi_ns)
R("fdi.sensor_fault", r"^(?P<src>[A-Z]+)\((?P<idx>\d+)\):\s*fault (?P<onoff>on|off)\s*,\s*(?P<reason>\w+)",
  "Sensor fault {onoff_text}: {src_text} ({reason})",
  "Fault detection {onoff_text} a fault on {src_text} #{idx}: {reason_text}. Raise/clear pairs at power-on are common while sensors start.",
  "sensors", "normal", "[L-FDI]ULTRASONIC(0): fault on , disconnect", tags=_t("L-FDI"), post=_p_fdi_ns)
R("fdi.rtk_link", r"^RTK\((?P<idx>\d+)\):\s*(?P<what>disconnect|reconnect)", "RTK module {what}",
  "Fault detection reports the RTK (high-precision GNSS) module #{idx} {what}ed.",
  "gps", "normal", "[L-FDI]RTK(0): disconnect", tags=_t("L-FDI"))
R("fdi.tilt_motor_on", r"^tilt when motor on = (?P<tilt>-?[\d.]+), static flag = (?P<static>\d)", "Tilt at motor start: {tilt}",
  "Fault detection recorded the aircraft's tilt when the motors started ({tilt}; unit not stated, value consistent with radians) and a static flag of {static}.",
  "imu", "normal", "[L-FDI]tilt when motor on = 0.021897, static flag = 1", tags=_t("L-FDI"))
R("fdi.imu_ok", r"^\[IMU\](?P<idx>\d+)\s+(?P<check>dir|mis cali|level 1&2 temp cali|base cali|cali ver) ok", "IMU {idx} check passed: {check}",
  "Start-up self-check: IMU {idx} passed the '{check}' check (orientation / misalignment / temperature / base calibration / calibration version).",
  "imu", "low", "[L-FDI][IMU]0 cali ver ok 4 4 9 9", tags=_t("L-FDI"))
R("fdi.imu_err_status", r"^\[IMU\]\s*gyro acc (?P<idx>\d+) err status (?P<code>\w+)", "IMU {idx} error status {code}",
  "Fault detection reports gyro/accelerometer {idx} error status code {code} (bit field; meaning not publicly documented).",
  "imu", "normal", "[L-FDI][IMU] gyro acc 0 err status 4040", tags=_t("L-FDI"))
R("fdi.ns_init_pass", r"^NS\((?P<ns>\d+)\):\s*ns init pass", "Navigation system initialised",
  "Navigation system {ns} (attitude/position estimator) passed its initialisation.",
  "navigation", "low", "[L-FDI]NS(0): ns init pass", tags=_t("L-FDI"))
R("fdi.ns_init_values", r"^NS\((?P<ns>\d+)\):\s*ns init (?P<which>[awm])=\s*(?P<x>-?[\d.]+)\s+(?P<y>-?[\d.]+)\s+(?P<z>-?[\d.]+)",
  "Navigation init sensor reading ({which})",
  "Sensor reading used to initialise navigation system {ns}: '{which}' (a = accelerometer, w = gyroscope, m = magnetometer) = {x}, {y}, {z}.",
  "navigation", "low", "[L-FDI]NS(0): ns init a= 0.038 -0.007 -1.008", tags=_t("L-FDI"))
R("fdi.ns_wait_static", r"^NS\((?P<ns>\d+)\):\s*init: wait_static", "Navigation waiting for aircraft to be still",
  "Navigation system {ns} is waiting for the aircraft to be stationary before initialising.",
  "navigation", "low", "[L-FDI]NS(0): init: wait_static", tags=_t("L-FDI"))
R("fdi.ctrl_event", r"^\[FDI CTRL\] event:\s*(?P<det>.*?)\s*turn on", "Fault detector enabled: {det}",
  "A flight-controller fault-detection (FDI) monitor named '{det}' was switched on. This start-up message records that the detector is active; it does NOT mean the condition (for example an impact or fly-away) occurred.",
  "safety", "low", "[L-FDI][FDI CTRL] event: impact turn on", post=_p_fdi_ctrl)
R("fdi.legacy_module", r"^\[FDI (?P<sensor>[A-Z]+)\[(?P<idx>\d+)\]\]\s*(?:event)?:?\s*(?P<what>.+)$", "Fault detection ({sensor}): {what}",
  "Older-firmware fault-detection start-up message for the {sensor} monitor #{idx}: '{what}'.",
  "safety", "low", "[FDI GPS[1]] event:turn on")
R("fdi.baro_cfg", r"^barometer_pool", "Barometer fault-detection configuration",
  "Start-up message: barometer filter configuration used by fault detection.",
  "sensors", "low", "[L-FDI]barometer_pool[0].priv.config.filter_config_flag:(541263214)", tags=_t("L-FDI"))
R("fdi.rtk_jump", r"^rtk:jump_hp=(?P<jump_h>-?[\d.]+), hv_mod=(?P<hv_mod>-?[\d.]+), jump_z=(?P<jump_z>-?[\d.]+)", "RTK position jump measured",
  "Fault detection measured a jump in the RTK position: horizontal {jump_h}, vertical {jump_z} (units not stated; consistent with metres).",
  "gps", "normal", "[L-FDI]rtk:jump_hp=44.778339, hv_mod=0.024783, jump_z=2.695053", tags=_t("L-FDI"))
R("fdi.no_ns", r"^fatal error: no ns with level < (?P<level>\d+)", "No usable navigation solution",
  "Fault detection reports that no navigation-system instance had a fault level below {level} (DJI's internal scale), i.e. no acceptable position/attitude estimate was available at that moment.",
  "navigation", "high", "[L-FDI]fatal error: no ns with level < 4 !!!", tags=_t("L-FDI"))
R("fdi.ns_switch_warning", r"^ns switch app warning!", "Navigation source switch warning",
  "Fault detection switched navigation source and raised a warning to the app.",
  "navigation", "high", "[L-FDI]ns switch app warning!", tags=_t("L-FDI"))
R("fdi.ns_req", r"^ns req:(?P<who>\w+),(?P<from>\d+)to(?P<to>\d+),reason:(?P<reason>\w+),result:(?P<result>\w+)",
  "Navigation system switch {from} -> {to} ({result})",
  "A request by '{who}' to switch the active navigation system from {from} to {to} (reason '{reason}') finished with result '{result}'.",
  "navigation", "normal", "[L-FDI]ns req:fdi,0to1,reason:normal,result:succeed", tags=_t("L-FDI"))
R("fdi.switch", r"^\[SWITCH\] req:(?P<req>\d+),(?P<from>\d+)->(?P<to>\d+),result:(?P<res>\d+)", "Navigation switch request {from} -> {to}",
  "Navigation/sensor switch request {req} from {from} to {to}, result code {res} (codes not publicly documented).",
  "navigation", "normal", "[L-FDI][SWITCH] req:2,0->1,result:6,serr:3,derr:11", tags=_t("L-FDI"))

# ---- navigation filter ([L-NS][AHRS]) -----------------------------------------
R("ns.baro_reset", r"^\[AHRS\]\s*reset baro just after (?P<ev>takeoff|land)", "Barometer reference reset after {ev}",
  "The navigation filter reset its barometric height reference just after {ev} (it re-zeroes height relative to the take-off/landing point).",
  "navigation", "normal", "[L-NS][AHRS] reset baro just after takeoff", tags=_t("L-NS"))
R("ns.init_attitude", r"^\[AHRS\]\s*Init atti RPY:\s*\(\s*(?P<roll>-?[\d.]+),\s*(?P<pitch>-?[\d.]+),\s*(?P<yaw>-?[\d.]+)\)", "Initial attitude: yaw {yaw} deg",
  "The navigation filter initialised the aircraft attitude: roll {roll}, pitch {pitch}, yaw (heading) {yaw} degrees.",
  "navigation", "normal", "[L-NS][AHRS]Init atti RPY: (2.16, 0.19,-105.93)", tags=_t("L-NS"))
R("ns.wmm", r"^\[AHRS\]\s*wmm (?P<which>dec|inc):\s*(?P<deg>-?[\d.]+)", "Magnetic {which} from World Magnetic Model: {deg} deg",
  "The navigation filter looked up the local magnetic field from the World Magnetic Model: {which} = {deg} degrees (dec = declination, inc = inclination). Because these depend on location, they are consistent only with certain regions.",
  "navigation", "low", "[L-NS][AHRS] wmm dec: -5.391582", tags=_t("L-NS"))
R("ns.yaw_aligned", r"^\[AHRS\]\s*yaw aligned (?P<value>\d)", "Heading aligned",
  "The navigation filter reports its heading (yaw) estimate aligned (flag {value}).",
  "navigation", "normal", "[L-NS][AHRS] yaw aligned 1", tags=_t("L-NS"))
R("ns.connect", r"^\[AHRS\]\s*connect (?P<sensor>\w+)\s*:\s*(?P<value>\d)", "Navigation input connected: {sensor}",
  "The navigation filter registered input '{sensor}' (gps, rtk, mag = compass, us = ultrasonic, vo = vision) with flag {value}.",
  "navigation", "low", "[L-NS][AHRS] connect gps \t :1", tags=_t("L-NS"))
R("ns.buffer", r"^\[AHRS\],?\s*(?:qg|pva) obv", "Navigation filter buffer/configuration status",
  "Internal navigation-filter observation buffer or sensor-timing status (diagnostic).",
  "navigation", "low", "[L-NS][AHRS],qg obv buffer size[218/270]", tags=_t("L-NS"))
R("ns.param", r"^\[AHRS\]\s*(?:para:\s*)?(?:[A-Za-z_][\w ]*?|R\w+|Init\s+qg\d|product|version(?: code)?|vo data type|VO DT changed|verti id)\s*:|^\[AHRS\]\s*height max obv num",
  "Navigation filter (AHRS) parameter",
  "Start-up configuration value of the navigation filter (attitude/heading reference system), e.g. sensor-fusion switches, gains or version.",
  "navigation", "low", "[L-NS][AHRS]  fs_gnss_hp        :  1", tags=_t("L-NS"))

# ---- IMU / sensors -----------------------------------------------------------
R("imu.gyro_acc_cfg", r"^(?:turn on temp ctrl|gyr_acc_config|fitler|fz:|\s*As=|vib detect|\[mark\]|\[shadow\]|\[\d+\]|imu_\d+ group)",
  "IMU (gyro/accelerometer) start-up configuration",
  "Start-up message from the gyroscope/accelerometer driver (filters, temperature control, calibration and registration).",
  "imu", "low", "[L-GYRO_ACC]turn on temp ctrl(0), cur_temp:0.0, reason:gyro_acc_tmp_ctl_init", tags=("L-GYRO_ACC",))
R("imu.heading_state", r"^\[heading\] state: (?P<state>\w+)", "Heading estimate state: {state}",
  "The IMU heading estimator reported state '{state}'.",
  "imu", "normal", "[L-IMU][heading] state: fixed", tags=_t("L-IMU"), post=_p_imu_heading)
R("imu.switch_level", r"^set imu switch level = (?P<level>\d+)", "IMU switch level set to {level}",
  "The IMU redundancy/switch level was set to {level}.",
  "imu", "normal", "[L-IMU]set imu switch level = 1", tags=_t("L-IMU"))
R("imu.legacy_cali", r"^(?:app )?temp cali|^mis cali|^imu_type:|^imu status:", "IMU calibration/status data",
  "Older-firmware start-up line with IMU temperature/misalignment calibration data or status.",
  "imu", "low", "temp cali (-11.000000,55.005882) aa fw:4 4")
R("sensor.baro_init", r"^\[baro\((?P<idx>\d+)\)\]\s*std_filtered_air_press init succeed: press:(?P<pressure>[\d.]+)",
  "Barometer initialised (air pressure {pressure})",
  "Barometer {idx} initialised with an air pressure of {pressure} (consistent with kPa).",
  "sensors", "low", "[L-SENSOR][baro(0)] std_filtered_air_press init succeed: press:97.3", tags=_t("L-SENSOR"))
R("sensor.startup", r"^(?:\[ASR\]|imu group|compass group|baro group|\[baro\(\d+\)\]|\s*hw_ver voltage)",
  "Sensor start-up / synchronisation message",
  "Start-up message from the sensor hub (sensor groups, barometer, sensor time synchronisation 'ASR').",
  "sensors", "low", "[L-SENSOR]imu group0 ok", tags=_t("L-SENSOR"))
R("fmu.device_registered", r"^success reg dev type\s*(?P<type>\d+)\((?P<name>[^)]*)\), id:(?P<id>\d+)", "Device registered: {name}",
  "Start-up message: the flight controller's device manager registered device type {type} ('{name}') as id {id}.",
  "system", "low", "[L-FMU/DM]success reg dev type  8(gyro_acc0), id:2, total num:2", tags=_t("L-FMU/DM"))
R("fmu.busy_device_changed", r"^Busy Device Changed\. Type:(?P<type_name>\w+), <ID:(?P<from_id>\d+) idx:(?P<from_idx>\d+)-->ID:(?P<to_id>\d+) idx:(?P<to_idx>\d+)>",
  "Active {type_name} device switched",
  "The device manager switched the active '{type_name}' device from id {from_id} (index {from_idx}) to id {to_id} (index {to_idx}), e.g. a switch to a redundant sensor.",
  "sensors", "normal", "[L-FMU/DM]Busy Device Changed. Type:imu1_ext_ns, <ID:9 idx:0-->ID:20 idx:1>, change ti", tags=_t("L-FMU/DM"))
R("hal.add_failed", r"^\[HAL\]\((?P<dev>[\w@]+)\):add to fmu failed ret = (?P<ret>\d+)", "Device registration failed: {dev}",
  "The hardware layer failed to register device '{dev}' with the flight controller (return code {ret}).",
  "system", "normal", "d2c<info-root>[HAL](tof0@0):add to fmu failed ret = 8")

# ---- GPS ---------------------------------------------------------------------
R("gps.signature_ok", r"^(?:<GPS INFO>)?GPS ecdsa_verify OK", "GPS module signature verified",
  "The cryptographic (ECDSA) signature check on the GPS module passed.",
  "gps", "low", "[L-GPS]<GPS INFO>GPS ecdsa_verify OK")
R("gps.frequency_error", r"^<GPS ERROR>.*frequency error", "GPS message-rate error",
  "The GPS driver reported that GPS messages did not arrive at the expected rate (missing/lost messages).",
  "gps", "normal", "[L-GPS]<GPS ERROR>[0][0x08] frequency error:lost msg", tags=_t("L-GPS"))
R("gps.init_no_ack", r"^<GPS ERROR>.*INIT\[(?P<step>\d+)\] ERROR \[(?P<msg>[A-Z0-9-]+)\] do not receive ack", "GPS configuration step not acknowledged ({msg})",
  "During initialisation the GPS receiver did not acknowledge configuration step {step} ({msg}).",
  "gps", "normal", "[L-GPS]<GPS ERROR>GPS[1][0] INIT[19] ERROR [MON-HW] do not receive ack", tags=_t("L-GPS"))
R("gps.agps_incomplete", r"^<AGPS ERROR>agps transmit is not finish", "Assisted-GPS data transfer incomplete",
  "Transfer of assisted-GPS (AGPS) data from the app to the aircraft was not finished or failed.",
  "gps", "normal", "[L-GPS]<AGPS ERROR>agps transmit is not finish or encounter error!", tags=_t("L-GPS"))
R("gps.agps_info", r"^<AGPS INFO>", "Assisted-GPS (AGPS) status",
  "Status message about assisted-GPS data exchanged with the app (AGPS speeds up the first satellite fix).",
  "gps", "low", "[L-GPS]<AGPS INFO>motor start,agps comm will stop...", tags=_t("L-GPS"))
R("gps.init_info", r"^(?:<GPS INFO>|drv_gps_init|gps init: )", "GPS receiver start-up message",
  "Start-up/initialisation message from the GPS receiver driver (baud rate, firmware status, receiver ID, acknowledgements).",
  "gps", "low", "[L-GPS]<GPS INFO>drv_gps_init")
R("gps.date", r"^read gps date:(?P<date>\d{8})", "Date read from GPS: {date}",
  "The flight controller read the current date ({date}) from the GPS receiver.",
  "system", "normal", "[L-DBG1]read gps date:20210419", post=_p_date8)

# ---- geo / fly-safe -----------------------------------------------------------
R("geo.height_status", r"^\[(?P<tick>[\d.]+)\]:height:(?P<height>\d+),pi:(?P<pi>\d+),pih:(?P<pih>\d+),pa:(?P<pa>\d+),pab:(?P<pab>\d+),isd:(?P<isd>\d+)",
  "Fly-safe (GEO) height status: {height}",
  "Periodic GEO/fly-safe status line. The 'height' field ({height}) is consistent with the applicable altitude limit in metres; the other flags (pi, pih, pa, pab, isd) are not publicly documented.",
  "safety", "low", "[L-GEO][70036736.0]:height:500,pi:1,pih:1,pa:0,pab:0,isd:1", tags=_t("L-GEO"))
R("geo.nfz_db", r"^\[TZ\]nfz_db num:(?P<count>\d+), no_valid_pos:(?P<no_valid_pos>\d+)", "No-fly-zone database entries: {count}",
  "The fly-safe module reports {count} no-fly-zone database entries in use (no-valid-position flag {no_valid_pos}).",
  "safety", "normal", "[L-GEO][TZ]nfz_db num:3, no_valid_pos:0", tags=_t("L-GEO"))
R("geo.app_db_entry", r"^\[print_app_db\]\[(?P<slot>\d+)\] id:(?P<id>\d+) \((?P<lat_e6>-?\d+), (?P<lon_e6>-?\d+)\) (?P<value>\d+)",
  "App fly-safe zone entry at {lat}, {lon}",
  "Fly-safe zone data supplied by the app: slot {slot}, id {id}, centre latitude {lat}, longitude {lon} (stored as degrees x 1,000,000); the trailing value {value} is unlabelled.",
  "safety", "normal", "[L-GEO][print_app_db][0] id:0 (46545278, 6616667) 2414", tags=_t("L-GEO"), post=_p_e6_ll)
R("geo.app_db_update", r"^\[(?:fmu_api_handler_update_app_db|update_nfz_app_db)\]", "Fly-safe database update from app",
  "Step in transferring fly-safe (no-fly-zone) data from the app to the aircraft.",
  "safety", "low", "[L-GEO][update_nfz_app_db]update app db[1]", tags=_t("L-GEO"))
R("geo.lost_app", r"^lost app, clear app_pos_info", "Fly-safe: app lost, app position cleared",
  "The fly-safe module lost contact with the app and cleared the app-supplied position information.",
  "safety", "normal", "[L-GEO]lost app, clear app_pos_info", tags=_t("L-GEO"))
R("geo.user_id", r"^\[update_user_id\]user id updated (?P<old>\d+)=>(?P<new>\d+)", "Fly-safe user ID changed",
  "The fly-safe module's stored user ID changed from {old} to {new}.",
  "safety", "normal", "[L-GEO][update_user_id]user id updated 4559911=>0", tags=_t("L-GEO"))
R("geo.flight_connect", r"^\[nfz_flight_monitor\]flight connect changed! (?P<from>\d+) -> (?P<to>\d+)", "Fly-safe monitor connection {from} -> {to}",
  "The fly-safe flight monitor's connection state changed from {from} to {to}.",
  "safety", "low", "[L-GEO][nfz_flight_monitor]flight connect changed! 0 -> 1", tags=_t("L-GEO"))
R("geo.relation", r"^\[record_flysafe_info\]drone relation (?P<from>\w+) => (?P<to>\w+)", "No-fly-zone relation: {from} -> {to}",
  "The fly-safe module's assessment of the aircraft's relation to no-fly zones changed from {from} ({from_text}) to {to} ({to_text}).",
  "safety", "normal", "[L-GEO][record_flysafe_info]drone relation OUT_NFZ => NFZ_UNKNOWN", tags=_t("L-GEO"), post=_p_geo_rel)
R("geo.internal", r"^\[(?:update_fc_db_handle|sa_drone_storage_process)\]", "Fly-safe database housekeeping",
  "Internal fly-safe database update or storage message.",
  "safety", "low", "[L-GEO][update_fc_db_handle]update fc db num:0", tags=_t("L-GEO"))
R("geo.country_pos", r"^\[country_limit_process\]cur pos:\(" + LAT + r" " + LON + r"\)", "Country flight-restriction check position",
  "The country flight-restriction check used the position latitude {lat}, longitude {lon} (rounded to 2 decimals in the log).",
  "safety", "normal", "[L-DBG2][country_limit_process]cur pos:(46.52 6.57) (0.81,0.11)")
R("geo.country_result", r"^\[country_limit_process\]region:(?P<region>\w+) strategy:(?P<strategy>\d+) result:(?P<result>\w+)", "Country flight-restriction result: {result}",
  "The country flight-restriction check for region {region} (strategy {strategy}) returned '{result}'.",
  "safety", "normal", "[L-DBG2][country_limit_process]region:REGION_ALL strategy:250 result:CAN_FLY")
R("geo.country_init", r"^\[country_limit_result_init\]", "Country flight-restriction check initialised",
  "Start-up message from the country flight-restriction module.",
  "safety", "low", "[L-DBG2][country_limit_result_init][INIT]strategy:250 can fly area num:1")
R("geo.airport_limit", r"^airport limit (?P<what>inited|data need update)", "Airport no-fly data {what}",
  "Older-firmware fly-safe message about the built-in airport no-fly-zone data ('{what}').",
  "safety", "normal", "airport limit inited[1]")
R("geo.whitelist", r"^\[whitelist\]", "Fly-safe unlock (whitelist) message",
  "Message from the fly-safe unlock/whitelist module.",
  "safety", "low", "[whitelist]invalid flash key")

# ---- missions / waypoints -------------------------------------------------------
R("mis.wp_upload", r"^\[WP_UPLOAD\](?P<index>\d+):\s*Llh\((?P<lat_rad>-?[\d.]+),(?P<lon_rad>-?[\d.]+),(?P<alt>-?[\d.]+)\)(?:\s*D:(?P<d>-?[\d.]+))?",
  "Waypoint {index} uploaded ({lat}, {lon}, {alt} m)",
  "A waypoint-mission point was uploaded to the aircraft: latitude/longitude logged in radians ({lat_rad}, {lon_rad}) = about {lat}, {lon}; altitude {alt} m (DJI waypoint altitudes are relative to the take-off point). The 'D' value {d} is unlabelled.",
  "navigation", "normal", "[L-MIS][WP_UPLOAD]0: Llh(0.81195163,0.11461895,23.30) D:0.21", tags=_t("L-MIS"), post=_p_rad_ll)
R("mis.sif_header", r"^\|#SiF%\|Date", "Mission position-record header",
  "Header of the tab-separated mission position records that follow (Date, Time, ID, Lat, Lon, Alt, Press, Yaw, RTKValid).",
  "navigation", "low", "[L-MIS]|#SiF%|Date\t\tTime\t\tID\tLat\t\t\t\tLon\t\t\tAlt\t\tPress\tYaw\t\tRTKValid", tags=_t("L-MIS"))
R("mis.sif_record", r"^\|#SiF%\|(?P<date>\d{8})\t+(?P<time>\d{6})\t+\s*(?P<id>\d+)\t+(?P<lat>-?[\d.]+)\t+(?P<lon>-?[\d.]+)\t+(?P<alt>-?[\d.]+)\t+(?P<press>-?[\d.]+)\t+(?P<yaw>-?[\d.]+)\t+(?P<rtk_valid>\d+)",
  "Mission position record ({lat}, {lon})",
  "Tab-separated record from the mission module (header: Date Time ID Lat Lon Alt Press Yaw RTKValid): {date} {time} (time zone not stated), position {lat}, {lon}, Alt {alt}, Press {press}, Yaw {yaw}, RTK valid {rtk_valid}. In the corpus about one record is written per mission camera shot.",
  "navigation", "normal", "[L-MIS]|#SiF%|20210419\t095119\t     0\t46.52140393\t\t6.56718225\t469.05\t360.17\t0.03\t1", tags=_t("L-MIS"), post=_p_sif)
R("mis.mission_received", r"^received mission length (?P<count>\d+) from app", "Waypoint mission received from app ({count} points)",
  "The aircraft received a waypoint mission of {count} waypoints from the app.",
  "navigation", "normal", "[L-MIS]received mission length 5 from app", tags=_t("L-MIS"))
R("mis.wp_info_main", r"^\[WP INFO\]:\s*len:(?P<count>\d+), vel_range:(?P<max_speed>[\d.]+), idle_vel:(?P<speed>[\d.]+), action_on_finish:(?P<finish_action>\d+)",
  "Waypoint mission settings: {count} points, speed {speed}",
  "Waypoint mission parameters: {count} waypoints, cruise speed {speed} (maximum {max_speed}; consistent with m/s), finish-action code {finish_action}.",
  "navigation", "normal", "[L-MIS][WP INFO]: len:5, vel_range:14.000000, idle_vel:13.000000, action_on_finish:1", tags=_t("L-MIS"))
R("mis.go_stop", r"^WP Mission: received go/stop (?P<value>\d+) from app", "Waypoint mission start/stop command from app",
  "The aircraft received a waypoint-mission go/stop command from the app (value {value}).",
  "navigation", "high", "[L-MIS]WP Mission: received go/stop 0 from app", tags=_t("L-MIS"))
R("mis.route_end", r"^\[WP\]: stop mode\. end of trace, quit", "Waypoint mission route completed",
  "The waypoint mission reached the end of its route and the mission logic quit.",
  "navigation", "normal", "[L-MIS][WP]: stop mode. end of trace, quit.", tags=_t("L-MIS"))
R("mis.p2p_timeout", r"^\{?\[WP_FLY_p2p\] p2p fly moving time out", "Waypoint leg timed out",
  "The point-to-point flight between waypoints exceeded its time limit.",
  "navigation", "normal", "[L-MIS]{[WP_FLY_p2p] p2p fly moving time out!", tags=_t("L-MIS"))
R("mis.simple_shot", r"^\[WP\] send simple shot", "Waypoint action: take photo",
  "The waypoint mission commanded the camera to take a single photo.",
  "camera_gimbal", "normal", "[L-MIS][WP] send simple shot", tags=_t("L-MIS"))
R("mis.rtk_switch", r"^\s*RTK is (?P<state>ok|not ready), switch to (?P<target>\w+)", "Mission positioning switched to {target}",
  "The mission module switched its positioning source to {target} because RTK is {state}.",
  "gps", "normal", "[L-MIS] RTK is ok, switch to rtk", tags=_t("L-MIS"))
R("mis.gps2rtk", r"^gps2rtk offset\s+(?P<x>-?[\d.]+)\s+(?P<y>-?[\d.]+)\s+(?P<z>-?[\d.]+)", "GPS-to-RTK position offset",
  "Offset between the GPS and RTK positions: {x}, {y}, {z} (units not stated; consistent with metres).",
  "gps", "low", "[L-MIS]gps2rtk offset -10.04  42.95  -10.09", tags=_t("L-MIS"))
R("mis.wp_detail", r"^(?:\[WP\]|\[WP_INFO\]|\[WP INFO\]|\[SDK\]|ack |Ready to handle action|I have done one|finished all actions|finish gimbal pitch|received on/off|input mission validity|WP Mission: ack|no ready waypoint mission|req_watch_mode)",
  "Waypoint-mission processing step",
  "Internal step of waypoint-mission execution (upload acknowledgements, per-waypoint actions, gimbal/camera handling, mission settings).",
  "navigation", "low", "[L-MIS]I have done one action [20.20]", tags=_t("L-MIS"))
R("osd.mission_finished", r"^Mission finished, now I tell app", "Mission finished (reported to app)",
  "The aircraft reported to the app that the mission finished.",
  "navigation", "normal", "[L-OSD]Mission finished, now I tell app:  3  size is 6", tags=_t("L-OSD"))
R("osd.wp_finish_push", r"^push wp finish info to app", "Mission-finish info sent to app",
  "The waypoint-mission finish information was pushed to the app.",
  "navigation", "low", "[L-OSD]push wp finish info to app success!", tags=_t("L-OSD"))
R("nmis.gohome_failed", r"^GoHome Check Failed: (?P<why>.+?)!?\s*$", "Return-to-home request rejected: {why}",
  "A return-to-home request was rejected by the flight-task manager because: {why}.",
  "flight", "high", "[L-N_MIS]GoHome Check Failed: motor off!", tags=_t("L-N_MIS"))
R("nmis.abort_all", r"^req abort all for (?P<why>.+?)\s*$", "Automatic tasks aborted: {why}",
  "The flight-task manager aborted all automatic tasks because: {why}.",
  "flight", "normal", "[L-N_MIS]req abort all for exit landing for ground not smooth", tags=_t("L-N_MIS"), post=_p_abort)
R("nmis.internal", r"^(?:send cur mission exit data|Switch new mis|Exit mis|Init plan|Fail to check mis|Check fail of plan|Exit plan|req abort plan for|reset_smart_landing_status)",
  "Flight-task manager internal step",
  "Internal bookkeeping of the flight-task manager (switching between numbered internal tasks/plans; the numbers are internal IDs).",
  "flight", "low", "[L-N_MIS]Switch new mis 4 req 2 from mis 3 req 2", tags=_t("L-N_MIS"))

# ---- camera -------------------------------------------------------------------
R("camera.shot_position", r"^cam success\((?P<h>\d+) (?P<m>\d+) (?P<s>\d+) (?P<n>\d+)\)\((?P<lon>-?[\d.]+),(?P<lat>-?[\d.]+),(?P<alt>-?[\d.]+)\)",
  "Photo taken at {lat}, {lon}",
  "The camera reported a successful capture. Position logged with it: latitude {lat}, longitude {lon} (the log writes longitude first), altitude {alt} (datum not stated). The first group ({h} {m} {s} {n}) reads as hours, minutes, seconds and an unlabelled counter.",
  "camera_gimbal", "normal", "[L-CAMERA]cam success(9 36 30 777015902)(6.567274,46.521394,467.845540) (0.009494,0.01", tags=_t("L-CAMERA"))
R("camera.set_date", r"^cmd_handler_setting_date (?P<date>[\d-]+),\s*(?P<time>[\d:]+)", "Camera clock set to {date} {time}",
  "The flight controller sent date/time {date} {time} to the camera (camera clock synchronisation; time zone not stated).",
  "camera_gimbal", "normal", "[L-CAMERA]cmd_handler_setting_date 2023-09-22, 15:33:21", tags=_t("L-CAMERA"))
R("camera.set_time_failed", r"^setting time\s+failed ret:(?P<ret>-?\d+)", "Camera clock setting failed",
  "Setting the camera's clock failed (return code {ret}).",
  "camera_gimbal", "normal", "[L-CAMERA]setting time  failed ret:-17", tags=_t("L-CAMERA"))
R("camera.shot_ok", r"^SUCCESSED SIMPLE_SHOT", "Photo captured (single shot)",
  "The camera confirmed a single-photo capture.",
  "camera_gimbal", "normal", "[L-CAMERA]SUCCESSED SIMPLE_SHOT", tags=_t("L-CAMERA"))
R("camera.shot_cmd", r"^Send camera shot", "Photo command sent to camera",
  "The flight controller sent a take-photo command to the camera.",
  "camera_gimbal", "low", "[L-CAMERA]Send camera shot, timer 0.005000", tags=_t("L-CAMERA"))
R("camera.handshake", r"^(?:Receive ack|Receive reply|<\d+>|SIMPLE_SHOT, wait|recv ack|recv shot ack|WP session|<SET_MODE>)",
  "Camera command handshake",
  "Acknowledgement/handshake step in flight-controller-to-camera communication (mode changes, shot acknowledgements, sessions).",
  "camera_gimbal", "low", "[L-CAMERA]Receive ack result 0", tags=_t("L-CAMERA"))
R("gimbal.disconnect", r"^The (?P<which>\w+) gimbal (?P<idx>\d+) disconnect", "Gimbal disconnected",
  "The flight controller reports the {which} gimbal (#{idx}) disconnected.",
  "camera_gimbal", "normal", "[L-GIMBAL]The 1st gimbal 0 disconnect", tags=_t("L-GIMBAL"))

# ---- LEDs ----------------------------------------------------------------------
R("led.action", r"^action changed\.\s*(?P<desc>.*?)\s*$", "LED status changed: {desc}",
  "The aircraft's status LEDs changed pattern.", "system", "low",
  "[L-FMU/LED]action changed. set home:(0)", tags=_t("L-FMU/LED"), post=_p_led)
R("led.legacy", r"^\[LED\] changed:\s*(?P<desc>.+?)\s*$", "LED status changed: {desc}",
  "The aircraft's status LEDs changed pattern.", "system", "low",
  "[LED] changed: battery warning!", post=_p_led)
R("led.config", r"^(?:type:\d+, normal flash action|c\d+:\d+,\d+;|set_open_led_action|fill_open_led_action|Reg from 0x|change status in pm handler|ce not sync changed|not_sync_start_time)",
  "LED pattern configuration",
  "Internal configuration of the status-LED patterns (colour/timing tables and registrations).",
  "system", "low", "[L-FMU/LED]c0:0,15;c1:0,3;c2:0,13;c3:2,3;c4:0,10;c5:0,3;c6:0,10;c7:0,3", tags=_t("L-FMU/LED"))

# ---- parameter manager ([L-CFG], PM:) -------------------------------------------
R("cfg.rc_lost_action", r"^(?P<op>get|set) rc lost act\.\s*(?:cmd:)?\s*(?P<act>\d+)", "RC signal-lost action {op_text}: {act_text}",
  "The failsafe action for remote-controller signal loss was {op_text} code {act} ({act_text}; numbering as used by the DJI Mobile SDK: 0 hover, 1 land, 2 return-to-home).",
  "safety", "normal", "[L-CFG]get rc lost act. 2", tags=_t("L-CFG"), post=_p_rc_lost)
R("cfg.param_changed", r'^PM:\[(?P<source>[^\]]+)\]\s*"(?P<name>[^"]+)" to (?P<raw_value>\S+)', "Parameter changed: {name} = {raw_value}",
  "The parameter manager changed parameter '{name}'{friendly_note} to {raw_value} (requested by '{source}').",
  "configuration", "low", '[L-CFG]PM:[Mobile APP] "g_status.farm.app_stop_motor_start" to 0', post=_p_param_change)
R("cfg.param_out_of_range", r"^PM:\[(?P<source>[^\]]+)\]\s*param is out of range,(?P<name>\S+)", "Parameter value rejected (out of range): {name}",
  "The parameter manager rejected a value for '{name}' because it was outside the allowed range (requested by '{source}').",
  "configuration", "low", "[L-CFG]PM:[mission_info_record_to_user_log]param is out of range,farm_total_fly_miles")
R("cfg.pm_internal", r"^(?:PM:cannot find the index by hash value|PM:>>>>|PM:add root work|can't find var in opentable|imu_cali_state_notify)",
  "Parameter-manager internal message",
  "Internal parameter-manager message (lookup misses, table statistics). Not related to a pilot action.",
  "configuration", "low", "[L-CFG]PM:cannot find the index by hash value:4146467217")
R("cfg.table_stats", r"^pbd cnt:\d+,item cnt:\d+,cmd cnt:\d+", "Parameter table statistics",
  "Size statistics of the flight controller's parameter table (start-up message).",
  "configuration", "low", "pbd cnt:76,item cnt:1294,cmd cnt:108!")

# ---- API / SDK / commands -----------------------------------------------------------
R("api.force_landing", r"^basic_cmd_force_landing(?P<variant>_avoid_ground)?$", "Forced landing command",
  "The SDK/app interface executed a forced-landing command{variant}.",
  "flight", "high", "[L-API]basic_cmd_force_landing", tags=_t("L-API"),
  post=lambda v, d: d.__setitem__("variant", " (variant that bypasses landing protection / ground checks)" if v.get("variant") else "") or {})
R("api.exit_landing", r"^basic_cmd_exit_landing", "Landing cancelled by command",
  "The SDK/app interface executed a command to exit (cancel) landing.",
  "flight", "high", "[L-API]basic_cmd_exit_landing", tags=_t("L-API"))
R("api.landing_checks", r"^(?:force_landing_check|force_landing_avoid_ground_check|exit_landing_check)", "Landing command pre-check",
  "The SDK/app interface checked whether a forced-landing or exit-landing command may be executed.",
  "flight", "normal", "[L-API]force_landing_check", tags=_t("L-API"))
R("api.multi_drone", r"^\[MUTI_DRONE\]", "Multi-aircraft control information",
  "Information about multi-aircraft control (one controller/app managing several aircraft).",
  "system", "low", "[L-API][MUTI_DRONE] receive muti_drone info: not_in_ctrl_flag:0 id:21", tags=_t("L-API"))
R("api.cmd_index", r"^(?:get_id_index_by_basic_cmd|exec_fc_basic_function)", "Basic command dispatch",
  "Internal dispatch of a basic flight-controller command (command index lookup/execution).",
  "system", "low", "[L-API]get_id_index_by_basic_cmd: cmd 31 index 18", tags=_t("L-API", "L-CMD"))
R("sdk.startup", r"^\[USER\]", "SDK / user-data start-up message",
  "Start-up message from the onboard SDK protocol or user-statistics module.",
  "system", "low", "[L-USER][USER]Read user config!", tags=("L-USER",))

# ---- ESC / PWM / misc hardware ---------------------------------------------------------
R("esc.link_up", r"^ESC(?P<esc>\d+) link up", "ESC {esc} link established",
  "Communication with electronic speed controller (ESC) {esc} was established.",
  "motors", "low", "[L-ESC]ESC0 link up")
R("esc.protocol_error", r"^ESC(?P<esc>\d+) protocol error - (?P<code>0x\w+)", "ESC {esc} protocol error {code}",
  "The flight controller reported a communication protocol error with ESC {esc} (code {code}).",
  "motors", "normal", "[L-ESC]ESC0 protocol error - 0x12!", tags=_t("L-ESC"))
R("esc.info", r"^(?:esc alive info|escm ppm test|ESC\d+ version:|Loader\s*=|Firmware\s*=)", "ESC start-up information",
  "Start-up information about the electronic speed controllers (alive mask, self-test, versions).",
  "motors", "low", "[L-ESC]esc alive info = 0x0")
R("esc.status_changed", r"^\[esc_is_(?P<what>stall|empty)\] status changed: last\((?P<last>0x\w+)\) != current\((?P<cur>0x\w+)\)", "ESC {what} status changed",
  "The ESC '{what}' status bit mask changed from {last} to {cur} (0x00000000 = no ESC flagged).",
  "motors", "normal", "[esc_is_stall] status changed: last(0xffffffff) != current(0x00000000)")
R("pwm.actuator_mode", r"^set actuator mode:(?P<mode>\w+)", "Actuator mode: {mode}",
  "The motor output driver changed mode to {mode}.",
  "motors", "normal", "[L-PWM]set actuator mode:CTRL", tags=_t("L-PWM"), post=_p_pwm)
R("pwm.cfg", r"^(?:actuator_cfg_changed|f_chl_cfg changed)", "Motor-output configuration loaded",
  "Start-up message: motor/actuator output configuration changed (loaded).",
  "motors", "low", "[L-PWM]actuator_cfg_changed![ACTUATOR_0]", tags=_t("L-PWM"))
R("ioc.course_lock", r"^(?:Set CL\.|\[IOC\.CL\s*\])\s*(?P<deg>-?[\d.]+) Degree", "Course-lock heading recorded: {deg} deg",
  "Intelligent Orientation Control (IOC) recorded the course-lock reference heading of {deg} degrees (the forward direction used by Course Lock mode).",
  "navigation", "normal", "[L-IOC]Set CL. -171.7 Degree")
R("ioc.reset", r"^old\.IOC reset", "Intelligent Orientation Control reset",
  "Older-firmware message: the Intelligent Orientation Control setting was reset.",
  "navigation", "low", "old.IOC reset intelligence_orientation_enabled")
R("recorder.timezone", r"^set timezone:(?P<tz>-?\d+), result:(?P<result>\d+)", "Recorder time zone set (value {tz})",
  "The flight-data recorder's time-zone value was set to {tz} (encoding not documented in the log), result code {result}.",
  "storage", "normal", "[L-RECORDER]set timezone:80, result:0", tags=_t("L-RECORDER"))
R("radar.filter", r"^Set radar filter", "Radar filter setting",
  "Setting of the obstacle/terrain radar's filter.",
  "sensors", "low", "[L-RADAR]Set radar filter wide to 20.00", tags=_t("L-RADAR"))
R("fan.status", r"^fan", "Cooling fan status",
  "Start-up/status message from the cooling fan controller.",
  "system", "low", "[L-FAN]fan cfg synced", tags=_t("L-FAN"))
R("ctrl.init", r"^(?:horiz vel complement filter|navi_data init|Wind estimate|init_DFT)", "Flight-control start-up configuration",
  "Start-up message from the flight-control (attitude/velocity controller) module.",
  "flight", "low", "[L-CTRL]Wind estimate using method V2:acc to speed!!", tags=_t("L-CTRL"))
R("fmu.inventory", r"^(?:\[DB\]FATAL ERROR haven't inited DB|(?:v0 |open |sdk )?api num:|db data num|db_size|debug module num|sa module num|code check num|sys dev num|dm table size|\w* ?api num)",
  "Flight-controller software inventory",
  "Start-up message listing internal software component counts. A '[DB]FATAL ERROR haven't inited DB' line here is start-up ordering noise seen in most corpus logs.",
  "system", "low", "[L-FMU]api num: 193", tags=_t("L-FMU"))
R("sim.param", r"^read parameter successfully", "Simulator parameters read",
  "Start-up message: the built-in simulator module read its parameters (does not mean the simulator was used).",
  "system", "low", "[L-SIM]read parameter successfully!", tags=_t("L-SIM"))
R("sdk.protocol", r"^(?:\[USER\]\s*)?(?:Init_dji_api_protocol|Baud_rate|-+start sdk protocol)", "Onboard SDK protocol start-up",
  "Start-up message of the onboard SDK serial protocol.",
  "system", "low", "[L-SDK][USER]----------start sdk protocol ---------", tags=_t("L-SDK"))
R("dev.internal", r"^(?:call comm_recorder_cfg|add blockID|\d+-cfg finished|\d+-centerboard|\d+-rtk set fix status|\d+-record \d+-\d+)",
  "Device / recorder configuration message",
  "Internal device-manager message (data-recorder configuration, centre-board version/reset, RTK fix status).",
  "system", "low", "[L-DEV]call comm_recorder_cfg sender:11 id 61761 res:0x06 len:230 item:57 4", tags=_t("L-DEV"))
R("gear.state", r"^Gear \[(?P<value>-?\d+)\] (?P<state>\w+)", "Landing-gear message: {state}",
  "Landing-gear (retractable undercarriage) message: '{state}' with value {value}.",
  "flight", "normal", "Gear [-60] Show")
R("rtk.station", r"^\[rtk\]receive MC set motive station", "RTK: flight controller set mobile-station mode",
  "The RTK module received the flight controller's 'set motive (mobile) station' command.",
  "gps", "low", "[rtk]receive MC set motive station")

# ---- OS / boot / storage -----------------------------------------------------------------
R("boot.reset_cause", r"^reset by (?P<cause>power|soft|watchdog)\b", "Flight-controller reset: {cause}",
  "The flight controller recorded the cause of its last reset: {cause}.",
  "system", "normal", "{root}INFO:reset by power.", post=_p_reset)
R("boot.watchdog_reset", r"^\[panic\]:!+reset by watchdog!+", "Flight controller reset by watchdog",
  "The crash handler recorded that the last reset was caused by the watchdog timer: the software stopped responding and the processor restarted automatically.",
  "system", "high", "[panic]:!!!!!!!!!!!!!!!!!reset by watchdog!!!!!!!!!!!!!!!!!")
R("boot.crash_record_empty", r"^(?:\[panic\]|\[hardfault\]):-+\s*(?P<what>.+?)\s+is null", "No stored crash record ({what})",
  "At start-up the flight controller checks its crash-dump memory; '{what} is null' means no saved crash/fault data of that kind was found.",
  "system", "low", "{root}INFO:[panic]:-----fault info is null: addr(0x2045e800),flag(0xf4a9134f)-----")
R("boot.crash_check", r"^(?:\[panic\]|\[hardfault\]):\*+\s*check", "Crash-record check",
  "Start-up step: the flight controller checks its crash-dump memory for a saved fault trace.",
  "system", "low", "{root}INFO:[panic]:******************check last trace ******************")
R("boot.watchdog_cfg", r"^(?:iwdg_set_time_out|wdg_set_swdg_time_out|watchdog enable|iwdg_set_max_timeout|iwdg_set_swdg_time_out)",
  "Watchdog timer configured",
  "Start-up message: the watchdog timer (which restarts the processor if the software hangs) was configured/enabled.",
  "system", "low", "watchdog enable.")
R("boot.link_init", r"^(?:link_init <\d+> (?P<link>\w+)|lib_route_init ok|link_host_init ok|__comm_cfg_init\((?P<link2>\w+)\)|comm_rc_init\()",
  "Communication link initialised",
  "Start-up message: an internal communication link/route of the flight controller was initialised.",
  "system", "low", "{root}INFO:link_init <4> link_ofdm, ret(0x0)")
R("boot.ofdm_failed", r"^\[link_ofdm\] setup entry failed", "Radio (OFDM) link setup failed",
  "Setting up the OFDM radio-link entry failed (OFDM is the video/control radio link to the remote controller).",
  "radio_link", "normal", "{root}INFO:[link_ofdm] setup entry failed")
R("boot.ofdm_tx_err", r"^\[Err\] OFDM_TX State:(?P<state>0x\w+)", "Radio (OFDM) transmitter error state {state}",
  "The OFDM radio transmitter reported error state {state}.",
  "radio_link", "normal", "[Err] OFDM_TX State:0x0000000c")
R("boot.init_tasks", r"^\[Init\]", "Flight-controller task initialisation",
  "Start-up message: flight-controller task scheduling initialised (task rates).",
  "system", "low", "[Init]HF task : 400Hz @ mode1")
R("boot.misc", r"^(?:i2c\d+ core recovery|mp_state:|dyn_map_mem_init|battery collect param handler|recorder \d+ configuare done|samv7_backup_sram_alloc|uuid_index\.flag|cali_switch_config|counter: \d+ timeout_counter)",
  "Start-up / housekeeping message",
  "Low-level start-up or housekeeping message from the flight-controller operating system (bus recovery, memory, recorder set-up).",
  "system", "low", "{root}INFO:i2c1 core recovery")
R("boot.clock_set", r"^(?:calender_setup_time\s+|calender_time set type\((?P<type>\d+)\) to )\[(?P<date>[\d-]+)\]\s*(?P<time>[\d:]+)", "Flight-controller clock set: {date} {time}",
  "The flight controller set its calendar clock to {date} {time} (time zone not stated). Useful when correlating log times with real time.",
  "system", "normal", "d77<fly-ctrl>calender_setup_time  [2021-4-19]10:44:13. last_type(7),updates(0)")
R("boot.time_set_by", r"^set time by (?P<source>\d+), (?P<date>[\d-]+)\s+(?P<time>[\d:]+)", "Flight-controller clock set: {date} {time}",
  "The flight controller set its clock to {date} {time} from time source {source} (source codes not documented; time zone not stated).",
  "system", "normal", "[L-DBG1]set time by 8, 2020-7-10  20:31:10")
R("boot.time_check_err", r"^time_check type\((?P<type>\d+)\) err! (?P<when>.+)$", "Clock consistency check error",
  "The flight controller's clock consistency check (type {type}) flagged the time {when} (note the seconds field 60).",
  "system", "low", "<FLY>time_check type(4) err! 2017-7-24 8:11:60")
R("storage.eeprom_load", r"^eeprom load\s+(?P<block>\d+)\s+(?P<offset>\d+)\s+(?P<size>\d+)\s+(?P<alloc>\d+)", "Settings block loaded from EEPROM",
  "Start-up message: a block of stored settings was loaded from the flight controller's non-volatile memory (block {block}).",
  "storage", "low", "0 eeprom load 12  448   52   56")
R("storage.eeprom_write", r"^Eeprom write offset:(?P<offset>[0-9a-fA-F]+)", "Settings written to EEPROM",
  "Stored settings were written to the flight controller's non-volatile memory (offset 0x{offset}).",
  "storage", "low", "Eeprom write offset:3b8")
R("storage.baseinfo_seq", r"^baseInfo seq error:this\[(?P<this>\d+)\]last\[(?P<last>\d+)\]", "Base-info record sequence gap",
  "Internal logging message: a base-information record sequence number jumped (this {this}, last {last}).",
  "storage", "low", "0 baseInfo seq error:this[10]last[0]")
R("storage.cfg_write", r"^cfg write index (?P<index>\d+)", "Configuration record written",
  "The system log recorded a configuration write (index {index}).",
  "storage", "low", "cfg write index 42")
R("storage.write_timeout", r"write time_out", "Storage write timeout",
  "The storage driver reported a write timeout (data may be missing from the log around this time).",
  "storage", "normal", "Info:2>write time_out : dma ,trace=0x0")
R("module.listed", r"^Op\.module\s+(?P<module>\S+)", "Log module listed: [{module}]",
  "The log header lists firmware module [{module}]{module_desc}, which may write lines to this log.",
  "system", "low", "Op.module\tL-GPS", post=_p_tag_module)
R("module.string_def", r"^Op\.\[string\]", "Log format definition",
  "Log header line defining the format of text records.",
  "system", "low", "Op.[string]\ntype\t32769\nname\tDEBUG_MODULE")
R("senddata.msg", r"^\[(?:INFO|ERROR)\]", "Telemetry push configuration message",
  "Message from the module that pushes telemetry data packages ('bags') to the app/SDK.",
  "radio_link", "low", "[L-SEND DATA][INFO] [Reset] Dev_id: 0", tags=("L-SEND DATA",))

# ---------------------------------------------------------------------------
# Configuration parameters (sys_cfg dumps)
# ---------------------------------------------------------------------------
# name -> (friendly name, unit, description, value meanings)
PARAMS: Dict[str, Tuple[str, str, str, Optional[Dict[int, str]]]] = {}


def _P(names: Iterable[str], friendly: str, unit: str, desc: str, enum: Optional[Dict[int, str]] = None) -> None:
    for n in names:
        PARAMS[n.lower()] = (friendly, unit, desc, enum)


_ONOFF = {0: "off", 1: "on"}
_BAT_ACTION = {0: "warning only", 1: "return-to-home", 2: "land"}
_P(["go_home_height", "g_config.go_home.fixed_go_home_altitude"], "Return-to-home altitude", "m",
   "Height the aircraft climbs to before flying back to the home point on return-to-home.")
_P(["g_config.flying_limit.max_height", "limit_height_rel_cfg"], "Maximum flight altitude limit", "m",
   "Maximum height above the take-off point the pilot allows the aircraft to fly.")
_P(["g_config.flying_limit.max_radius"], "Maximum distance limit", "m",
   "Maximum horizontal distance from the home point when the distance limit is enabled.")
_P(["g_config.flying_limit.height_limit_enabled", "g_config.advanced_function.height_limit_enabled"],
   "Height limit enabled", "", "Whether the altitude limit is enforced.", _ONOFF)
_P(["g_config.flying_limit.radius_limit_enabled", "g_config.advanced_function.radius_limit_enabled"],
   "Distance limit enabled", "", "Whether the maximum-distance limit is enforced.", _ONOFF)
_P(["g_config.advanced_function.fail_safe_protection_enabled"], "Failsafe protection enabled", "",
   "Whether failsafe protection (action on remote-controller signal loss) is enabled.", _ONOFF)
_P(["g_config.advanced_function.one_key_go_home_enabled"], "One-key return-to-home enabled", "",
   "Whether one-key (button) return-to-home is enabled.", _ONOFF)
_P(["g_config.advanced_function.avoid_ground_enabled", "g_config.flying_limit.avoid_ground_enabled"],
   "Ground-avoidance enabled", "", "Whether ground avoidance is enabled.", _ONOFF)
_P(["g_config.flying_limit.user_avoid_ground_enable"], "Landing protection / ground avoidance (user)", "",
   "User setting for ground avoidance.", _ONOFF)
_P(["rc_lost_action", "g_config.fail_safe.protect_action"], "Action on remote-controller signal loss", "",
   "What the aircraft does when the remote-controller signal is lost (DJI Mobile SDK numbering).", RC_LOST_ACTIONS)
_P(["rc_lost_action_time"], "Delay before signal-loss action", "s",
   "Time the remote-controller signal must be lost before the failsafe action starts.")
_P(["rc_lost_enable"], "Signal-loss failsafe enabled", "", "Whether the signal-loss failsafe is enabled.", _ONOFF)
_P(["novice_func_enabled", "g_config.novice_cfg.novice_func_enabled"], "Beginner mode enabled", "",
   "Whether beginner (novice) mode, which restricts height, distance and speed, is enabled.", _ONOFF)
_P(["novice_max_height", "g_config.novice_cfg.max_height"], "Beginner-mode height limit", "m",
   "Maximum height while beginner mode is on.")
_P(["novice_max_radius", "g_config.novice_cfg.max_radius"], "Beginner-mode distance limit", "m",
   "Maximum distance while beginner mode is on.")
_P(["dji_bat_level_1", "g_config.voltage2.level_1_voltage"], "Low-battery warning threshold", "%",
   "Battery percentage at which the low-battery (level 1) warning is given.")
_P(["dji_bat_level_2", "g_config.voltage2.level_2_voltage"], "Critical-battery warning threshold", "%",
   "Battery percentage at which the critical (level 2) warning is given.")
_P(["bat_cap_v1_prot_type", "g_config.voltage2.level_1_function"], "Action at low-battery warning", "",
   "What the aircraft does at the low-battery (level 1) threshold (codes as commonly documented for DJI flight controllers).", _BAT_ACTION)
_P(["bat_cap_v2_prot_type", "g_config.voltage2.level_2_function"], "Action at critical-battery warning", "",
   "What the aircraft does at the critical (level 2) threshold (codes as commonly documented).", _BAT_ACTION)
_P(["raw_bat_level_1", "g_config.voltage.level_1_protect"], "Low-voltage warning threshold", "mV",
   "Voltage at which the low-voltage (level 1) protection triggers.")
_P(["raw_bat_level_2", "g_config.voltage.level_2_protect"], "Critical-voltage threshold", "mV",
   "Voltage at which the critical-voltage (level 2) protection triggers.")
_P(["bat_level_1_action", "g_config.voltage.level_1_protect_type"], "Action at low-voltage warning", "",
   "What the aircraft does at the low-voltage threshold (codes as commonly documented).", _BAT_ACTION)
_P(["bat_level_2_action", "g_config.voltage.level_2_protect_type"], "Action at critical-voltage warning", "",
   "What the aircraft does at the critical-voltage threshold (codes as commonly documented).", _BAT_ACTION)
_P(["ensmartbatgh", "g_config.voltage2.user_set_smart_bat"], "Smart return-to-home (battery) setting", "",
   "User setting for Smart (battery-based) return-to-home.")
_P(["level1_smart_battert_gohome", "g_config.voltage2.level1_smart_battert_gohome"], "Smart-RTH battery reserve", "%",
   "Battery reserve used by the smart low-battery return-to-home logic.")
_P(["level2_smart_battert_land", "g_config.voltage2.level2_smart_battert_land"], "Smart-landing battery reserve", "%",
   "Battery reserve used by the smart low-battery landing logic.")
_P(["enable_multiple_mode", "g_config.control.multi_control_mode_enable"], "Multiple flight modes enabled", "",
   "Whether 'multiple flight modes' is enabled, which allows ATTI/Sport positions of the flight-mode switch.", _ONOFF)
_P(["fswitch_selection", "g_config.control.control_mode[0]"], "Flight-mode switch position 1 mode", "",
   "Flight mode assigned to switch position 1 (internal mode code).")
_P(["fswitch_selection_1", "g_config.control.control_mode[1]"], "Flight-mode switch position 2 mode", "",
   "Flight mode assigned to switch position 2 (internal mode code).")
_P(["fswitch_selection_2", "g_config.control.control_mode[2]"], "Flight-mode switch position 3 mode", "",
   "Flight mode assigned to switch position 3 (internal mode code).")
_P(["go_home_heading", "g_config.go_home.go_home_heading_option"], "Return-to-home heading option", "",
   "How the aircraft orients itself while returning home (internal option code).")
_P(["adv_gohome_enable", "g_config.go_home.adv_gohome_enable"], "Advanced return-to-home enabled", "", "", _ONOFF)
_P(["avoid_enable", "g_config.go_home.avoid_enable"], "Obstacle avoidance during RTH enabled", "", "", _ONOFF)
_P(["user_avoid_enable", "g_config.avoid_obstacle_limit_cfg.user_avoid_enable"], "Obstacle avoidance enabled (user)", "",
   "User setting for obstacle avoidance.", _ONOFF)
_P(["g_config.avoid_obstacle_limit_cfg.avoid_obstacle_enable"], "Obstacle avoidance enabled", "", "", _ONOFF)
_P(["close_go_home_height"], "Return-to-home altitude when close to home", "m",
   "Height used for return-to-home when the aircraft is close to the home point.")
_P(["limit_height_abs"], "Absolute height limit", "m", "Absolute height limit value.")
_P(["limit_height_abs_without_gps"], "Height limit without GPS", "m", "Height limit applied when no GPS is available.")
_P(["g_config.airport_limit_cfg.cfg_search_radius"], "Airport no-fly-zone search radius", "m",
   "Radius within which the fly-safe logic searches for airports.")
_P(["g_status.user_info.statistical_info.total_distance"], "Lifetime total flight distance", "m",
   "Cumulative flight distance stored by the aircraft (as recorded; unit consistent with metres).")
_P(["g_status.user_info.statistical_info.total_motor_start_time", "g_status.user_info.statistical_info.total_motor_start_t"],
   "Lifetime total motor-on time", "s", "Cumulative motor running time stored by the aircraft (as recorded; unit consistent with seconds).")
_P(["g_raw.battery_info.serial_number"], "Battery serial number", "", "Serial number reported by the smart battery.")
_P(["smart_bat_basic_cap"], "Smart-battery basic capacity reserve", "%", "")
_P(["bat_enable_smart_bat_landing_protect"], "Smart-battery landing protection enabled", "", "", _ONOFF)
_P(["g_config.fdi.gps_max_horizontal_pos_over_count"], "FDI: GPS horizontal-position outlier count limit", "",
   "Flight-controller fault-detection (FDI) threshold: how many consecutive times the GPS horizontal position may exceed its limits before GPS is treated as faulty.")
_P(["g_config.fdi.gps_max_horizontal_vel_over_count"], "FDI: GPS horizontal-velocity outlier count limit", "",
   "Fault-detection threshold: consecutive GPS horizontal-velocity exceedances tolerated before GPS is treated as faulty.")
_P(["g_config.fdi.gps_max_horizontal_pos_diff"], "FDI: max GPS horizontal-position difference", "",
   "Fault-detection threshold for the allowed difference in GPS horizontal position (unit not stated; consistent with metres).")
_P(["g_config.fdi.max_horizontal_vel_fusion_error"], "FDI: max horizontal-velocity fusion error", "",
   "Fault-detection threshold for the allowed horizontal-velocity error of the sensor-fusion estimate.")
_P(["tripod_func_enabled", "g_config.mode_tripod_en_cfg.tripod_func_enabled"], "Tripod mode enabled", "", "", _ONOFF)

_PARAM_GROUPS: List[Tuple[str, str]] = [
    ("g_config.fdi.", "Flight-controller fault-detection (FDI) parameter"),
    ("g_config.go_home.", "Return-to-home setting"),
    ("g_config.voltage", "Battery/voltage protection setting"),
    ("g_config.flying_limit.", "Flight-limit setting"),
    ("g_config.novice_cfg.", "Beginner-mode setting"),
    ("g_config.mode_sport_cfg.", "Sport-mode tuning"),
    ("mode_sport_cfg", "Sport-mode tuning"),
    ("g_config.mode_tripod", "Tripod-mode tuning"),
    ("mode_tripod_cfg", "Tripod-mode tuning"),
    ("g_config.avoid_obstacle_limit_cfg.", "Obstacle-avoidance setting"),
    ("avoid_", "Obstacle-avoidance setting"),
    ("g_config.hotpoint_cfg.", "Point-of-Interest (hotpoint) setting"),
    ("g_config.followme_cfg.", "Follow-me setting"),
    ("g_config.home_lock_cfg.", "Home-lock setting"),
    ("g_config.airport_limit_cfg.", "Airport no-fly-zone setting"),
    ("g_config.imu_para_cfg.", "IMU / navigation-filter setting"),
    ("g_config.imu_dir", "IMU mounting orientation"),
    ("g_config.receiver.", "Remote-control receiver calibration"),
    ("g_config.aircraft.", "Airframe / motor-mixing setting"),
    ("g_config.api_entry_cfg.", "SDK/API interface setting"),
    ("g_config.serial_api_cfg.", "SDK/API interface setting"),
    ("g_config.control.", "Flight-control setting"),
    ("g_config.fail_safe.", "Failsafe setting"),
    ("g_config.advanced_function.", "Advanced-function switch"),
    ("g_config.output_servo.", "Servo/motor output setting"),
    ("g_config.intelligence_orientation.", "Intelligent Orientation Control (IOC) setting"),
    ("g_config.", "Flight-controller configuration setting"),
    ("g_raw.gps_snr.", "GPS satellite signal strength (SNR) snapshot"),
    ("g_raw.battery_info.", "Battery information"),
    ("g_raw.", "Raw sensor value snapshot"),
    ("g_status.user_info.statistical_info", "Aircraft usage statistics"),
    ("g_status.", "Flight-controller status value"),
    ("g_cfg_debug.", "Debug configuration"),
    ("fix_send", "Data-logging stream configuration"),
    ("cfg_actuator_", "Motor/actuator output setting"),
    ("esc_", "Motor/ESC setting"),
    ("motor_", "Motor setting"),
    ("f_chl_", "Remote-control channel mapping"),
    ("r_rc_", "Remote-control channel mapping"),
    ("farm_", "Agricultural (spraying) statistic/setting"),
    ("sweep_", "Test-signal (frequency sweep) setting"),
    ("range.", "Parameter range metadata"),
    ("go_home", "Return-to-home setting"),
    ("gohome", "Return-to-home setting"),
    ("novice", "Beginner-mode setting"),
    ("bat", "Battery setting"),
    ("rc_", "Remote-control setting"),
    ("limit_", "Flight-limit setting"),
    ("gps", "GPS setting"),
    ("compass", "Compass setting"),
    ("imu", "IMU setting"),
]


def _param_aliases(name: str) -> List[str]:
    out = []
    for a in name.split(","):
        a = a.strip()
        if not a:
            continue
        out.append(a)
        if a.endswith("_0"):
            out.append(a[:-2])
    return out


def _param_info(name: str) -> Optional[Tuple[str, str, str, Optional[Dict[int, str]]]]:
    for a in _param_aliases(name):
        info = PARAMS.get(a.lower())
        if info:
            return info
    return None


def _param_group(name: str) -> Optional[str]:
    for a in _param_aliases(name):
        al = a.lower()
        for prefix, label in _PARAM_GROUPS:
            if al.startswith(prefix.lower()):
                return label
    return None


_PARAM_RE = re.compile(
    r"^\[(?P<value>[^\]]*)\]"
    r"(?:\[(?P<default>[^\]]*)\]\[(?P<min>[^\]]*)\]\[(?P<max>[^\]]*)\]\[(?P<type_code>0x[0-9a-fA-F]+)\])?"
    r"\s*=>\s*(?P<name>\S.*?)\s*$")


def _short_num(s: str) -> str:
    s = s.strip()
    if re.match(r"^-?\d+\.\d+$", s):
        s = s.rstrip("0").rstrip(".")
    return s


def _explain_param(m: "re.Match[str]") -> Dict[str, Any]:
    raw_name = m.group("name")
    value_s = _short_num(m.group("value"))
    values: Dict[str, Any] = {"name": raw_name, "value": _num("value", value_s) if value_s else None}
    if m.group("type_code"):
        values.update({"default": _num("d", _short_num(m.group("default"))),
                       "min": _num("d", _short_num(m.group("min"))),
                       "max": _num("d", _short_num(m.group("max"))),
                       "type_code": m.group("type_code")})
    primary = _param_aliases(raw_name)[0] if _param_aliases(raw_name) else raw_name
    info = _param_info(raw_name)
    group = _param_group(raw_name)
    shown = value_s if value_s else "(empty)"
    head = f"Configuration parameter {raw_name} = {shown}."
    if "default" in values:
        head += f" Firmware default {_short_num(m.group('default'))}, allowed range {_short_num(m.group('min'))} to {_short_num(m.group('max'))}."
        if _short_num(m.group("default")) != value_s:
            head += " The current value differs from the default, i.e. it was changed from the factory setting."
    importance = "low"
    if info:
        friendly, unit, desc, enum = info
        values["friendly"] = friendly
        if unit:
            values["unit"] = unit
        meaning = ""
        if enum is not None and isinstance(values.get("value"), int) and values["value"] in enum:
            meaning = enum[values["value"]]
            values["meaning"] = meaning
        disp_v = f"{shown} {unit}".strip() if not meaning else f"{shown} ({meaning})"
        title = f"{friendly} = {disp_v}"
        detail = f"{head} {friendly}: {desc}".strip()
        if meaning:
            detail += f" Value {shown} means '{meaning}'."
        importance = "normal"
        rule = "cfg.param:" + friendly.lower().replace(" ", "_").replace("/", "_")
    else:
        title = f"Configuration parameter {primary} = {shown}"
        what = f"{group}: '{_humanize(primary)}'." if group else "Flight-controller configuration value; DroneDog has no specific description for this parameter."
        detail = f"{head} {what} Configuration values are dumped by the flight controller at the start of each log."
        rule = "cfg.param"
    return {"title": _clip(title), "detail": detail, "category": "configuration",
            "importance": importance, "values": values, "rule": rule}


# ---------------------------------------------------------------------------
# Tag-level fallbacks
# ---------------------------------------------------------------------------
TAG_INFO: Dict[str, Tuple[str, str]] = {
    "L-FMU": ("system", "flight-management unit (core flight controller)"),
    "L-FMU/LED": ("system", "status-LED controller"),
    "L-FMU/MOTOR": ("motors", "motor manager"),
    "L-FMU/FSM": ("flight", "flight state machine"),
    "L-FMU/DM": ("system", "device manager"),
    "L-FMU/VERSION": ("system", "firmware/version banner"),
    "L-FMU/TOPOLOGY": ("system", "device topology"),
    "L-FMU/WRITE": ("storage", "log writer"),
    "L-FMU/CODE": ("system", "code integrity check"),
    "L-FLYMODE": ("flight", "flight-mode logic"),
    "L-HOME": ("home_point", "home-point module"),
    "L-BATTERY": ("battery", "battery module"),
    "L-GPS": ("gps", "GPS module"),
    "L-RTK": ("gps", "RTK (high-precision GNSS) module"),
    "L-COMPASS": ("compass", "compass module"),
    "L-FDI": ("sensors", "fault detection and isolation (FDI)"),
    "L-NS": ("navigation", "navigation system / attitude estimator"),
    "L-RC": ("radio_link", "remote-controller module"),
    "L-API": ("system", "SDK/API interface"),
    "L-SDK": ("system", "onboard SDK"),
    "L-ACTIVATION": ("activation", "activation module"),
    "L-DEV": ("system", "device module"),
    "L-SYS": ("system", "system module"),
    "L-SYS_ERR": ("system", "system error module"),
    "L-CFG": ("configuration", "parameter/configuration manager"),
    "L-GEO": ("safety", "GEO fly-safe (no-fly-zone) module"),
    "L-FLYLIMIT": ("safety", "flight-limit module"),
    "L-WHITELIST": ("safety", "fly-safe unlock (whitelist) module"),
    "L-ADS-B": ("safety", "ADS-B (manned aircraft detection) module"),
    "L-ESC": ("motors", "electronic speed controller (ESC) module"),
    "L-PWM": ("motors", "motor output (PWM) module"),
    "L-VO": ("sensors", "vision positioning module"),
    "L-VISION": ("sensors", "vision system"),
    "L-TOF": ("sensors", "time-of-flight distance sensor"),
    "L-RADAR": ("sensors", "radar module"),
    "L-AVOID": ("safety", "obstacle-avoidance module"),
    "L-SIM_AVOIDANCE": ("system", "simulator obstacle-avoidance module"),
    "L-TOPO": ("system", "device topology module"),
    "L-MIS": ("navigation", "mission (waypoint) module"),
    "L-N_MIS": ("flight", "flight-task manager"),
    "L-WP2": ("navigation", "waypoint mission module"),
    "L-POI": ("navigation", "point-of-interest module"),
    "L-CAMERA": ("camera_gimbal", "camera control"),
    "L-ZENMUSE": ("camera_gimbal", "Zenmuse camera/gimbal"),
    "L-GIMBAL": ("camera_gimbal", "gimbal module"),
    "L-GYRO_ACC": ("imu", "gyroscope/accelerometer driver"),
    "L-IMU": ("imu", "IMU module"),
    "L-SENSOR": ("sensors", "sensor hub"),
    "L-PITOT": ("sensors", "airspeed (pitot) sensor"),
    "L-TAKEOFF": ("flight", "take-off module"),
    "L-ON_HAND_TAKEOFF": ("flight", "hand-launch take-off module"),
    "L-CTRL": ("flight", "flight-control module"),
    "L-OSD": ("system", "on-screen-display data module"),
    "L-SIM": ("system", "simulator module"),
    "L-USER": ("system", "user statistics module"),
    "L-DBG1": ("system", "debug output"),
    "L-DBG2": ("system", "debug output"),
    "L-FAN": ("system", "cooling fan controller"),
    "L-IOC": ("navigation", "Intelligent Orientation Control (course/home lock)"),
    "L-CMD": ("system", "command dispatcher"),
    "L-SEND DATA": ("radio_link", "telemetry push module"),
    "L-GEAR": ("flight", "landing-gear module"),
    "L-DROPSAFE": ("other", "drop-safe (agricultural) module"),
    "L-SPRAY_SYS": ("other", "spraying system (agricultural)"),
    "L-FARM": ("other", "agricultural (farm) module"),
    "L-AES": ("system", "encryption module"),
    "L-HAL_LINK": ("system", "hardware link layer"),
    "L-SA": ("system", "sensor abstraction layer"),
    "L-CENTER_BOARD": ("system", "centre board"),
    "L-PROTOCOL": ("system", "protocol module"),
    "L-AGENT": ("system", "agent module"),
    "L-EMBEDDED": ("system", "embedded system module"),
    "L-EEPROM": ("storage", "non-volatile memory (EEPROM)"),
    "L-RECORDER": ("storage", "flight-data recorder"),
    "L-AP": ("system", "application processor link"),
    "L-WRISTBAND": ("radio_link", "wristband controller"),
    "L-HEADSET": ("radio_link", "headset (goggles) link"),
    "L-AUTOTEST": ("system", "factory self-test"),
    "L-MAP": ("navigation", "map module"),
    "L-WL": ("radio_link", "wireless link"),
    "L-INFO_SHARE": ("system", "information-sharing module"),
    "L-USB": ("system", "USB module"),
}
TASK_INFO = {
    "root": "flight-controller operating system (root task)",
    "ctrl": "flight-control task",
    "tska": "flight-controller task A",
    "tskd": "flight-controller task D",
    "gps_sign": "GPS signature task",
}
KIND_INFO = {
    "fly_log_text": ("system", "Flight-controller log message"),
    "sd_logs": ("system", "Flight-controller system log message"),
    "drv_log": ("system", "Flight-controller driver log message"),
    "svn_info": ("system", "Firmware build information"),
    "module_name": ("system", "Log module listing"),
    "sys_cfg": ("configuration", "Configuration dump line"),
    "rec_defs": ("system", "Log record definition"),
}
_ALERT_WORDS = re.compile(r"fatal|error|err\b|fail|warn|lost|abnormal|fault", re.I)


def _fallback(tag: str, task: str, body: str, kind: Optional[str]) -> Optional[Dict[str, Any]]:
    imp = "normal" if _ALERT_WORDS.search(body) else "low"
    caution = (" The original text is shown verbatim; DroneDog has no specific interpretation for this line, "
               "so no meaning beyond the source module should be inferred.")
    if tag:
        cat, label = TAG_INFO.get(tag, ("other", f"firmware module [{tag}]"))
        title = label[0].upper() + label[1:] + " message"
        return {"title": _clip(title), "detail": f"Unclassified message from the {label} ([{tag}])." + caution,
                "category": cat, "importance": imp, "values": {"module": tag},
                "rule": f"fallback:tag:{tag}"}
    if task:
        label = TASK_INFO.get(task, f"flight-controller task '{task}'")
        return {"title": _clip(f"Flight-controller task message ({task})"),
                "detail": f"Unclassified message from the {label} ({{{task}}})." + caution,
                "category": "system", "importance": imp, "values": {"task": task},
                "rule": f"fallback:task:{task}"}
    if kind and kind in KIND_INFO:
        cat, label = KIND_INFO[kind]
        return {"title": label, "detail": f"Unclassified {label[0].lower() + label[1:]} (record type '{kind}')." + caution,
                "category": cat, "importance": imp, "values": {}, "rule": f"fallback:kind:{kind}"}
    return None


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------
_BY_TAG_CACHE: Dict[str, List[Rule]] = {}


def _candidates(tag: str) -> List[Rule]:
    lst = _BY_TAG_CACHE.get(tag)
    if lst is None:
        lst = [r for r in RULES if r.tags is None or tag in r.tags]
        _BY_TAG_CACHE[tag] = lst
    return lst


def _apply(rule: Rule, m: "re.Match[str]") -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    disp: Dict[str, Any] = {}
    for k, txt in m.groupdict().items():
        if txt is None:
            continue
        values[k] = _num(k, txt)
        disp[k] = txt.strip()
    title, detail = rule.title, rule.detail
    category, importance = rule.category, rule.importance
    if rule.post is not None:
        over = rule.post(values, disp) or {}
        title = over.get("_title", title)
        detail = over.get("_detail", detail)
        category = over.get("_category", category)
        importance = over.get("_importance", importance)
    for k, v in values.items():
        disp.setdefault(k, str(v))
    return {"title": _clip(_fmt(title, disp)), "detail": _fmt(detail, disp), "category": category,
            "importance": importance, "values": values, "rule": rule.id}


def explain(message: str, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Explain one event line.

    Returns ``{"title", "detail", "category", "importance", "values", "rule"}`` or ``None``
    when nothing at all can be said about the line. ``rule`` ids starting with
    ``fallback:`` mark generic (tag/kind-level) explanations.
    """
    if not isinstance(message, str) or not message.strip():
        return None
    tag, task, body = _clean(message)
    if "=>" in body and body.startswith("["):
        pm = _PARAM_RE.match(body)
        if pm:
            return _explain_param(pm)
    for rule in _candidates(tag):
        if rule.kinds is not None and (kind is None or kind not in rule.kinds):
            continue
        m = rule.regex.search(body)
        if m:
            return _apply(rule, m)
    return _fallback(tag, task, body, kind)


def explain_many(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return copies of ``events`` with the explanation added under ``"explain"``."""
    out = []
    for ev in events or []:
        try:
            e = dict(ev)
        except (TypeError, ValueError):
            e = {"message": ev}
        try:
            kind = e.get("kind")
            e["explain"] = explain(str(e.get("message") or e.get("raw") or ""),
                                   kind if isinstance(kind, str) else None)
        except Exception:  # never let explanation break event display
            e["explain"] = None
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------

def _md(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ").replace("\t", "\\t")


def glossary_markdown() -> str:
    """Render the human-readable event glossary (docs/EVENT_GLOSSARY.md)."""
    L: List[str] = []
    L.append("# DroneDog event glossary")
    L.append("")
    L.append("Plain-English meanings of the DJI flight-log event lines DroneDog explains. Generated from the "
             "rule table in `dronedog/analysis/explain.py` (`python -m dronedog.analysis.explain > docs/EVENT_GLOSSARY.md`); "
             "do not edit by hand.")
    L.append("")
    L.append("How to read this: the original log text is always kept and shown verbatim. The explanation states only "
             "what the line itself establishes; where a field is undocumented the explanation says so. "
             "Each row shows the rule id, the matching pattern (regular expression applied after the leading "
             "flight-controller tick, any `<FLY>`/`{task}INFO:` prefix and the `[L-MODULE]` tag are removed), a real "
             "example line from the test corpus, and the explanation DroneDog produces for that example.")
    L.append("")
    L.append("Importance: **high** = forensically significant (take-off, landing, motor start/stop, home point, "
             "return-to-home, low battery, GPS or signal loss, no-fly zones, forced landing); **normal** = useful "
             "context; **low** = start-up noise and configuration dumps.")
    L.append("")
    sections: Dict[str, List[Rule]] = {}
    for r in RULES:
        sections.setdefault(r.category, []).append(r)
    for cat in CATEGORIES:
        rules = sections.get(cat)
        if not rules:
            continue
        L.append(f"## {cat.replace('_', ' ').title()}")
        L.append("")
        L.append("| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |")
        L.append("|---|---|---|---|---|---|---|")
        for r in rules:
            ex = explain(r.example, (r.kinds or (None,))[0])
            tags = ", ".join(f"[{t}]" for t in r.tags if t and not t.startswith("L-DBG")) if r.tags else "any"
            title = ex["title"] if ex else r.title
            det = ex["detail"] if ex else r.detail
            imp = ex["importance"] if ex else r.importance
            kinds = f" (record kind: {', '.join(r.kinds)})" if r.kinds else ""
            L.append(f"| `{r.id}` | {_md(tags or 'untagged')}{kinds} | `{_md(r.regex.pattern)}` | `{_md(r.example)}` | "
                     f"{_md(title)} | {_md(det)} | {imp} |")
        L.append("")
    L.append("## Configuration parameters")
    L.append("")
    L.append("Lines of the form `[value] => name` or `[value][default][min][max][0xNN] => name` are configuration "
             "values dumped at the start of each DAT log. DroneDog shows them as `Configuration parameter <name> = <value>` "
             "(category configuration, importance low) and notes when the value differs from the firmware default. "
             "The `0xNN` field is a type/flag code and is not interpreted. Well-known parameters get a friendly name "
             "and importance normal:")
    L.append("")
    L.append("| Parameter name(s) | Friendly name | Unit | Meaning | Value codes |")
    L.append("|---|---|---|---|---|")
    grouped: Dict[Tuple[str, str, str], List[str]] = {}
    enums: Dict[Tuple[str, str, str], Optional[Dict[int, str]]] = {}
    for n, (f, u, d, e) in PARAMS.items():
        grouped.setdefault((f, u, d), []).append(n)
        enums[(f, u, d)] = e
    for (f, u, d), names in grouped.items():
        e = enums[(f, u, d)]
        codes = ", ".join(f"{k} = {v}" for k, v in e.items()) if e else ""
        L.append(f"| {_md(', '.join('`' + x + '`' for x in names))} | {_md(f)} | {u} | {_md(d)} | {_md(codes)} |")
    L.append("")
    L.append("Other parameters are labelled by their name prefix:")
    L.append("")
    L.append("| Name prefix | Described as |")
    L.append("|---|---|")
    for p, label in _PARAM_GROUPS:
        L.append(f"| `{_md(p)}` | {_md(label)} |")
    L.append("")
    L.append("## Reference tables")
    L.append("")
    L.append("### App-log flight modes")
    L.append("")
    L.append("| Recorded name | DJI app name | Meaning |")
    L.append("|---|---|---|")
    for k, (n, d) in FLIGHT_MODES.items():
        L.append(f"| `{k}` | {_md(n)} | {_md(d)} |")
    L.append("")
    L.append("### App-log flight-controller actions")
    L.append("")
    L.append("| Recorded name | Title | Meaning |")
    L.append("|---|---|---|")
    for k, (t, d, _c) in FLIGHT_ACTIONS.items():
        L.append(f"| `{k}` | {_md(t)} | {_md(d)} |")
    L.append("")
    L.append("### App-log aircraft fault flags")
    L.append("")
    L.append("| Flag | Title | Meaning |")
    L.append("|---|---|---|")
    for k, (t, d, _c) in OSD_FLAGS.items():
        L.append(f"| `{k}` | {_md(t)} | {_md(d)} |")
    L.append("")
    L.append("### Status-LED conditions (`[L-FMU/LED]action changed.` / `[LED] changed:`)")
    L.append("")
    L.append("| Condition text contains | Title | Meaning | Importance |")
    L.append("|---|---|---|---|")
    for keys, t, d, imp, _c in LED_PATTERNS:
        L.append(f"| {_md(', '.join(repr(k) for k in keys))} | {_md(t)} | {_md(d)} | {imp} |")
    L.append("")
    L.append("### Fault-detection (FDI) navigation sources and fault reasons")
    L.append("")
    L.append("| Token | Meaning |")
    L.append("|---|---|")
    for k, (n, _c) in NS_SOURCES.items():
        L.append(f"| `{k}` | {_md(n)} |")
    for k, d in FDI_REASONS.items():
        L.append(f"| `{k}` | fault reason: {_md(d)} |")
    L.append("")
    L.append("### Battery protection states (`[L-BATTERY]old:X=>>>>new:Y`)")
    L.append("")
    L.append("| State | Meaning |")
    L.append("|---|---|")
    for k, d in BATTERY_STATES.items():
        L.append(f"| `{k}` | {_md(d)} |")
    L.append("")
    L.append("## Fallbacks")
    L.append("")
    L.append("Lines that match no rule but carry a known module tag are labelled with the module only "
             "(rule id `fallback:tag:<tag>`, e.g. `[L-GPS]` -> \"GPS module message\"), with an explicit note that "
             "no further meaning should be inferred. Known module tags:")
    L.append("")
    L.append("| Tag | Category | Module |")
    L.append("|---|---|---|")
    for k, (c, label) in TAG_INFO.items():
        L.append(f"| `[{k}]` | {c} | {_md(label)} |")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.write(glossary_markdown())
