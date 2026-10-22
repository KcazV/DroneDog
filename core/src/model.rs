//! Plain Rust data types for the C1 contract (ARCHITECTURE.md). No Python here;
//! `lib.rs` converts these into dicts/lists.

use crate::frame::Frames;

/// A loosely typed scalar used for `details` values (C2) and other small maps.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Map(Vec<(String, Value)>),
}

impl Value {
    pub fn opt_str(s: &str) -> Value {
        let t = s.trim();
        if t.is_empty() {
            Value::Null
        } else {
            Value::Str(t.to_string())
        }
    }
    pub fn float(x: f64) -> Value {
        if x.is_finite() {
            Value::Float(x)
        } else {
            Value::Null
        }
    }
}

/// The C2 key set, in contract order. Every ParseResult carries all of these keys
/// (null when unknown). Extra keys may follow.
pub const C2_KEYS: &[&str] = &[
    "aircraft_model",
    "aircraft_name",
    "aircraft_sn",
    "fc_sn",
    "camera_sn",
    "rc_sn",
    "battery_sn",
    "app_platform",
    "app_version",
    "log_version",
    "start_time_utc",
    "duration_s",
    "total_distance_m",
    "max_height_m",
    "max_h_speed_ms",
    "max_v_speed_ms",
    "home_lat",
    "home_lon",
    "city",
    "area",
    "sub_street",
    "firmware_version",
    "record_line_count",
];

/// Ordered key/value details map. Starts with every C2 key set to Null.
#[derive(Debug, Clone)]
pub struct Details {
    pub entries: Vec<(String, Value)>,
}

impl Default for Details {
    fn default() -> Self {
        Details {
            entries: C2_KEYS
                .iter()
                .map(|k| (k.to_string(), Value::Null))
                .collect(),
        }
    }
}

impl Details {
    pub fn set(&mut self, key: &str, v: Value) {
        if let Some(e) = self.entries.iter_mut().find(|(k, _)| k == key) {
            e.1 = v;
        } else {
            self.entries.push((key.to_string(), v));
        }
    }
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.entries.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Info,
    Warn,
    Critical,
}

impl Severity {
    pub fn as_str(&self) -> &'static str {
        match self {
            Severity::Info => "info",
            Severity::Warn => "warn",
            Severity::Critical => "critical",
        }
    }
}

#[derive(Debug, Clone)]
pub struct Event {
    pub t_offset_ms: i64,
    pub kind: String,
    pub severity: Severity,
    pub message: String,
    pub raw: Option<String>,
}

/// C1 ParseResult.
#[derive(Debug)]
pub struct ParseResult {
    pub adapter: String,
    pub adapter_version: String,
    pub details: Details,
    pub frames: Frames,
    pub events: Vec<Event>,
    pub records_total: i64,
    pub records_decoded: i64,
    pub records_failed: i64,
    pub warnings: Vec<String>,
    pub encrypted_unreadable: bool,
}

/// C1 detect() result.
#[derive(Debug, Clone, PartialEq)]
pub struct Detection {
    pub kind: &'static str,
    pub subtype: Option<String>,
    pub version: Option<i64>,
    pub encrypted: Option<bool>,
    pub confidence: f64,
    pub reasons: Vec<String>,
}

impl Detection {
    pub fn unknown(reasons: Vec<String>) -> Self {
        Detection {
            kind: "unknown",
            subtype: None,
            version: None,
            encrypted: None,
            confidence: 0.0,
            reasons,
        }
    }
}

/// Crate version plus the linked dji-log-parser version (pinned with `=` in Cargo.toml).
pub const CRATE_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const DJI_LOG_PARSER_VERSION: &str = "0.5.7";

pub fn version_string() -> String {
    format!(
        "dronedog_core {} (dji-log-parser {})",
        CRATE_VERSION, DJI_LOG_PARSER_VERSION
    )
}
