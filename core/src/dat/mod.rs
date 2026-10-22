//! DJI aircraft / controller DAT support.
//!
//! Status: detection is best-effort from public knowledge (see `detect.rs`); record
//! decoding is NOT implemented yet. The DAT workstream (docs/DAT_FORMAT.md,
//! tools/dat_ref*) owns this module and will replace the stub in [`parse_dat`].

pub mod detect;

use std::path::Path;

use crate::detect::{ByteSource, FileSource, HEAD_LEN};
use crate::frame::Frames;
use crate::model::{version_string, Details, ParseResult, Value};

pub const ADAPTER_NAME: &str = "dat_stub";

#[derive(Debug)]
pub enum DatError {
    Io(std::io::Error),
}

impl std::fmt::Display for DatError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DatError::Io(e) => write!(f, "{e}"),
        }
    }
}

/// Stub: returns a valid ParseResult with empty frames and whatever the header
/// detector could read.
pub fn parse_dat(path: &Path) -> Result<ParseResult, DatError> {
    if path.is_dir() {
        return Err(DatError::Io(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("{} is a directory, not a file", path.display()),
        )));
    }
    let mut src = FileSource::open(path).map_err(DatError::Io)?;
    let len = src.len();
    let head = src.read_at(0, HEAD_LEN);
    let name = path
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();

    let mut details = Details::default();
    let mut warnings = vec!["DAT decoding not yet implemented".to_string()];
    let header = detect::read_header(&head);

    match detect::detect_dat(&head, len, &name) {
        Some(d) => {
            if let Some(v) = d.version {
                details.set("log_version", Value::Int(v));
            }
            details.set(
                "dat_subtype",
                d.subtype.clone().map(Value::Str).unwrap_or(Value::Null),
            );
            details.set("dat_detect_confidence", Value::Float(d.confidence));
        }
        None => warnings.push(format!(
            "file does not look like a DJI DAT (no DAT markers in the first {} bytes)",
            head.len()
        )),
    }
    if let Some(b) = header.build_string {
        details.set("dat_build_string", Value::Str(b));
    }
    details.set("file_size_bytes", Value::Int(len as i64));

    Ok(ParseResult {
        adapter: ADAPTER_NAME.into(),
        adapter_version: version_string(),
        details,
        frames: Frames::default(),
        events: Vec::new(),
        records_total: 0,
        records_decoded: 0,
        records_failed: 0,
        warnings,
        encrypted_unreadable: false,
    })
}
