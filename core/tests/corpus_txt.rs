//! Integration tests against real DJI TXT logs found in the corpus.
//! Skips cleanly (passes with a note) when no samples are present.

mod common;

use std::time::Instant;

use dronedog_core::frame::{ColumnRef, C3_COLUMNS};
use dronedog_core::model::C2_KEYS;
use dronedog_core::txt;

#[test]
fn real_txt_samples_parse_and_hold_invariants() {
    let samples = common::txt_samples();
    if samples.is_empty() {
        eprintln!("SKIP: no real DJI TXT samples under {}", common::corpus_root().display());
        return;
    }
    for (path, sha) in &samples {
        let det = dronedog_core::detect::detect_path(path).expect("detect");
        assert_eq!(det.kind, "dji_txt", "{}: {:?}", path.display(), det);
        let v = det.version.unwrap();

        let t0 = Instant::now();
        let r = txt::parse_txt(path, None).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        let ms = t0.elapsed().as_millis();

        // C2 keys all present.
        for k in C2_KEYS {
            assert!(r.details.get(k).is_some(), "missing details key {k}");
        }
        // C3 columns all present and equal length.
        let cols = r.frames.columns();
        assert_eq!(cols.len(), C3_COLUMNS.len());
        let n = r.frames.len();
        for (name, c) in &cols {
            assert_eq!(c.len(), n, "column {name} length");
        }
        // Never (0,0).
        for (la, lo) in r.frames.lat.iter().zip(&r.frames.lon) {
            match (la, lo) {
                (Some(a), Some(b)) => assert!(!(a.abs() < 1e-7 && b.abs() < 1e-7)),
                (None, None) => {}
                _ => panic!("lat/lon nullness differs"),
            }
        }
        if let ColumnRef::Float(rc) = &cols.iter().find(|(n, _)| *n == "rc_throttle").unwrap().1 {
            assert!(rc.iter().flatten().all(|x| (-1.0..=1.0).contains(x)));
        }
        if v >= 13 {
            assert!(r.encrypted_unreadable);
            assert_eq!(n, 0);
            assert!(r.warnings.iter().any(|w| w.contains("encrypted")));
        } else {
            assert!(!r.encrypted_unreadable);
            assert!(n > 0, "{}: no frames", path.display());
            assert!(r.frames.lat.iter().any(|x| x.is_some()), "no GPS fix in whole log");
        }
        eprintln!(
            "OK v{v:<2} {} frames={n:<6} events={:<4} records={} failed={} parse={ms}ms  {}",
            &sha[..12],
            r.events.len(),
            r.records_total,
            r.records_failed,
            path.display()
        );
        if std::env::var("DRONEDOG_INSPECT").is_ok() {
            for i in [0usize, 1, 2, n / 2, n.saturating_sub(1)] {
                if i < n {
                    eprintln!(
                        "  [{i}] t={:?} utc={:?} lat={:?} lon={:?} h={:?} msl={:?} v=({:?},{:?},{:?}) mode={:?} sw={:?} gps={:?}/{:?} batt={:?}% {:?}V cells={:?} temp={:?} rc_t={:?} dist={:?} motor={:?} fly={:?} gimbal_p={:?}",
                        r.frames.t_offset_ms[i], r.frames.t_utc[i], r.frames.lat[i], r.frames.lon[i],
                        r.frames.alt_rel_m[i], r.frames.alt_msl_m[i], r.frames.vel_n[i], r.frames.vel_e[i],
                        r.frames.vel_d[i], r.frames.flight_mode[i], r.frames.rc_mode_switch[i],
                        r.frames.gps_sats[i], r.frames.gps_health[i], r.frames.battery_pct[i],
                        r.frames.battery_v[i], r.frames.battery_cells_v[i], r.frames.battery_temp_c[i],
                        r.frames.rc_throttle[i], r.frames.distance_m[i], r.frames.motor_on[i],
                        r.frames.is_flying[i], r.frames.gimbal_pitch[i]
                    );
                }
            }
            for e in r.events.iter().take(40) {
                eprintln!("  ev {:>8} {:<22} {:<8} {}", e.t_offset_ms, e.kind, e.severity.as_str(), e.message);
            }
            for w in &r.warnings {
                eprintln!("  warn: {w}");
            }
            eprintln!("  details: {:?}", r.details.entries);
        }
    }
    eprintln!("tested {} real TXT sample(s)", samples.len());
}
