//! Golden-output regression baselines for real TXT samples.
//!
//! For each real sample: corpus/baselines/txt/<sha256[:12]>.json holds a compact summary
//! (details, frame_count, first/last frame, bbox, event counts, record counts).
//!
//! * `DRONEDOG_WRITE_BASELINES=1 cargo test --no-default-features --test baselines_txt`
//!   (re)writes the baselines.
//! * Otherwise every existing baseline is compared; samples without a baseline are
//!   listed but do not fail (the corpus is still growing).

mod common;

use std::collections::BTreeMap;

use serde_json::{json, Map, Value as J};

use dronedog_core::frame::{ColumnRef, Frames};
use dronedog_core::model::{ParseResult, Value};

fn val(v: &Value) -> J {
    match v {
        Value::Null => J::Null,
        Value::Bool(b) => json!(b),
        Value::Int(i) => json!(i),
        Value::Float(f) => json!(f),
        Value::Str(s) => json!(s),
        Value::Map(m) => J::Object(m.iter().map(|(k, v)| (k.clone(), val(v))).collect()),
    }
}

fn row(f: &Frames, i: usize) -> J {
    let mut m = Map::new();
    for (name, c) in f.columns() {
        let v = match c {
            ColumnRef::Int(v) => json!(v[i]),
            ColumnRef::Float(v) => json!(v[i]),
            ColumnRef::Str(v) => json!(v[i]),
            ColumnRef::Bool(v) => json!(v[i]),
            ColumnRef::FloatList(v) => json!(v[i]),
        };
        m.insert(name.to_string(), v);
    }
    J::Object(m)
}

fn summary(rel: &str, sha: &str, size: u64, det: &dronedog_core::model::Detection, r: &ParseResult) -> J {
    let n = r.frames.len();
    let (mut la0, mut la1, mut lo0, mut lo1) = (f64::MAX, f64::MIN, f64::MAX, f64::MIN);
    let mut fixes = 0usize;
    for (la, lo) in r.frames.lat.iter().zip(&r.frames.lon) {
        if let (Some(a), Some(b)) = (la, lo) {
            fixes += 1;
            la0 = la0.min(*a);
            la1 = la1.max(*a);
            lo0 = lo0.min(*b);
            lo1 = lo1.max(*b);
        }
    }
    let bbox = if fixes > 0 {
        json!({"min_lat": la0, "max_lat": la1, "min_lon": lo0, "max_lon": lo1, "frames_with_fix": fixes})
    } else {
        J::Null
    };
    let mut ev: BTreeMap<String, usize> = BTreeMap::new();
    for e in &r.events {
        *ev.entry(format!("{}:{}", e.kind, e.severity.as_str())).or_default() += 1;
    }
    json!({
        "baseline_schema": 1,
        "generated_by": r.adapter_version,
        "source_path": rel,
        "sha256": sha,
        "size": size,
        "detect": {"kind": det.kind, "subtype": det.subtype, "version": det.version, "encrypted": det.encrypted},
        "adapter": r.adapter,
        "details": J::Object(r.details.entries.iter().map(|(k, v)| (k.clone(), val(v))).collect()),
        "frame_count": n,
        "records_total": r.records_total,
        "records_decoded": r.records_decoded,
        "records_failed": r.records_failed,
        "encrypted_unreadable": r.encrypted_unreadable,
        "first_frame": if n > 0 { row(&r.frames, 0) } else { J::Null },
        "last_frame": if n > 0 { row(&r.frames, n - 1) } else { J::Null },
        "bbox": bbox,
        "event_count": r.events.len(),
        "event_counts": ev,
        "warnings": r.warnings,
    })
}

/// Fields that may legitimately differ between runs (tool version, which duplicate
/// path was picked) are excluded from comparison.
fn comparable(mut j: J) -> J {
    if let J::Object(m) = &mut j {
        m.remove("generated_by");
        m.remove("source_path");
    }
    j
}

#[test]
fn txt_golden_baselines() {
    let samples = common::txt_samples();
    if samples.is_empty() {
        eprintln!("SKIP: no real DJI TXT samples under {}", common::corpus_root().display());
        return;
    }
    let root = common::corpus_root();
    let out_dir = root.join("baselines").join("txt");
    let write = std::env::var("DRONEDOG_WRITE_BASELINES").map(|v| v == "1").unwrap_or(false);
    if write {
        std::fs::create_dir_all(&out_dir).unwrap();
    }
    let (mut compared, mut written, mut missing) = (0, 0, Vec::new());
    for (path, sha) in &samples {
        let det = dronedog_core::detect::detect_path(path).unwrap();
        let r = dronedog_core::txt::parse_txt(path, None).unwrap();
        let rel = path
            .strip_prefix(&root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        let rel = rel.trim_start_matches("../").to_string();
        let size = std::fs::metadata(path).unwrap().len();
        let s = summary(&rel, sha, size, &det, &r);
        let file = out_dir.join(format!("{}.json", &sha[..12]));
        if write {
            std::fs::write(&file, serde_json::to_string_pretty(&s).unwrap() + "\n").unwrap();
            written += 1;
        } else if file.exists() {
            let old: J = serde_json::from_str(&std::fs::read_to_string(&file).unwrap()).unwrap();
            assert_eq!(
                comparable(old),
                comparable(s),
                "baseline mismatch for {} ({}); rerun with DRONEDOG_WRITE_BASELINES=1 only if the change is intended",
                rel,
                file.display()
            );
            compared += 1;
        } else {
            missing.push(rel);
        }
    }
    eprintln!(
        "baselines: {} samples, {compared} compared, {written} written, {} without baseline",
        samples.len(),
        missing.len()
    );
    for m in missing {
        eprintln!("  no baseline yet: {m}");
    }
}
