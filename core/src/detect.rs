//! Format detection (C1 `detect`). Reads only the header bytes it needs, never the
//! whole file, never writes. Never panics on garbage: all reads are bounds-checked and
//! anything that does not match a known layout comes back as kind "unknown".
//!
//! DJI app TXT layout (from dji-log-parser 0.5.7 `layout/prefix.rs`, `lib.rs`):
//!
//! ```text
//! 0x00 u64 LE  detail_offset   (v<12: offset of Details block; v13+: offset of records)
//! 0x08 u16 LE  detail_length
//! 0x0A u8      log version
//! 0x0B u8      unknown
//! 0x0C u64     encrypt magic version
//! 0x14 [80]    reserved            -> 100-byte prefix (v1-v5 use only the first 12 bytes)
//! ```
//! v1-v11: Details at `detail_offset` (usually the last ~400 bytes of the file).
//! v12:    Details (436 bytes) right after the 100-byte prefix, records after that.
//! v13+:   Auxiliary "Info" block (magic 0x00, u16 size, XOR'd Details) at 0x64, then an
//!         Auxiliary "Version" block (magic 0x01, u16 size); records start at `detail_offset`
//!         and are AES encrypted (need DJI keychains).

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

use crate::dat;
use crate::model::Detection;

/// Bytes read from the start of the file for detection.
pub const HEAD_LEN: usize = 4096;

/// Abstract random-access byte source so detection can run on files and on
/// in-memory buffers (unit tests) with the same code.
pub trait ByteSource {
    fn len(&self) -> u64;
    /// Read up to `n` bytes at `off`. Short or empty result if past EOF.
    fn read_at(&mut self, off: u64, n: usize) -> Vec<u8>;
}

impl ByteSource for &[u8] {
    fn len(&self) -> u64 {
        <[u8]>::len(self) as u64
    }
    fn read_at(&mut self, off: u64, n: usize) -> Vec<u8> {
        let l = <[u8]>::len(self) as u64;
        if off >= l {
            return Vec::new();
        }
        let start = off as usize;
        let end = start.saturating_add(n).min(l as usize);
        self[start..end].to_vec()
    }
}

pub struct FileSource {
    f: File,
    len: u64,
}

impl FileSource {
    pub fn open(path: &Path) -> io::Result<Self> {
        let f = File::open(path)?;
        let len = f.metadata()?.len();
        Ok(FileSource { f, len })
    }
}

impl ByteSource for FileSource {
    fn len(&self) -> u64 {
        self.len
    }
    fn read_at(&mut self, off: u64, n: usize) -> Vec<u8> {
        if off >= self.len {
            return Vec::new();
        }
        let n = (n as u64).min(self.len - off) as usize;
        let mut buf = vec![0u8; n];
        if self.f.seek(SeekFrom::Start(off)).is_err() {
            return Vec::new();
        }
        let mut got = 0;
        while got < n {
            match self.f.read(&mut buf[got..]) {
                Ok(0) => break,
                Ok(k) => got += k,
                Err(ref e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
        buf.truncate(got);
        buf
    }
}

pub(crate) fn u16le(b: &[u8], off: usize) -> Option<u16> {
    b.get(off..off + 2).map(|s| u16::from_le_bytes([s[0], s[1]]))
}
pub(crate) fn u32le(b: &[u8], off: usize) -> Option<u32> {
    b.get(off..off + 4)
        .map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
}
pub(crate) fn u64le(b: &[u8], off: usize) -> Option<u64> {
    b.get(off..off + 8).map(|s| {
        let mut a = [0u8; 8];
        a.copy_from_slice(s);
        u64::from_le_bytes(a)
    })
}
pub(crate) fn i64le(b: &[u8], off: usize) -> Option<i64> {
    u64le(b, off).map(|v| v as i64)
}

pub(crate) fn hex_preview(b: &[u8], n: usize) -> String {
    b.iter()
        .take(n)
        .map(|x| format!("{x:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Highest TXT log version dji-log-parser 0.5.7 is known to handle.
pub const TXT_MAX_KNOWN_VERSION: u8 = 14;
/// First TXT version whose records are AES encrypted (need DJI keychains).
pub const TXT_FIRST_ENCRYPTED_VERSION: u8 = 13;

const TS_MIN_MS: i64 = 1_262_304_000_000; // 2010-01-01
const TS_MAX_MS: i64 = 4_102_444_800_000; // 2100-01-01

fn plausible_ts_ms(ts: i64) -> bool {
    (TS_MIN_MS..TS_MAX_MS).contains(&ts)
}

fn fmt_ts(ts: i64) -> String {
    chrono::DateTime::from_timestamp_millis(ts)
        .map(|d| d.format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string())
        .unwrap_or_else(|| ts.to_string())
}

fn txt_filename_hint(name: &str) -> bool {
    let n = name.to_ascii_lowercase();
    n.starts_with("djiflightrecord") && n.ends_with(".txt")
}

/// TXT header check. Returns a detection with kind "dji_txt" when the prefix is
/// structurally consistent, otherwise `Err(reasons)` explaining why not.
pub fn detect_txt<S: ByteSource>(src: &mut S, file_name: &str) -> Result<Detection, Vec<String>> {
    let len = src.len();
    let mut reasons = Vec::new();
    if len < 12 {
        reasons.push(format!(
            "TXT: file is {len} bytes, smaller than the 12-byte minimum DJI TXT prefix"
        ));
        return Err(reasons);
    }
    let head = src.read_at(0, 100);
    let (Some(detail_offset), Some(detail_length)) = (u64le(&head, 0), u16le(&head, 8)) else {
        reasons.push("TXT: could not read the 12-byte prefix".into());
        return Err(reasons);
    };
    let version = head[10];
    reasons.push(format!(
        "TXT prefix: detail_offset={detail_offset} (u64 LE @0x00), detail_length={detail_length} (u16 LE @0x08), version byte={version} (@0x0A), byte@0x0B=0x{:02x}",
        head[11]
    ));

    if version == 0 || version > 30 {
        reasons.push(format!(
            "TXT: version byte {version} is outside the plausible range 1..={TXT_MAX_KNOWN_VERSION}"
        ));
        return Err(reasons);
    }

    let mut score: f64 = 0.0;
    let name_hint = txt_filename_hint(file_name);

    if version < 12 {
        let prefix_size: u64 = if version < 6 { 12 } else { 100 };
        if len < prefix_size {
            reasons.push(format!(
                "TXT: file shorter ({len} B) than the {prefix_size}-byte prefix for v{version}"
            ));
            return Err(reasons);
        }
        if detail_offset < prefix_size || detail_offset >= len {
            reasons.push(format!(
                "TXT: detail_offset {detail_offset} not inside the file (prefix {prefix_size} B, file {len} B)"
            ));
            return Err(reasons);
        }
        let tail = len - detail_offset;
        reasons.push(format!(
            "TXT v{version}: records span 0x{prefix_size:X}..0x{detail_offset:X}, Details block {tail} B at end of file"
        ));
        score += 0.4;
        let det = src.read_at(detail_offset, 99);
        match i64le(&det, 91) {
            Some(ts) if plausible_ts_ms(ts) => {
                reasons.push(format!(
                    "TXT: Details start_time @0x{:X} = {ts} ms -> {} (plausible)",
                    detail_offset + 91,
                    fmt_ts(ts)
                ));
                score += 0.4;
            }
            Some(ts) => reasons.push(format!(
                "TXT: Details start_time @0x{:X} = {ts} is not a plausible epoch-ms timestamp",
                detail_offset + 91
            )),
            None => reasons.push(format!(
                "TXT: Details block truncated ({} B readable at 0x{detail_offset:X}); start_time not readable",
                det.len()
            )),
        }
    } else if version == 12 {
        if len < 536 {
            reasons.push(format!(
                "TXT v12: file is {len} B, shorter than prefix + 436-byte Details (536 B)"
            ));
            return Err(reasons);
        }
        score += 0.4;
        let det = src.read_at(100, 99);
        match i64le(&det, 91) {
            Some(ts) if plausible_ts_ms(ts) => {
                reasons.push(format!(
                    "TXT v12: Details start_time @0xBF = {ts} ms -> {} (plausible)",
                    fmt_ts(ts)
                ));
                score += 0.4;
            }
            _ => reasons.push("TXT v12: Details start_time @0xBF not plausible".into()),
        }
    } else {
        // v13+: Auxiliary Info block at 0x64, then Auxiliary Version block.
        let aux = src.read_at(100, 3);
        if aux.len() < 3 {
            reasons.push(format!(
                "TXT v{version}: file truncated before the Auxiliary block at 0x64"
            ));
            return Err(reasons);
        }
        if aux[0] != 0 {
            reasons.push(format!(
                "TXT v{version}: expected Auxiliary Info magic 0x00 at 0x64, found 0x{:02x}",
                aux[0]
            ));
            return Err(reasons);
        }
        let info_len = u16::from_le_bytes([aux[1], aux[2]]) as u64;
        let ver_off = 103 + info_len;
        if ver_off >= len {
            reasons.push(format!(
                "TXT v{version}: Auxiliary Info length {info_len} runs past end of file ({len} B)"
            ));
            return Err(reasons);
        }
        reasons.push(format!(
            "TXT v{version}: Auxiliary Info block (magic 0x00) at 0x64, {info_len} B"
        ));
        score += 0.4;
        let vb = src.read_at(ver_off, 6);
        if vb.len() >= 6 && vb[0] == 1 {
            let vsize = u16::from_le_bytes([vb[1], vb[2]]) as u64;
            let aux_ver = u16::from_le_bytes([vb[3], vb[4]]);
            let dept = vb[5];
            let rec_off = ver_off + 3 + vsize;
            reasons.push(format!(
                "TXT v{version}: Auxiliary Version block (magic 0x01) at 0x{ver_off:X}: size={vsize}, aux_version={aux_ver}, department={dept}; records expected at 0x{rec_off:X}"
            ));
            if detail_offset == rec_off || detail_offset == 0 {
                reasons.push(format!(
                    "TXT v{version}: prefix detail_offset {detail_offset} agrees with record start"
                ));
                score += 0.4;
            } else {
                reasons.push(format!(
                    "TXT v{version}: prefix detail_offset {detail_offset} != computed record start {rec_off}"
                ));
                score += 0.2;
            }
        } else {
            reasons.push(format!(
                "TXT v{version}: no Auxiliary Version block (magic 0x01) at 0x{ver_off:X}"
            ));
        }
    }

    if matches!(detail_length, 400 | 436) {
        reasons.push(format!(
            "TXT: detail_length {detail_length} matches a known Details size"
        ));
        score += 0.1;
    }
    if name_hint {
        reasons.push(format!(
            "filename '{file_name}' matches DJIFlightRecord_*.txt"
        ));
        score += 0.1;
    }

    // Require more than a coincidental prefix: structural fit plus at least one
    // independent confirmation (timestamp / aux block / size / filename).
    if score < 0.5 {
        reasons.push(format!(
            "TXT: evidence too weak (score {score:.2}) to call this a DJI TXT log"
        ));
        return Err(reasons);
    }

    let encrypted = version >= TXT_FIRST_ENCRYPTED_VERSION;
    if encrypted {
        reasons.push(format!(
            "TXT v{version} >= {TXT_FIRST_ENCRYPTED_VERSION}: records are AES encrypted; decoding needs DJI keychains (no API key available, D5)"
        ));
    } else if version >= 7 {
        reasons.push(format!(
            "TXT v{version}: records XOR-obfuscated with a published scheme (readable without a key)"
        ));
    }
    if version > TXT_MAX_KNOWN_VERSION {
        reasons.push(format!(
            "TXT: version {version} is newer than the highest version dji-log-parser 0.5.7 handles ({TXT_MAX_KNOWN_VERSION})"
        ));
        score -= 0.2;
    }

    Ok(Detection {
        kind: "dji_txt",
        subtype: Some(format!("txt_v{version}")),
        version: Some(version as i64),
        encrypted: Some(encrypted),
        confidence: score.clamp(0.0, 0.99),
        reasons,
    })
}

/// Detect the format of any byte source.
pub fn detect_source<S: ByteSource>(src: &mut S, file_name: &str) -> Detection {
    let len = src.len();
    if len == 0 {
        return Detection::unknown(vec!["file is empty (0 bytes)".into()]);
    }
    let txt = detect_txt(src, file_name);
    let head = src.read_at(0, HEAD_LEN);
    let dat = dat::detect::detect_dat(&head, len, file_name);

    match (txt, dat) {
        (Ok(t), Some(d)) => {
            // Both matched: keep the stronger, record the other as a reason.
            if d.confidence > t.confidence {
                let mut d = d;
                d.reasons
                    .push(format!("also weakly matched TXT layout (confidence {:.2})", t.confidence));
                d
            } else {
                let mut t = t;
                t.reasons
                    .push(format!("also weakly matched DAT layout (confidence {:.2})", d.confidence));
                t
            }
        }
        (Ok(t), None) => t,
        (Err(_), Some(d)) => d,
        (Err(mut r), None) => {
            r.push(format!(
                "DAT: no DJI DAT markers (BUILD string @0x10, DJI_LOG_V3, chained 0x55 records, FLYnnn.DAT name) in first {} bytes; head = [{}]",
                head.len(),
                hex_preview(&head, 16)
            ));
            Detection::unknown(r)
        }
    }
}

pub fn detect_path(path: &Path) -> io::Result<Detection> {
    if path.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{} is a directory, not a file", path.display()),
        ));
    }
    let mut src = FileSource::open(path)?;
    let name = path
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();
    Ok(detect_source(&mut src, &name))
}

/// Convenience for tests: detect an in-memory buffer.
pub fn detect_bytes(buf: &[u8], file_name: &str) -> Detection {
    let mut s: &[u8] = buf;
    detect_source(&mut s, file_name)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Synthetic v11-style TXT: 100-byte prefix, records, Details at end.
    pub(crate) fn synth_txt(version: u8, ts_ms: i64) -> Vec<u8> {
        let prefix = if version < 6 { 12 } else { 100 };
        let records = 300usize;
        let details = 400usize;
        let detail_offset = (prefix + records) as u64;
        let mut b = vec![0u8; prefix + records + details];
        b[0..8].copy_from_slice(&detail_offset.to_le_bytes());
        b[8..10].copy_from_slice(&(details as u16).to_le_bytes());
        b[10] = version;
        let d = detail_offset as usize;
        b[d + 91..d + 99].copy_from_slice(&ts_ms.to_le_bytes());
        b
    }

    fn synth_txt_v13() -> Vec<u8> {
        let mut b = vec![0u8; 2000];
        let info_len: u16 = 699;
        let ver_off = 103 + info_len as usize;
        let rec_off = ver_off + 3 + 4;
        b[0..8].copy_from_slice(&(rec_off as u64).to_le_bytes());
        b[8..10].copy_from_slice(&436u16.to_le_bytes());
        b[10] = 14;
        b[11] = 1;
        b[100] = 0;
        b[101..103].copy_from_slice(&info_len.to_le_bytes());
        b[ver_off] = 1;
        b[ver_off + 1..ver_off + 3].copy_from_slice(&4u16.to_le_bytes());
        b[ver_off + 3..ver_off + 5].copy_from_slice(&4u16.to_le_bytes());
        b[ver_off + 5] = 3;
        b
    }

    /// Pseudo-random bytes (xorshift), deterministic.
    fn noise(n: usize, seed: u64) -> Vec<u8> {
        let mut x = seed | 1;
        (0..n)
            .map(|_| {
                x ^= x << 13;
                x ^= x >> 7;
                x ^= x << 17;
                (x >> 24) as u8
            })
            .collect()
    }

    #[test]
    fn txt_v11_detected() {
        let b = synth_txt(11, 1_594_384_279_645);
        let d = detect_bytes(&b, "DJIFlightRecord_2020-07-10_[20-31-19].txt");
        assert_eq!(d.kind, "dji_txt");
        assert_eq!(d.subtype.as_deref(), Some("txt_v11"));
        assert_eq!(d.version, Some(11));
        assert_eq!(d.encrypted, Some(false));
        assert!(d.confidence > 0.8, "{d:?}");
        assert!(d.reasons.iter().any(|r| r.contains("2020-07-10")));
    }

    #[test]
    fn txt_v3_old_prefix_detected() {
        let b = synth_txt(3, 1_450_000_000_000);
        let d = detect_bytes(&b, "x.bin");
        assert_eq!(d.kind, "dji_txt");
        assert_eq!(d.subtype.as_deref(), Some("txt_v3"));
    }

    #[test]
    fn txt_v14_encrypted() {
        let b = synth_txt_v13();
        let d = detect_bytes(&b, "DJIFlightRecord_2024-09-01_[14-55-49].txt");
        assert_eq!(d.kind, "dji_txt");
        assert_eq!(d.subtype.as_deref(), Some("txt_v14"));
        assert_eq!(d.encrypted, Some(true));
        assert!(d.confidence > 0.8, "{d:?}");
    }

    #[test]
    fn empty_is_unknown() {
        let d = detect_bytes(&[], "FLY001.DAT");
        assert_eq!(d.kind, "unknown");
        assert!(!d.reasons.is_empty());
    }

    #[test]
    fn tiny_is_unknown() {
        for n in 1..120 {
            let b = vec![0xFFu8; n];
            assert_eq!(detect_bytes(&b, "a.txt").kind, "unknown", "len {n}");
        }
    }

    #[test]
    fn random_is_unknown() {
        for seed in 1..200u64 {
            let b = noise(4096 + seed as usize * 7, seed * 0x9E37_79B9);
            let d = detect_bytes(&b, "blob.bin");
            assert_eq!(d.kind, "unknown", "seed {seed}: {d:?}");
            assert!(!d.reasons.is_empty());
        }
    }

    #[test]
    fn truncated_txt_is_unknown() {
        let b = synth_txt(11, 1_594_384_279_645);
        // Cut off before the Details block: detail_offset now points past EOF.
        let cut = &b[..350];
        let d = detect_bytes(cut, "DJIFlightRecord_x.txt");
        assert_eq!(d.kind, "unknown", "{d:?}");
    }

    #[test]
    fn truncated_v13_is_unknown() {
        let b = synth_txt_v13();
        let d = detect_bytes(&b[..150], "DJIFlightRecord_x.txt");
        assert_eq!(d.kind, "unknown", "{d:?}");
    }

    #[test]
    fn every_prefix_of_samples_never_panics() {
        let a = synth_txt(11, 1_594_384_279_645);
        let c = synth_txt_v13();
        let dat = crate::dat::detect::tests::synth_dat_v3();
        for buf in [&a, &c, &dat] {
            for n in 0..buf.len().min(1200) {
                let _ = detect_bytes(&buf[..n], "FLY001.DAT");
            }
        }
    }
}
