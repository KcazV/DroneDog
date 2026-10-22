//! Shared helpers for corpus-backed integration tests.
#![allow(dead_code)]

use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

/// Corpus root: $DRONEDOG_CORPUS or <repo>/corpus.
pub fn corpus_root() -> PathBuf {
    if let Ok(p) = std::env::var("DRONEDOG_CORPUS") {
        return PathBuf::from(p);
    }
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("corpus")
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>, depth: usize) {
    if depth > 12 {
        return;
    }
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let p = e.path();
        let Ok(ft) = e.file_type() else { continue };
        if ft.is_dir() {
            walk(&p, out, depth + 1);
        } else if ft.is_file() {
            out.push(p);
        }
    }
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Real DJI TXT samples: every *.txt under corpus/raw and corpus/_downloads that
/// either is named DJIFlightRecord*.txt or detects as dji_txt. Deduplicated by SHA-256,
/// sorted by hash so runs are deterministic. Returns (path, sha256).
pub fn txt_samples() -> Vec<(PathBuf, String)> {
    let root = corpus_root();
    let mut files = Vec::new();
    for sub in ["raw", "_downloads", "samples"] {
        walk(&root.join(sub), &mut files, 0);
    }
    let mut seen = std::collections::BTreeMap::new();
    for p in files {
        let name = p
            .file_name()
            .map(|s| s.to_string_lossy().to_ascii_lowercase())
            .unwrap_or_default();
        if !name.ends_with(".txt") {
            continue;
        }
        let named = name.starts_with("djiflightrecord");
        if !named {
            match dronedog_core::detect::detect_path(&p) {
                Ok(d) if d.kind == "dji_txt" => {}
                _ => continue,
            }
        }
        let Ok(bytes) = std::fs::read(&p) else { continue };
        let h = sha256_hex(&bytes);
        seen.entry(h).or_insert(p);
    }
    seen.into_iter().map(|(h, p)| (p, h)).collect()
}

/// DAT-looking files (by name) for detector smoke tests.
pub fn dat_samples() -> Vec<PathBuf> {
    let root = corpus_root();
    let mut files = Vec::new();
    for sub in ["raw", "_downloads", "samples"] {
        walk(&root.join(sub), &mut files, 0);
    }
    files.retain(|p| {
        p.extension()
            .map(|e| e.to_string_lossy().eq_ignore_ascii_case("dat"))
            .unwrap_or(false)
            && p.file_name()
                .map(|n| n.to_string_lossy().to_ascii_uppercase().contains("FLY"))
                .unwrap_or(false)
    });
    files.sort();
    files
}
