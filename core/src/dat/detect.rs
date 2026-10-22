//! Best-effort DJI DAT detector (aircraft FLYnnn.DAT and controller DATs).
//!
//! Isolated here so the DAT workstream can refine it without touching TXT code.
//! Evidence used (public knowledge; DatCon `Files/DatFile.java` is the spec source):
//!
//! * ASCII `BUILD` at offset 0x10 (e.g. `BUILD Apr  8 2016 18:22:32`): DatCon's
//!   `isDatFile` test.
//! * ASCII `DJI_LOG_V3` at offset 0xF2 (242): DatCon selects `DatFileV3`, records start
//!   at 0x100. Without it DatCon uses `DatFileV1` with records at 0x80 ("legacy").
//! * ASCII `LOGH` at offset 0: DatCon reports "Probably an encrypted .DAT".
//! * zlib header `78 9C` at offset 0 with a .DAT name: DJI Assistant export container
//!   (DatCon `DJIAssistantFile.isDJIDat`).
//! * Records framed as `0x55, len(10-bit LE), ...` chained back-to-back: we walk the
//!   chain from the expected record start and count consecutive hits.
//! * File name `FLYnnn.DAT` (optionally with a timestamp prefix).
//!
//! Encryption state of plain-header DATs is NOT decided here (`encrypted: None`):
//! record payloads in both V1 and V3 are XOR-obfuscated per record, which is a
//! published scheme, but newer aircraft additionally encrypt; the header alone does not
//! tell them apart.

use crate::detect::{hex_preview, u32le};
use crate::model::Detection;

pub const BUILD_OFFSET: usize = 0x10;
pub const V3_MARKER_OFFSET: usize = 0xF2;
pub const V1_RECORDS_START: usize = 0x80;
pub const V3_RECORDS_START: usize = 0x100;
const MIN_CHAIN: usize = 6;

#[derive(Debug, Default, Clone)]
pub struct DatHeader {
    pub build_string: Option<String>,
    pub v3_marker: bool,
    pub v3_marker_elsewhere: Option<usize>,
    pub logh_magic: bool,
    pub zlib_magic: bool,
    pub words: [Option<u32>; 3],
}

fn ascii_until_nul(b: &[u8], off: usize, max: usize) -> Option<String> {
    let s = b.get(off..)?;
    let s = &s[..s.len().min(max)];
    let end = s.iter().position(|&c| c == 0).unwrap_or(s.len());
    let s = &s[..end];
    if s.iter().all(|c| c.is_ascii_graphic() || *c == b' ') && !s.is_empty() {
        Some(String::from_utf8_lossy(s).trim_end().to_string())
    } else {
        None
    }
}

fn find(hay: &[u8], needle: &[u8], limit: usize) -> Option<usize> {
    let hay = &hay[..hay.len().min(limit)];
    if hay.len() < needle.len() {
        return None;
    }
    hay.windows(needle.len()).position(|w| w == needle)
}

pub fn read_header(head: &[u8]) -> DatHeader {
    let mut h = DatHeader::default();
    if head.get(BUILD_OFFSET..BUILD_OFFSET + 5) == Some(b"BUILD") {
        h.build_string = ascii_until_nul(head, BUILD_OFFSET, 32);
    }
    h.v3_marker = head.get(V3_MARKER_OFFSET..V3_MARKER_OFFSET + 10) == Some(b"DJI_LOG_V3");
    if !h.v3_marker {
        h.v3_marker_elsewhere = find(head, b"DJI_LOG_V3", 1024);
    }
    h.logh_magic = head.get(0..4) == Some(b"LOGH");
    h.zlib_magic = head.get(0..2) == Some(&[0x78, 0x9C]);
    h.words = [u32le(head, 0), u32le(head, 4), u32le(head, 8)];
    h
}

/// Length of a 0x55 frame at `p`: 10-bit little-endian length in bytes 1..2.
fn frame_len(b: &[u8], p: usize) -> Option<usize> {
    if *b.get(p)? != 0x55 {
        return None;
    }
    let lo = *b.get(p + 1)? as usize;
    let hi = (*b.get(p + 2)? & 0x03) as usize;
    Some(lo | (hi << 8))
}

/// Walk back-to-back 0x55 frames from `start`. Returns (count, first lengths).
pub fn walk_chain(b: &[u8], start: usize) -> (usize, Vec<usize>) {
    let mut p = start;
    let mut n = 0;
    let mut lens = Vec::new();
    while let Some(l) = frame_len(b, p) {
        if l < 8 {
            break;
        }
        n += 1;
        if lens.len() < 6 {
            lens.push(l);
        }
        p += l;
        if p >= b.len() {
            break;
        }
    }
    (n, lens)
}

/// Best chain starting at any 0x55 in [from, to).
fn best_chain(b: &[u8], from: usize, to: usize) -> Option<(usize, usize, Vec<usize>)> {
    let to = to.min(b.len());
    let mut best: Option<(usize, usize, Vec<usize>)> = None;
    for p in from..to {
        if b[p] != 0x55 {
            continue;
        }
        let (n, lens) = walk_chain(b, p);
        if best.as_ref().map_or(true, |(bn, _, _)| n > *bn) {
            best = Some((n, p, lens));
        }
    }
    best
}

fn dat_name_hint(name: &str) -> (bool, bool) {
    let up = name.to_ascii_uppercase();
    let is_dat = up.ends_with(".DAT");
    // FLYnnn.DAT, or <timestamp>_FLYnnn.DAT as written by DJI Assistant.
    let fly = is_dat
        && up
            .rfind("FLY")
            .map(|i| {
                let digits = &up[i + 3..up.len() - 4];
                !digits.is_empty() && digits.len() <= 4 && digits.bytes().all(|c| c.is_ascii_digit())
            })
            .unwrap_or(false);
    (is_dat, fly)
}

/// Returns `Some(detection)` (kind "dji_dat") when there is content evidence for a DAT,
/// `None` otherwise. File name alone is never sufficient.
pub fn detect_dat(head: &[u8], file_len: u64, file_name: &str) -> Option<Detection> {
    if head.is_empty() {
        return None;
    }
    let h = read_header(head);
    let (is_dat_name, fly_name) = dat_name_hint(file_name);
    let mut reasons = Vec::new();
    let mut score: f64 = 0.0;
    let mut content_evidence = false;

    if let Some(b) = &h.build_string {
        reasons.push(format!("DAT: ASCII '{b}' at offset 0x10 (DatCon BUILD check)"));
        score += 0.35;
        content_evidence = true;
    }
    if h.v3_marker {
        reasons.push("DAT: ASCII 'DJI_LOG_V3' at offset 0xF2 (DatCon V3 layout, records at 0x100)".into());
        score += 0.15;
        content_evidence = true;
    } else if let Some(off) = h.v3_marker_elsewhere {
        reasons.push(format!(
            "DAT: ASCII 'DJI_LOG_V3' at unexpected offset 0x{off:X} (expected 0xF2)"
        ));
        score += 0.05;
    }
    if h.logh_magic {
        reasons.push("DAT: ASCII 'LOGH' at offset 0 (DatCon: 'Probably an encrypted .DAT')".into());
        score += 0.3;
        content_evidence = true;
    }
    if h.zlib_magic && is_dat_name {
        reasons.push("DAT: zlib header 78 9C at offset 0 with .DAT name (DJI Assistant export container per DatCon)".into());
        score += 0.3;
        content_evidence = true;
    }

    // Record framing.
    let expected_start = if h.v3_marker {
        V3_RECORDS_START
    } else {
        V1_RECORDS_START
    };
    let chain = best_chain(head, 0x40.min(head.len()), 0x800);
    let mut chained = false;
    if let Some((n, p, lens)) = &chain {
        if *n >= MIN_CHAIN || (*n >= 3 && p + lens.iter().sum::<usize>() >= head.len()) {
            chained = true;
            reasons.push(format!(
                "DAT: {n} back-to-back 0x55-framed records from offset 0x{p:X} within the first {} bytes (first lengths {:?})",
                head.len(),
                lens
            ));
            score += 0.45;
            content_evidence = true;
            if *p == expected_start {
                reasons.push(format!(
                    "DAT: first record exactly at expected start 0x{expected_start:X}"
                ));
                score += 0.05;
            }
        } else if *n > 0 {
            reasons.push(format!(
                "DAT: only {n} chained 0x55 frame(s) from 0x{p:X} (need {MIN_CHAIN}); framing not confirmed"
            ));
        }
    }

    if fly_name {
        reasons.push(format!("DAT: filename '{file_name}' matches FLYnnn.DAT"));
        score += 0.15;
    } else if is_dat_name {
        reasons.push(format!("DAT: filename '{file_name}' has .DAT extension"));
        score += 0.05;
    }

    if !content_evidence {
        return None;
    }

    if let [Some(a), Some(b), Some(c)] = h.words {
        reasons.push(format!(
            "DAT: header words u32 LE @0x00/0x04/0x08 = {a}, {b}, {c} (meaning not yet documented)"
        ));
    }
    reasons.push(format!("DAT: file size {file_len} bytes"));

    let (subtype, version, encrypted) = if h.logh_magic {
        ("dat_encrypted", None, Some(true))
    } else if h.zlib_magic && is_dat_name && h.build_string.is_none() {
        ("dat_assistant_zlib", None, None)
    } else if h.build_string.is_some() && h.v3_marker && chained {
        ("dat_v3", Some(3), None)
    } else if h.build_string.is_some() && !h.v3_marker && chained {
        ("dat_legacy", Some(1), None)
    } else {
        reasons.push(format!(
            "DAT: markers incomplete (BUILD={}, V3={}, framing={}); layout unsure. head = [{}]",
            h.build_string.is_some(),
            h.v3_marker,
            chained,
            hex_preview(head, 16)
        ));
        ("dat_unknown", None, None)
    };
    if encrypted.is_none() {
        reasons.push(
            "DAT: encryption state not determinable from the header; left null for the DAT decoder".into(),
        );
    }

    Some(Detection {
        kind: "dji_dat",
        subtype: Some(subtype.to_string()),
        version,
        encrypted,
        confidence: score.clamp(0.0, 0.99),
        reasons,
    })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    fn put_chain(b: &mut [u8], mut p: usize, lens: &[usize]) {
        for &l in lens {
            if p + 3 > b.len() {
                break;
            }
            b[p] = 0x55;
            b[p + 1] = (l & 0xFF) as u8;
            b[p + 2] = ((l >> 8) & 0x03) as u8;
            p += l;
        }
    }

    pub(crate) fn synth_dat_v3() -> Vec<u8> {
        let mut b = vec![0u8; 4096];
        b[0..4].copy_from_slice(&0x10u32.to_le_bytes());
        b[4..8].copy_from_slice(&3u32.to_le_bytes());
        b[0x10..0x10 + 26].copy_from_slice(b"BUILD Nov 16 2017 10:30:14");
        b[V3_MARKER_OFFSET..V3_MARKER_OFFSET + 10].copy_from_slice(b"DJI_LOG_V3");
        put_chain(&mut b, V3_RECORDS_START, &[0x29, 0x2c, 0x39, 0x39, 0x44, 0x20, 0x30, 0x40, 0x55, 0x60]);
        b
    }

    fn synth_dat_legacy() -> Vec<u8> {
        let mut b = vec![0x53u8; 4096];
        b[0..0x80].fill(0);
        b[0x10..0x10 + 26].copy_from_slice(b"BUILD Apr  8 2016 18:22:32");
        put_chain(&mut b, V1_RECORDS_START, &[0x2c, 0x35, 0x34, 0x26, 0x2c, 0x35, 0x34, 0x26]);
        b
    }

    #[test]
    fn v3_detected() {
        let b = synth_dat_v3();
        let d = crate::detect::detect_bytes(&b, "20-07-10-08-31-09_FLY256.DAT");
        assert_eq!(d.kind, "dji_dat");
        assert_eq!(d.subtype.as_deref(), Some("dat_v3"));
        assert_eq!(d.version, Some(3));
        assert!(d.confidence > 0.9, "{d:?}");
        assert!(d.reasons.iter().any(|r| r.contains("0x100")));
    }

    #[test]
    fn legacy_detected() {
        let b = synth_dat_legacy();
        let d = crate::detect::detect_bytes(&b, "FLY002.DAT");
        assert_eq!(d.kind, "dji_dat");
        assert_eq!(d.subtype.as_deref(), Some("dat_legacy"));
    }

    #[test]
    fn chain_without_build_is_unknown_subtype() {
        let mut b = vec![0u8; 4096];
        put_chain(&mut b, 0x200, &[0x30; 12]);
        let d = crate::detect::detect_bytes(&b, "FLY010.DAT");
        assert_eq!(d.kind, "dji_dat");
        assert_eq!(d.subtype.as_deref(), Some("dat_unknown"));
    }

    #[test]
    fn logh_is_encrypted() {
        let mut b = vec![0u8; 512];
        b[0..4].copy_from_slice(b"LOGH");
        let d = crate::detect::detect_bytes(&b, "FLY001.DAT");
        assert_eq!(d.subtype.as_deref(), Some("dat_encrypted"));
        assert_eq!(d.encrypted, Some(true));
    }

    #[test]
    fn name_alone_is_not_enough() {
        let b = vec![0u8; 4096];
        let d = crate::detect::detect_bytes(&b, "FLY001.DAT");
        assert_eq!(d.kind, "unknown");
    }

    #[test]
    fn name_hints() {
        assert_eq!(dat_name_hint("FLY001.DAT"), (true, true));
        assert_eq!(dat_name_hint("20-07-10-08-31-09_FLY256.DAT"), (true, true));
        assert_eq!(dat_name_hint("fly12.dat"), (true, true));
        assert_eq!(dat_name_hint("FLY.DAT"), (true, false));
        assert_eq!(dat_name_hint("x.txt"), (false, false));
    }
}
