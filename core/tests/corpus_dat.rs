//! Detector + stub parser smoke test on real DAT files found in the corpus.
//! Skips cleanly when none are present.

mod common;

#[test]
fn real_dat_samples_detect_and_stub_parse() {
    let samples = common::dat_samples();
    if samples.is_empty() {
        eprintln!("SKIP: no FLY*.DAT samples under {}", common::corpus_root().display());
        return;
    }
    for p in &samples {
        let d = dronedog_core::detect::detect_path(p).expect("detect");
        assert_eq!(d.kind, "dji_dat", "{}: {:?}", p.display(), d);
        assert!(!d.reasons.is_empty());
        let r = dronedog_core::dat::parse_dat(p).expect("parse_dat stub");
        assert!(r.frames.is_empty());
        assert!(!r.encrypted_unreadable);
        assert!(r.warnings.iter().any(|w| w == "DAT decoding not yet implemented"));
        eprintln!(
            "OK {:<14} conf={:.2} v={:?} {}",
            d.subtype.as_deref().unwrap_or("-"),
            d.confidence,
            d.version,
            p.display()
        );
    }
    eprintln!("tested {} real DAT sample(s)", samples.len());
}
