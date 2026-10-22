//! Normalized columnar frame builder (ARCHITECTURE.md C3, schema v1).
//!
//! Columns are built directly as typed vectors (no serde round trip). Every push
//! appends exactly one value to every column, so all columns always have equal length.

/// One normalized row, filled by an adapter and appended with [`Frames::push`].
/// `None` means "not present in the source" (never a fabricated 0).
#[derive(Debug, Clone, Default)]
pub struct Row {
    pub t_offset_ms: Option<i64>,
    pub t_utc: Option<String>,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub alt_rel_m: Option<f64>,
    pub alt_msl_m: Option<f64>,
    pub vel_n: Option<f64>,
    pub vel_e: Option<f64>,
    pub vel_d: Option<f64>,
    pub speed_h_ms: Option<f64>,
    pub roll: Option<f64>,
    pub pitch: Option<f64>,
    pub yaw: Option<f64>,
    pub gimbal_pitch: Option<f64>,
    pub gimbal_roll: Option<f64>,
    pub gimbal_yaw: Option<f64>,
    pub rc_aileron: Option<f64>,
    pub rc_elevator: Option<f64>,
    pub rc_throttle: Option<f64>,
    pub rc_rudder: Option<f64>,
    pub rc_mode_switch: Option<String>,
    pub flight_mode: Option<String>,
    pub gps_sats: Option<i64>,
    pub gps_health: Option<i64>,
    pub battery_pct: Option<f64>,
    pub battery_v: Option<f64>,
    pub battery_cells_v: Option<Vec<f64>>,
    pub battery_temp_c: Option<f64>,
    pub home_lat: Option<f64>,
    pub home_lon: Option<f64>,
    pub distance_m: Option<f64>,
    pub height_m: Option<f64>,
    pub motor_on: Option<bool>,
    pub is_flying: Option<bool>,
    pub vps_height_m: Option<f64>,
    pub distance_app_m: Option<f64>,
    pub rc_signal_up_pct: Option<f64>,
    pub rc_signal_down_pct: Option<f64>,
}

/// Borrowed view of one column, used by the Python binding to build lists.
pub enum ColumnRef<'a> {
    Int(&'a [Option<i64>]),
    Float(&'a [Option<f64>]),
    Str(&'a [Option<String>]),
    Bool(&'a [Option<bool>]),
    FloatList(&'a [Option<Vec<f64>>]),
}

impl ColumnRef<'_> {
    pub fn len(&self) -> usize {
        match self {
            ColumnRef::Int(v) => v.len(),
            ColumnRef::Float(v) => v.len(),
            ColumnRef::Str(v) => v.len(),
            ColumnRef::Bool(v) => v.len(),
            ColumnRef::FloatList(v) => v.len(),
        }
    }
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

macro_rules! frames_struct {
    ($( $name:ident : $ty:ty => $variant:ident ),* $(,)?) => {
        /// Columnar frame store, one `Vec` per C3 column.
        #[derive(Debug, Default, Clone)]
        pub struct Frames {
            $( pub $name: Vec<Option<$ty>>, )*
        }

        /// C3 column names in contract order.
        pub const C3_COLUMNS: &[&str] = &[ $( stringify!($name), )* ];

        impl Frames {
            pub fn with_capacity(n: usize) -> Self {
                Frames { $( $name: Vec::with_capacity(n), )* }
            }

            pub fn push(&mut self, r: Row) {
                $( self.$name.push(r.$name); )*
            }

            /// All columns in contract order.
            pub fn columns(&self) -> Vec<(&'static str, ColumnRef<'_>)> {
                vec![ $( (stringify!($name), ColumnRef::$variant(&self.$name)), )* ]
            }
        }
    };
}

frames_struct! {
    t_offset_ms: i64 => Int,
    t_utc: String => Str,
    lat: f64 => Float,
    lon: f64 => Float,
    alt_rel_m: f64 => Float,
    alt_msl_m: f64 => Float,
    vel_n: f64 => Float,
    vel_e: f64 => Float,
    vel_d: f64 => Float,
    speed_h_ms: f64 => Float,
    roll: f64 => Float,
    pitch: f64 => Float,
    yaw: f64 => Float,
    gimbal_pitch: f64 => Float,
    gimbal_roll: f64 => Float,
    gimbal_yaw: f64 => Float,
    rc_aileron: f64 => Float,
    rc_elevator: f64 => Float,
    rc_throttle: f64 => Float,
    rc_rudder: f64 => Float,
    rc_mode_switch: String => Str,
    flight_mode: String => Str,
    gps_sats: i64 => Int,
    gps_health: i64 => Int,
    battery_pct: f64 => Float,
    battery_v: f64 => Float,
    battery_cells_v: Vec<f64> => FloatList,
    battery_temp_c: f64 => Float,
    home_lat: f64 => Float,
    home_lon: f64 => Float,
    distance_m: f64 => Float,
    height_m: f64 => Float,
    motor_on: bool => Bool,
    is_flying: bool => Bool,
    vps_height_m: f64 => Float,
    distance_app_m: f64 => Float,
    rc_signal_up_pct: f64 => Float,
    rc_signal_down_pct: f64 => Float,
}

impl Frames {
    pub fn len(&self) -> usize {
        self.t_offset_ms.len()
    }
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// Validates a WGS84 coordinate pair. Returns `None` for no-fix sentinels:
/// (0,0), NaN/inf, or out-of-range values. Never returns (0,0).
pub fn valid_latlon(lat: f64, lon: f64) -> Option<(f64, f64)> {
    if !lat.is_finite() || !lon.is_finite() {
        return None;
    }
    if lat.abs() > 90.0 || lon.abs() > 180.0 {
        return None;
    }
    // DJI writes 0/0 (radians 0) when there is no fix. Treat anything within ~1 cm
    // of the null island as "no fix".
    if lat.abs() < 1e-7 && lon.abs() < 1e-7 {
        return None;
    }
    Some((lat, lon))
}

/// Finite f64 or None.
pub fn fin(x: f64) -> Option<f64> {
    if x.is_finite() {
        Some(x)
    } else {
        None
    }
}

/// Great-circle distance in metres (haversine, mean Earth radius 6 371 008.8 m).
pub fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    const R: f64 = 6_371_008.8;
    let (p1, p2) = (lat1.to_radians(), lat2.to_radians());
    let dp = (lat2 - lat1).to_radians();
    let dl = (lon2 - lon1).to_radians();
    let a = (dp / 2.0).sin().powi(2) + p1.cos() * p2.cos() * (dl / 2.0).sin().powi(2);
    2.0 * R * a.sqrt().min(1.0).asin()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn columns_stay_equal_length() {
        let mut f = Frames::default();
        f.push(Row::default());
        f.push(Row {
            lat: Some(1.0),
            battery_cells_v: Some(vec![3.8, 3.9]),
            ..Row::default()
        });
        let cols = f.columns();
        assert_eq!(cols.len(), C3_COLUMNS.len());
        assert!(cols.iter().all(|(_, c)| c.len() == 2));
    }

    #[test]
    fn c3_column_set_matches_contract() {
        let expected = [
            "t_offset_ms", "t_utc", "lat", "lon", "alt_rel_m", "alt_msl_m", "vel_n", "vel_e",
            "vel_d", "speed_h_ms", "roll", "pitch", "yaw", "gimbal_pitch", "gimbal_roll",
            "gimbal_yaw", "rc_aileron", "rc_elevator", "rc_throttle", "rc_rudder",
            "rc_mode_switch", "flight_mode", "gps_sats", "gps_health", "battery_pct",
            "battery_v", "battery_cells_v", "battery_temp_c", "home_lat", "home_lon",
            "distance_m", "height_m", "motor_on", "is_flying", "vps_height_m", "distance_app_m", "rc_signal_up_pct",
            "rc_signal_down_pct",
        ];
        assert_eq!(C3_COLUMNS, &expected);
    }

    #[test]
    fn null_island_is_no_fix() {
        assert_eq!(valid_latlon(0.0, 0.0), None);
        assert_eq!(valid_latlon(f64::NAN, 1.0), None);
        assert_eq!(valid_latlon(91.0, 1.0), None);
        assert_eq!(valid_latlon(46.5, 6.6), Some((46.5, 6.6)));
    }

    #[test]
    fn haversine_sane() {
        // 1 degree of latitude ~ 111.2 km
        let d = haversine_m(0.0, 10.0, 1.0, 10.0);
        assert!((d - 111_195.0).abs() < 50.0, "{d}");
    }
}
