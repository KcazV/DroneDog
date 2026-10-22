//! DJI app TXT flight logs (DJIFlightRecord_*.txt) via the `dji-log-parser` crate,
//! used strictly as an offline library.
//!
//! OFFLINE GUARANTEE: this module never calls `DJILog::fetch_keychains`,
//! `fetch_keychains_async`, `KeychainsRequest::fetch` or anything else that touches the
//! network. v13+ logs are decoded only when the caller supplies cached keychains
//! (`keychains_json`); otherwise we return the plaintext Details and report the records
//! as encrypted. `tests/offline_guard.rs` enforces this by scanning the source.
//!
//! Field mapping and unit conversions are documented in `core/MAPPING.md`.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;

use chrono::{DateTime, Datelike, SecondsFormat, Utc};
use dji_log_parser::frame::{records_to_frames, Frame};
use dji_log_parser::keychain::KeychainFeaturePoint;
use dji_log_parser::layout::details::{Details as DjiDetails, ProductType};
use dji_log_parser::record::component_serial::ComponentType;
use dji_log_parser::record::firmware::SenderType;
use dji_log_parser::record::smart_battery_group::SmartBatteryGroup;
use dji_log_parser::record::Record;
use dji_log_parser::DJILog;

use crate::detect::{u64le, TXT_FIRST_ENCRYPTED_VERSION};
use crate::frame::{fin, haversine_m, valid_latlon, Frames, Row};
use crate::model::{version_string, Details, Event, ParseResult, Severity, Value};

pub const ADAPTER_NAME: &str = "txt_djilog";

/// RC stick raw range: 364..1684, centre 1024 (DJI RC hardware channels, +-660).
const RC_CENTER: f64 = 1024.0;
const RC_HALF_RANGE: f64 = 660.0;

#[derive(Debug)]
pub enum TxtError {
    /// File could not be read.
    Io(std::io::Error),
    /// Not a readable DJI TXT log, or the library rejected it.
    Parse(String),
    /// `keychains_json` was malformed.
    Keychain(String),
    /// The library panicked; caught and reported, never propagated.
    Panic(String),
}

impl std::fmt::Display for TxtError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TxtError::Io(e) => write!(f, "{e}"),
            TxtError::Parse(m) => write!(f, "{m}"),
            TxtError::Keychain(m) => write!(f, "invalid keychains_json: {m}"),
            TxtError::Panic(m) => write!(f, "internal error inside dji-log-parser: {m}"),
        }
    }
}

fn map_lib_err(context: &str, e: dji_log_parser::Error) -> TxtError {
    use dji_log_parser::Error as E;
    let msg = match &e {
        E::KeychainRequired => "records are encrypted (log v13+) and need DJI keychains".to_string(),
        E::MissingAuxilliaryData(s) => format!("v13+ auxiliary block missing: {s}"),
        E::Parse(b) => format!("binary layout parse error: {b}"),
        E::Io(io) => format!("I/O error: {io}"),
        other => other.to_string(),
    };
    TxtError::Parse(format!("dji-log-parser {context}: {msg}"))
}

fn panic_msg(p: Box<dyn std::any::Any + Send>) -> String {
    if let Some(s) = p.downcast_ref::<&str>() {
        s.to_string()
    } else if let Some(s) = p.downcast_ref::<String>() {
        s.clone()
    } else {
        "unknown panic".into()
    }
}

fn guarded<T>(what: &str, f: impl FnOnce() -> T) -> Result<T, TxtError> {
    catch_unwind(AssertUnwindSafe(f)).map_err(|p| TxtError::Panic(format!("{what}: {}", panic_msg(p))))
}

/// Pre-flight checks so that malformed headers become clean errors instead of
/// out-of-bounds slicing inside the library.
fn preflight(bytes: &[u8]) -> Result<u8, TxtError> {
    if bytes.len() < 100 {
        return Err(TxtError::Parse(format!(
            "not a DJI TXT log: file is {} bytes, smaller than the 100-byte prefix",
            bytes.len()
        )));
    }
    let version = bytes[10];
    if version == 0 || version > 30 {
        return Err(TxtError::Parse(format!(
            "not a DJI TXT log: version byte at 0x0A is {version}"
        )));
    }
    let detail_offset = u64le(bytes, 0).unwrap_or(u64::MAX);
    if version < 12 && detail_offset > bytes.len() as u64 {
        return Err(TxtError::Parse(format!(
            "not a readable DJI TXT log: detail_offset {detail_offset} is past end of file ({} bytes); file truncated or not a TXT log",
            bytes.len()
        )));
    }
    Ok(version)
}

fn iso(dt: &DateTime<Utc>) -> String {
    dt.to_rfc3339_opts(SecondsFormat::Millis, true)
}

fn valid_dt(dt: &DateTime<Utc>) -> bool {
    let y = dt.year();
    y > 2010 && y < 2100
}

fn product_name(p: &ProductType) -> Option<String> {
    match p {
        ProductType::None => None,
        other => Some(format!("{other:?}")),
    }
}

/// C2 details from the plaintext Details block.
fn map_details(d: &DjiDetails, version: u8) -> Details {
    let mut out = Details::default();
    out.set(
        "aircraft_model",
        product_name(&d.product_type).map(Value::Str).unwrap_or(Value::Null),
    );
    out.set("aircraft_name", Value::opt_str(&d.aircraft_name));
    out.set("aircraft_sn", Value::opt_str(&d.aircraft_sn));
    out.set("camera_sn", Value::opt_str(&d.camera_sn));
    out.set("rc_sn", Value::opt_str(&d.rc_sn));
    out.set("battery_sn", Value::opt_str(&d.battery_sn));
    out.set("app_platform", Value::Str(format!("{:?}", d.app_platform)));
    out.set(
        "app_version",
        if d.app_version == "0.0.0" {
            Value::Null
        } else {
            Value::opt_str(&d.app_version)
        },
    );
    out.set("log_version", Value::Int(version as i64));
    out.set(
        "start_time_utc",
        if valid_dt(&d.start_time) {
            Value::Str(iso(&d.start_time))
        } else {
            Value::Null
        },
    );
    out.set("duration_s", Value::float(d.total_time));
    out.set("total_distance_m", Value::float(d.total_distance as f64));
    out.set("max_height_m", Value::float(d.max_height as f64));
    out.set("max_h_speed_ms", Value::float(d.max_horizontal_speed as f64));
    out.set("max_v_speed_ms", Value::float(d.max_vertical_speed as f64));
    match valid_latlon(d.latitude, d.longitude) {
        Some((la, lo)) => {
            out.set("home_lat", Value::Float(la));
            out.set("home_lon", Value::Float(lo));
        }
        None => {}
    }
    out.set("city", Value::opt_str(&d.city));
    out.set("area", Value::opt_str(&d.area));
    out.set("sub_street", Value::opt_str(&d.sub_street));
    out.set("record_line_count", Value::Int(d.record_line_count as i64));
    // Extensions beyond C2 (documented in MAPPING.md).
    out.set("street", Value::opt_str(&d.street));
    out.set("product_type_code", Value::Str(format!("{:?}", d.product_type)));
    out.set("photo_count", Value::Int(d.capture_num as i64));
    out.set("video_time_s", Value::Int(d.video_time));
    out
}

/// Per-frame state the library's `Frame` does not carry, collected by our own pass
/// over the same records with identical frame segmentation (a frame is the state at
/// the moment the *next* OSD record arrives).
#[derive(Debug, Clone, Default)]
struct Extra {
    rc_mode_switch: Option<String>,
    rc_outcontrol: bool,
    rc_seen: bool,
    gimbal_seen: bool,
    home_seen: bool,
    battery_seen: bool,
    smart_temp_seen: bool,
    center_temp_c: Option<f64>,
    /// `Custom.distance` (m) as recorded by the app; None until a Custom record.
    app_distance_m: Option<f64>,
    cells_measured: bool,
}

struct RecordPass {
    extras: Vec<Extra>,
    /// (frame index, kind, severity, message)
    app_msgs: Vec<(usize, &'static str, Severity, String)>,
    firmware: Vec<(String, String)>,
    serials: Vec<(String, String)>,
    counts: RecordCounts,
}

#[derive(Default, Debug)]
struct RecordCounts {
    total: i64,
    invalid: i64,
    unknown: i64,
    osd: i64,
    jpeg: i64,
    garbled_msgs: i64,
}

/// True when a decoded app message looks like readable text rather than mis-decoded
/// bytes (replacement chars / control chars).
fn readable_text(s: &str) -> bool {
    let n = s.chars().count();
    if n == 0 {
        return false;
    }
    let bad = s
        .chars()
        .filter(|c| *c == '\u{FFFD}' || (c.is_control() && !c.is_whitespace()))
        .count();
    bad * 10 <= n // at most 10% suspicious characters
}

fn app_msg(
    out: &mut Vec<(usize, &'static str, Severity, String)>,
    c: &mut RecordCounts,
    fidx: usize,
    kind: &'static str,
    sev: Severity,
    msg: &str,
) {
    let m = msg.trim_matches(|ch: char| ch.is_whitespace() || ch == '\0');
    if m.is_empty() {
        return;
    }
    if readable_text(m) {
        out.push((fidx, kind, sev, m.to_string()));
    } else {
        c.garbled_msgs += 1;
    }
}

fn record_pass(records: &[Record], version: u8) -> RecordPass {
    let mut cur = Extra::default();
    let mut extras = Vec::new();
    let mut osd_seen: usize = 0;
    let mut app_msgs = Vec::new();
    let mut firmware: Vec<(String, String)> = Vec::new();
    let mut serials: Vec<(String, String)> = Vec::new();
    let mut c = RecordCounts::default();

    for r in records {
        c.total += 1;
        let fidx = osd_seen.saturating_sub(1);
        match r {
            Record::OSD(osd) => {
                c.osd += 1;
                if osd_seen > 0 {
                    extras.push(cur.clone());
                }
                cur.rc_outcontrol = osd.rc_outcontrol;
                osd_seen += 1;
            }
            Record::RC(rc) => {
                cur.rc_seen = true;
                cur.rc_mode_switch = Some(format!("{:?}", rc.flight_mode_switch));
            }
            Record::RCDisplayField(_) => cur.rc_seen = true,
            Record::Gimbal(_) => cur.gimbal_seen = true,
            Record::Home(_) => cur.home_seen = true,
            Record::Custom(cu) => {
                let d = cu.distance as f64;
                cur.app_distance_m = if d.is_finite() && d >= 0.0 { Some(d) } else { None };
            }
            Record::CenterBattery(b) => {
                cur.battery_seen = true;
                cur.cells_measured = true;
                if version >= 8 && b.temperature.is_finite() && b.temperature > -100.0 {
                    cur.center_temp_c = Some(b.temperature as f64);
                }
            }
            Record::SmartBattery(_) => cur.battery_seen = true,
            Record::SmartBatteryGroup(g) => match g {
                SmartBatteryGroup::SmartBatteryDynamic(_) => {
                    cur.battery_seen = true;
                    cur.smart_temp_seen = true;
                }
                SmartBatteryGroup::SmartBatterySingleVoltage(_) => cur.cells_measured = true,
                SmartBatteryGroup::SmartBatteryStatic(_) => {}
            },
            Record::AppTip(t) => {
                app_msg(&mut app_msgs, &mut c, fidx, "app_tip", Severity::Info, &t.message)
            }
            Record::AppWarn(w) => {
                app_msg(&mut app_msgs, &mut c, fidx, "app_warning", Severity::Warn, &w.message)
            }
            Record::AppSeriousWarn(w) => app_msg(
                &mut app_msgs,
                &mut c,
                fidx,
                "app_serious_warning",
                Severity::Critical,
                &w.message,
            ),
            Record::Firmware(fw) => {
                let who = match &fw.sender_type {
                    SenderType::MC => "flight_controller".to_string(),
                    other => format!("{other:?}").to_lowercase(),
                };
                if fw.version != "0.0.0" && !firmware.iter().any(|(k, _)| *k == who) {
                    firmware.push((who, fw.version.clone()));
                }
            }
            Record::ComponentSerial(cs) => {
                let who = match &cs.component_type {
                    ComponentType::Unknown(n) => format!("component_{n}"),
                    other => format!("{other:?}").to_lowercase(),
                };
                let s = cs.serial.trim().to_string();
                if !s.is_empty() && !serials.iter().any(|(k, v)| *k == who && *v == s) {
                    serials.push((who, s));
                }
            }
            Record::Invalid(_) => c.invalid += 1,
            Record::Unknown(..) => c.unknown += 1,
            Record::JPEG(_) => c.jpeg += 1,
            _ => {}
        }
    }
    RecordPass {
        extras,
        app_msgs,
        firmware,
        serials,
        counts: c,
    }
}

fn rc_norm(raw: u16) -> f64 {
    ((raw as f64 - RC_CENTER) / RC_HALF_RANGE).clamp(-1.0, 1.0)
}

/// Builds C3 columns from library frames + our extras.
fn build_frames(frames: &[Frame], extras: &[Extra], warnings: &mut Vec<String>) -> (Frames, Vec<Option<i64>>) {
    let n = frames.len();
    let mut out = Frames::with_capacity(n);

    // Clock: the app wall-clock timestamp from Custom records (ms resolution).
    let first_ts = frames
        .iter()
        .map(|f| f.custom.date_time)
        .find(valid_dt);
    let mut no_clock = 0usize;
    let mut backwards = 0usize;
    let mut last_off: Option<i64> = None;
    let mut offsets = Vec::with_capacity(n);

    for (i, f) in frames.iter().enumerate() {
        let ex = extras.get(i).cloned().unwrap_or_default();
        let mut r = Row::default();

        // --- time
        let dt = f.custom.date_time;
        if valid_dt(&dt) {
            r.t_utc = Some(iso(&dt));
            if let Some(t0) = first_ts {
                let off = (dt - t0).num_milliseconds();
                if let Some(prev) = last_off {
                    if off < prev {
                        backwards += 1;
                    }
                }
                last_off = Some(off);
                r.t_offset_ms = Some(off);
            }
        } else {
            no_clock += 1;
        }
        offsets.push(r.t_offset_ms);

        // --- position
        let pos = valid_latlon(f.osd.latitude, f.osd.longitude);
        if let Some((la, lo)) = pos {
            r.lat = Some(la);
            r.lon = Some(lo);
        }
        r.alt_rel_m = fin(f.osd.height as f64);
        r.height_m = fin(f.osd.height as f64);
        let home = if ex.home_seen {
            valid_latlon(f.home.latitude, f.home.longitude)
        } else {
            None
        };
        if let Some((hla, hlo)) = home {
            r.home_lat = Some(hla);
            r.home_lon = Some(hlo);
            // library: osd.altitude = OSD relative height + Home altitude
            r.alt_msl_m = fin(f.osd.altitude as f64);
            if let Some((la, lo)) = pos {
                r.distance_m = Some(haversine_m(hla, hlo, la, lo));
            }
        }

        // --- velocity (NED, m/s; DJI FlightRecord OSD speed_x/y/z * 0.1)
        let (vn, ve, vd) = (f.osd.x_speed as f64, f.osd.y_speed as f64, f.osd.z_speed as f64);
        r.vel_n = fin(vn);
        r.vel_e = fin(ve);
        r.vel_d = fin(vd);
        r.speed_h_ms = fin((vn * vn + ve * ve).sqrt());

        // --- attitude
        r.roll = fin(f.osd.roll as f64);
        r.pitch = fin(f.osd.pitch as f64);
        r.yaw = fin(f.osd.yaw as f64);

        if ex.gimbal_seen {
            r.gimbal_pitch = fin(f.gimbal.pitch as f64);
            r.gimbal_roll = fin(f.gimbal.roll as f64);
            r.gimbal_yaw = fin(f.gimbal.yaw as f64);
        }

        if ex.rc_seen {
            r.rc_aileron = Some(rc_norm(f.rc.aileron));
            r.rc_elevator = Some(rc_norm(f.rc.elevator));
            r.rc_throttle = Some(rc_norm(f.rc.throttle));
            r.rc_rudder = Some(rc_norm(f.rc.rudder));
        }
        r.rc_mode_switch = ex.rc_mode_switch.clone();
        r.flight_mode = f.osd.flyc_state.map(|m| format!("{m:?}"));
        r.gps_sats = Some(f.osd.gps_num as i64);
        r.gps_health = Some(f.osd.gps_level as i64);

        // --- battery
        if ex.battery_seen {
            r.battery_pct = Some(f.battery.charge_level as f64);
            r.battery_v = fin(f.battery.voltage as f64);
        }
        if ex.cells_measured && !f.battery.is_cell_voltage_estimated {
            r.battery_cells_v = Some(f.battery.cell_voltages.iter().map(|&v| v as f64).collect());
        }
        r.battery_temp_c = if ex.smart_temp_seen {
            fin(f.battery.temperature as f64)
        } else {
            ex.center_temp_c
        };

        r.motor_on = Some(f.osd.is_motor_on);
        r.is_flying = Some(!f.osd.is_on_ground);

        // --- v1.1 columns
        // VPS / ultrasonic height is only meaningful while the sensor reports working.
        r.vps_height_m = if f.osd.is_swave_work {
            fin(f.osd.vps_height as f64)
        } else {
            None
        };
        r.distance_app_m = ex.app_distance_m;
        r.rc_signal_up_pct = f.rc.uplink_signal.map(|v| v as f64);
        r.rc_signal_down_pct = f.rc.downlink_signal.map(|v| v as f64);

        out.push(r);
    }

    if n > 0 && first_ts.is_none() {
        warnings.push(
            "no valid app timestamps (Custom records) in this log: t_utc and t_offset_ms are null".into(),
        );
    } else if no_clock > 0 {
        warnings.push(format!(
            "{no_clock} of {n} frames precede the first valid app timestamp: their t_utc / t_offset_ms are null"
        ));
    }
    if backwards > 0 {
        warnings.push(format!(
            "app wall clock went backwards {backwards} time(s); t_offset_ms is not monotonic there"
        ));
    }
    (out, offsets)
}

/// Fill gaps so every event gets an integer offset: nearest earlier known offset,
/// else the first known one, else 0.
fn event_offsets(offsets: &[Option<i64>]) -> Vec<i64> {
    let first = offsets.iter().flatten().next().copied().unwrap_or(0);
    let mut last = first;
    offsets
        .iter()
        .map(|o| {
            if let Some(v) = o {
                last = *v;
            }
            o.unwrap_or(last)
        })
        .collect()
}

fn push_ev(evs: &mut Vec<Event>, t: i64, kind: &str, sev: Severity, msg: String, raw: Option<String>) {
    evs.push(Event {
        t_offset_ms: t,
        kind: kind.to_string(),
        severity: sev,
        message: msg,
        raw,
    });
}

fn flight_action_severity(a: &str) -> (&'static str, Severity) {
    let l = a.to_ascii_lowercase();
    if l.contains("outofcontrol") {
        ("signal_loss_rth", Severity::Critical)
    } else if l.contains("voltage") || l.contains("power") || l.contains("battery") {
        ("low_battery_action", Severity::Critical)
    } else if l.contains("gohome") || l.contains("rth") {
        ("rth", Severity::Warn)
    } else if l.contains("landing") {
        ("auto_landing", Severity::Warn)
    } else {
        ("flight_action", Severity::Info)
    }
}

/// Derive C1 events from frame-to-frame transitions and raw app messages.
fn derive_events(frames: &[Frame], extras: &[Extra], pass: &RecordPass, offs: &[i64]) -> Vec<Event> {
    let mut evs = Vec::new();
    let t_of = |i: usize| -> i64 { offs.get(i).copied().or_else(|| offs.last().copied()).unwrap_or(0) };

    let mut prev: Option<&Frame> = None;
    let mut prev_ex: Option<&Extra> = None;
    for (i, f) in frames.iter().enumerate() {
        let t = t_of(i);
        let ex = extras.get(i);
        let mode = f.osd.flyc_state.map(|m| format!("{m:?}"));
        match prev {
            None => {
                if let Some(m) = &mode {
                    push_ev(&mut evs, t, "flight_mode", Severity::Info, format!("Initial flight mode {m}"), Some(m.clone()));
                }
                if f.osd.is_motor_on {
                    push_ev(&mut evs, t, "motor_start", Severity::Info, "Motors already running at log start".into(), None);
                }
                if !f.osd.is_on_ground {
                    push_ev(&mut evs, t, "takeoff", Severity::Info, "Aircraft already airborne at log start".into(), None);
                }
            }
            Some(p) => {
                let pmode = p.osd.flyc_state.map(|m| format!("{m:?}"));
                if mode != pmode {
                    if let Some(m) = &mode {
                        let from = pmode.clone().unwrap_or_else(|| "none".into());
                        let (kind, sev) = if m.contains("GoHome") {
                            ("rth", Severity::Warn)
                        } else {
                            ("flight_mode_change", Severity::Info)
                        };
                        push_ev(&mut evs, t, kind, sev, format!("Flight mode changed from {from} to {m}"), Some(m.clone()));
                    }
                }
                if f.osd.is_motor_on != p.osd.is_motor_on {
                    if f.osd.is_motor_on {
                        push_ev(&mut evs, t, "motor_start", Severity::Info, "Motors started".into(), None);
                    } else {
                        push_ev(&mut evs, t, "motor_stop", Severity::Info, "Motors stopped".into(), None);
                    }
                }
                if f.osd.is_on_ground != p.osd.is_on_ground {
                    if f.osd.is_on_ground {
                        push_ev(&mut evs, t, "landing", Severity::Info, "Aircraft on ground".into(), None);
                    } else {
                        push_ev(&mut evs, t, "takeoff", Severity::Info, "Aircraft airborne".into(), None);
                    }
                }
                // Return-to-home state machine.
                let gh = f.osd.go_home_status.map(|g| format!("{g:?}"));
                let pgh = p.osd.go_home_status.map(|g| format!("{g:?}"));
                if gh != pgh {
                    if let Some(g) = &gh {
                        if g == "Standby" {
                            push_ev(&mut evs, t, "rth_end", Severity::Info, "Return-to-home status back to Standby".into(), Some(g.clone()));
                        } else if pgh.as_deref().map_or(true, |x| x == "Standby") {
                            push_ev(&mut evs, t, "rth", Severity::Warn, format!("Return-to-home started (status {g})"), Some(g.clone()));
                        } else {
                            push_ev(&mut evs, t, "rth_status", Severity::Info, format!("Return-to-home status {g}"), Some(g.clone()));
                        }
                    }
                }
                // Flight action (why the FC did something automatically).
                let fa = f.osd.flight_action.map(|a| format!("{a:?}"));
                let pfa = p.osd.flight_action.map(|a| format!("{a:?}"));
                if fa != pfa {
                    if let Some(a) = &fa {
                        if a != "None" {
                            let (kind, sev) = flight_action_severity(a);
                            push_ev(&mut evs, t, kind, sev, format!("Flight controller action: {a}"), Some(a.clone()));
                        }
                    }
                }
                // Battery voltage warning level from OSD.
                if f.osd.voltage_warning != p.osd.voltage_warning && f.osd.voltage_warning > 0 {
                    let sev = if f.osd.voltage_warning >= 2 { Severity::Critical } else { Severity::Warn };
                    push_ev(
                        &mut evs,
                        t,
                        "low_battery",
                        sev,
                        format!("Battery voltage warning level {} (charge {}%)", f.osd.voltage_warning, f.battery.charge_level),
                        Some(format!("voltage_warning={}", f.osd.voltage_warning)),
                    );
                }
                // GPS health.
                if f.osd.gps_level < 3 && p.osd.gps_level >= 3 {
                    push_ev(&mut evs, t, "gps_health_drop", Severity::Warn,
                        format!("GPS health dropped from {} to {} ({} satellites)", p.osd.gps_level, f.osd.gps_level, f.osd.gps_num),
                        Some(format!("gps_level={}", f.osd.gps_level)));
                } else if f.osd.gps_level >= 3 && p.osd.gps_level < 3 && i > 1 {
                    push_ev(&mut evs, t, "gps_health_recovered", Severity::Info,
                        format!("GPS health recovered to {} ({} satellites)", f.osd.gps_level, f.osd.gps_num),
                        Some(format!("gps_level={}", f.osd.gps_level)));
                }
                if p.osd.is_gpd_used && !f.osd.is_gpd_used {
                    let cause = f.osd.non_gps_cause.map(|c| format!("{c:?}")).unwrap_or_default();
                    push_ev(&mut evs, t, "gps_lost", Severity::Warn, format!("GPS positioning no longer used (cause: {cause})"), Some(cause));
                }
                // Link quality (OFDM uplink / downlink %).
                for (name, cur, old) in [
                    ("uplink", f.rc.uplink_signal, p.rc.uplink_signal),
                    ("downlink", f.rc.downlink_signal, p.rc.downlink_signal),
                ] {
                    match (old, cur) {
                        (Some(o), Some(0)) if o > 0 => push_ev(&mut evs, t, "signal_loss", Severity::Critical,
                            format!("RC {name} signal dropped to 0% (was {o}%)"), Some(format!("{name}=0"))),
                        (Some(0), Some(c)) if c > 0 => push_ev(&mut evs, t, "signal_restored", Severity::Info,
                            format!("RC {name} signal restored to {c}%"), Some(format!("{name}={c}"))),
                        _ => {}
                    }
                }
                // RC out-of-control flag from the OSD record.
                if let (Some(e), Some(pe)) = (ex, prev_ex) {
                    if e.rc_outcontrol && !pe.rc_outcontrol {
                        push_ev(&mut evs, t, "signal_loss", Severity::Critical, "OSD reports RC link lost (rc_outcontrol)".into(), Some("rc_outcontrol=1".into()));
                    } else if !e.rc_outcontrol && pe.rc_outcontrol {
                        push_ev(&mut evs, t, "signal_restored", Severity::Info, "OSD reports RC link back (rc_outcontrol cleared)".into(), Some("rc_outcontrol=0".into()));
                    }
                }
                // Aircraft fault flags (rising edge only).
                let flags: [(&str, bool, bool, Severity); 9] = [
                    ("compass_error", f.osd.is_compass_error, p.osd.is_compass_error, Severity::Warn),
                    ("barometer_dead_in_air", f.osd.is_barometer_dead_in_air, p.osd.is_barometer_dead_in_air, Severity::Critical),
                    ("motor_blocked", f.osd.is_motor_blocked, p.osd.is_motor_blocked, Severity::Critical),
                    ("not_enough_force", f.osd.is_not_enough_force, p.osd.is_not_enough_force, Severity::Critical),
                    ("propeller_catapult", f.osd.is_propeller_catapult, p.osd.is_propeller_catapult, Severity::Critical),
                    ("accelerometer_over_range", f.osd.is_acceletor_over_range, p.osd.is_acceletor_over_range, Severity::Warn),
                    ("vibrating", f.osd.is_vibrating, p.osd.is_vibrating, Severity::Warn),
                    ("out_of_limit", f.osd.is_out_of_limit, p.osd.is_out_of_limit, Severity::Warn),
                    ("obstacle_sensor_error", f.osd.wave_error, p.osd.wave_error, Severity::Warn),
                ];
                for (name, now, before, sev) in flags {
                    if now && !before {
                        push_ev(&mut evs, t, "aircraft_fault", sev, format!("OSD flag set: {name}"), Some(name.to_string()));
                    }
                }
                let mc = f.osd.motor_start_failed_cause.map(|c| format!("{c:?}"));
                let pmc = p.osd.motor_start_failed_cause.map(|c| format!("{c:?}"));
                if mc != pmc {
                    if let Some(c) = &mc {
                        if c != "None" {
                            push_ev(&mut evs, t, "motor_start_failed", Severity::Warn, format!("Motor start blocked: {c}"), Some(c.clone()));
                        }
                    }
                }
            }
        }
        prev = Some(f);
        prev_ex = ex;
    }

    for (fi, kind, sev, msg) in &pass.app_msgs {
        push_ev(&mut evs, t_of(*fi), kind, *sev, msg.clone(), Some(msg.clone()));
    }
    // Stable sort by time; equal times keep insertion order.
    evs.sort_by_key(|e| e.t_offset_ms);
    evs
}

fn parse_keychains(json: &str) -> Result<Vec<Vec<KeychainFeaturePoint>>, TxtError> {
    serde_json::from_str::<Vec<Vec<KeychainFeaturePoint>>>(json).map_err(|e| {
        TxtError::Keychain(format!(
            "{e} (expected JSON [[{{\"featurePoint\":..,\"aesKey\":..,\"aesIv\":..}}, ...], ...] as cached from DJI)"
        ))
    })
}

/// Parse a DJI TXT log from bytes. `keychains_json` is an offline, cached keychain
/// list (only used for v13+).
pub fn parse_txt_bytes(bytes: Vec<u8>, keychains_json: Option<&str>) -> Result<ParseResult, TxtError> {
    let version = preflight(&bytes)?;
    let mut warnings: Vec<String> = Vec::new();

    let keychains = match keychains_json {
        Some(j) if !j.trim().is_empty() => {
            let k = parse_keychains(j)?;
            if k.iter().all(|c| c.is_empty()) {
                warnings.push("keychains_json contained no keychain entries; treated as not supplied".into());
                None
            } else {
                Some(k)
            }
        }
        _ => None,
    };

    let log = guarded("reading prefix/details", || DJILog::from_bytes(bytes))?
        .map_err(|e| map_lib_err("could not read header", e))?;
    let version = if log.version != version { log.version } else { version };
    let mut details = map_details(&log.details, version);

    let encrypted = version >= TXT_FIRST_ENCRYPTED_VERSION;
    let mk = |details: Details, frames: Frames, events: Vec<Event>, c: (i64, i64, i64), warnings: Vec<String>, unreadable: bool| ParseResult {
        adapter: ADAPTER_NAME.into(),
        adapter_version: version_string(),
        details,
        frames,
        events,
        records_total: c.0,
        records_decoded: c.1,
        records_failed: c.2,
        warnings,
        encrypted_unreadable: unreadable,
    };

    if encrypted && keychains.is_none() {
        warnings.push(format!(
            "log version {version} records are AES encrypted; no DJI keychains were supplied, so only the plaintext header (details) was read. Flight records were not decoded."
        ));
        return Ok(mk(details, Frames::default(), Vec::new(), (0, 0, 0), warnings, true));
    }
    if !encrypted && keychains.is_some() {
        warnings.push(format!(
            "keychains_json supplied but log version {version} is not encrypted; keychains ignored"
        ));
    }
    let kc = if encrypted { keychains } else { None };

    // NOTE: records() never touches the network; it only consumes the supplied keychains.
    let records = guarded("decoding records", || log.records(kc))?
        .map_err(|e| map_lib_err("could not decode records", e))?;

    let pass = record_pass(&records, version);
    if let Some((_, v)) = pass.firmware.iter().find(|(k, _)| k == "flight_controller") {
        details.set("firmware_version", Value::Str(v.clone()));
    }
    if !pass.firmware.is_empty() {
        details.set(
            "firmware_versions",
            Value::Map(pass.firmware.iter().map(|(k, v)| (k.clone(), Value::Str(v.clone()))).collect()),
        );
    }
    if !pass.serials.is_empty() {
        details.set(
            "component_serials",
            Value::Map(pass.serials.iter().map(|(k, v)| (k.clone(), Value::Str(v.clone()))).collect()),
        );
    }
    let c = &pass.counts;
    // records_total = records_decoded + records_failed + (well-framed records of a type
    // the library does not decode; reported in a warning and details.records_unknown_type).
    let failed = c.invalid;
    let decoded = c.total - c.invalid - c.unknown;
    details.set("records_unknown_type", Value::Int(c.unknown));
    if c.invalid > 0 {
        warnings.push(format!(
            "{} of {} records were unreadable (library resynchronised past them)",
            c.invalid, c.total
        ));
        if c.total > 0 && c.invalid * 20 > c.total {
            warnings.push(format!(
                "{:.0}% of records failed to decode: frames and events for this log are INCOMPLETE",
                100.0 * c.invalid as f64 / c.total as f64
            ));
        }
    }
    if c.unknown > 0 {
        warnings.push(format!(
            "{} records have a record type dji-log-parser 0.5.7 does not decode (framed correctly, content not used)",
            c.unknown
        ));
    }
    if c.garbled_msgs > 0 {
        warnings.push(format!(
            "{} app tip/warning messages decoded to unreadable bytes and were not turned into events",
            c.garbled_msgs
        ));
    }
    if c.osd == 0 {
        warnings.push("no OSD records decoded: no frames".into());
    } else {
        warnings.push(
            "dji-log-parser emits a frame when the next OSD record arrives; the final OSD record's frame is not emitted (1 frame)".into(),
        );
    }
    if encrypted {
        warnings.push(format!(
            "log version {version} decrypted with supplied offline keychains"
        ));
    }

    let details_for_frames = log.details.clone();
    let frames = guarded("building frames", || records_to_frames(records, details_for_frames))?;
    if frames.len() != pass.extras.len() {
        warnings.push(format!(
            "internal frame alignment mismatch: library frames {} vs record pass {}; extra columns may be offset",
            frames.len(),
            pass.extras.len()
        ));
    }

    let (cols, offsets) = build_frames(&frames, &pass.extras, &mut warnings);
    let offs = event_offsets(&offsets);
    let events = derive_events(&frames, &pass.extras, &pass, &offs);

    Ok(mk(details, cols, events, (c.total, decoded, failed), warnings, false))
}

pub fn parse_txt(path: &Path, keychains_json: Option<&str>) -> Result<ParseResult, TxtError> {
    if path.is_dir() {
        return Err(TxtError::Io(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("{} is a directory, not a file", path.display()),
        )));
    }
    let bytes = std::fs::read(path).map_err(TxtError::Io)?;
    parse_txt_bytes(bytes, keychains_json)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn garbage_is_clean_error() {
        assert!(matches!(parse_txt_bytes(vec![], None), Err(TxtError::Parse(_))));
        assert!(matches!(parse_txt_bytes(vec![0xAB; 50], None), Err(TxtError::Parse(_))));
        let mut b = vec![0u8; 500];
        b[0..8].copy_from_slice(&10_000u64.to_le_bytes());
        b[10] = 11;
        assert!(matches!(parse_txt_bytes(b, None), Err(TxtError::Parse(_))));
    }

    #[test]
    fn random_never_panics() {
        let mut x: u64 = 0x1234_5678_9abc_def1;
        for n in [100usize, 101, 200, 600, 2000, 5000] {
            for _ in 0..20 {
                let mut b: Vec<u8> = (0..n)
                    .map(|_| {
                        x ^= x << 13;
                        x ^= x >> 7;
                        x ^= x << 17;
                        (x >> 32) as u8
                    })
                    .collect();
                // Force a plausible prefix so we exercise the library, not just preflight.
                let off = (n as u64) / 2;
                b[0..8].copy_from_slice(&off.to_le_bytes());
                b[10] = (x % 14 + 1) as u8;
                let _ = parse_txt_bytes(b, None);
            }
        }
    }

    #[test]
    fn bad_keychain_json() {
        let r = parse_keychains("{not json");
        assert!(matches!(r, Err(TxtError::Keychain(_))));
        assert!(parse_keychains("[]").unwrap().is_empty());
    }

    #[test]
    fn rc_normalisation() {
        assert_eq!(rc_norm(1024), 0.0);
        assert_eq!(rc_norm(1684), 1.0);
        assert_eq!(rc_norm(364), -1.0);
        assert_eq!(rc_norm(0), -1.0);
    }

    #[test]
    fn event_offsets_fill() {
        assert_eq!(event_offsets(&[None, Some(5), None, Some(9)]), vec![5, 5, 5, 9]);
        assert_eq!(event_offsets(&[None, None]), vec![0, 0]);
    }
}
