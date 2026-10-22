//! `dronedog_core`: DroneDog's Rust forensic core.
//!
//! This file contains Python bindings only (behind the `python` feature). All logic
//! lives in the modules below and is usable from Rust without Python.
//!
//! Runtime makes zero network calls. See `txt.rs` and `tests/offline_guard.rs`.

pub mod dat;
pub mod detect;
pub mod frame;
pub mod model;
pub mod txt;

#[cfg(feature = "python")]
mod py {
    use std::path::PathBuf;

    use pyo3::exceptions::{PyFileNotFoundError, PyIsADirectoryError, PyOSError, PyPermissionError, PyValueError};
    use pyo3::prelude::*;
    use pyo3::types::{PyDict, PyList};

    use crate::frame::ColumnRef;
    use crate::model::{self, Detection, ParseResult, Value};
    use crate::{dat, detect, txt};

    pyo3::create_exception!(
        dronedog_core,
        ParseError,
        PyValueError,
        "Input could not be parsed as the requested log format."
    );
    pyo3::create_exception!(
        dronedog_core,
        KeychainError,
        PyValueError,
        "keychains_json was malformed."
    );

    fn io_err(path: &str, e: std::io::Error) -> PyErr {
        let msg = format!("{path}: {e}");
        match e.kind() {
            std::io::ErrorKind::NotFound => PyFileNotFoundError::new_err(msg),
            std::io::ErrorKind::PermissionDenied => PyPermissionError::new_err(msg),
            std::io::ErrorKind::InvalidInput if e.to_string().contains("directory") => {
                PyIsADirectoryError::new_err(msg)
            }
            _ => PyOSError::new_err(msg),
        }
    }

    fn value_to_py<'py>(py: Python<'py>, v: &Value) -> PyResult<PyObject> {
        Ok(match v {
            Value::Null => py.None(),
            Value::Bool(b) => b.into_pyobject(py)?.to_owned().into_any().unbind(),
            Value::Int(i) => i.into_pyobject(py)?.into_any().unbind(),
            Value::Float(f) => f.into_pyobject(py)?.into_any().unbind(),
            Value::Str(s) => s.into_pyobject(py)?.into_any().unbind(),
            Value::Map(m) => {
                let d = PyDict::new(py);
                for (k, v) in m {
                    d.set_item(k, value_to_py(py, v)?)?;
                }
                d.into_any().unbind()
            }
        })
    }

    fn detection_to_py<'py>(py: Python<'py>, d: &Detection) -> PyResult<Bound<'py, PyDict>> {
        let o = PyDict::new(py);
        o.set_item("kind", d.kind)?;
        o.set_item("subtype", d.subtype.as_deref())?;
        o.set_item("version", d.version)?;
        o.set_item("encrypted", d.encrypted)?;
        o.set_item("confidence", d.confidence)?;
        o.set_item("reasons", PyList::new(py, &d.reasons)?)?;
        Ok(o)
    }

    fn column_to_py<'py>(py: Python<'py>, c: &ColumnRef<'_>) -> PyResult<Bound<'py, PyList>> {
        match c {
            ColumnRef::Int(v) => PyList::new(py, v.iter()),
            ColumnRef::Float(v) => PyList::new(py, v.iter()),
            ColumnRef::Str(v) => PyList::new(py, v.iter().map(|s| s.as_deref())),
            ColumnRef::Bool(v) => PyList::new(py, v.iter()),
            ColumnRef::FloatList(v) => {
                let items: PyResult<Vec<PyObject>> = v
                    .iter()
                    .map(|cell| -> PyResult<PyObject> {
                        Ok(match cell {
                            None => py.None(),
                            Some(xs) => PyList::new(py, xs.iter())?.into_any().unbind(),
                        })
                    })
                    .collect();
                PyList::new(py, items?)
            }
        }
    }

    fn result_to_py<'py>(py: Python<'py>, r: &ParseResult) -> PyResult<Bound<'py, PyDict>> {
        let o = PyDict::new(py);
        o.set_item("adapter", &r.adapter)?;
        o.set_item("adapter_version", &r.adapter_version)?;
        let det = PyDict::new(py);
        for (k, v) in &r.details.entries {
            det.set_item(k, value_to_py(py, v)?)?;
        }
        o.set_item("details", det)?;
        let frames = PyDict::new(py);
        for (name, col) in r.frames.columns() {
            frames.set_item(name, column_to_py(py, &col)?)?;
        }
        o.set_item("frames", frames)?;
        let evs = PyList::empty(py);
        for e in &r.events {
            let d = PyDict::new(py);
            d.set_item("t_offset_ms", e.t_offset_ms)?;
            d.set_item("kind", &e.kind)?;
            d.set_item("severity", e.severity.as_str())?;
            d.set_item("message", &e.message)?;
            d.set_item("raw", e.raw.as_deref())?;
            evs.append(d)?;
        }
        o.set_item("events", evs)?;
        o.set_item("records_total", r.records_total)?;
        o.set_item("records_decoded", r.records_decoded)?;
        o.set_item("records_failed", r.records_failed)?;
        o.set_item("warnings", PyList::new(py, &r.warnings)?)?;
        o.set_item("encrypted_unreadable", r.encrypted_unreadable)?;
        Ok(o)
    }

    /// Crate version plus the linked dji-log-parser version.
    #[pyfunction]
    #[pyo3(name = "version")]
    fn py_version() -> String {
        model::version_string()
    }

    /// Identify a file from its header bytes. Never writes. Returns a C1 detection dict.
    #[pyfunction]
    #[pyo3(name = "detect")]
    fn py_detect<'py>(py: Python<'py>, path: PathBuf) -> PyResult<Bound<'py, PyDict>> {
        let p = path.clone();
        let d = py
            .allow_threads(move || detect::detect_path(&p))
            .map_err(|e| io_err(&path.display().to_string(), e))?;
        detection_to_py(py, &d)
    }

    /// Parse a DJI app TXT log. Offline only: v13+ needs `keychains_json` (cached
    /// keychains); without it, details are returned and `encrypted_unreadable` is True.
    #[pyfunction]
    #[pyo3(name = "parse_txt", signature = (path, keychains_json=None))]
    fn py_parse_txt<'py>(
        py: Python<'py>,
        path: PathBuf,
        keychains_json: Option<String>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let p = path.clone();
        let r = py.allow_threads(move || txt::parse_txt(&p, keychains_json.as_deref()));
        let shown = path.display().to_string();
        match r {
            Ok(r) => result_to_py(py, &r),
            Err(txt::TxtError::Io(e)) => Err(io_err(&shown, e)),
            Err(txt::TxtError::Keychain(m)) => Err(KeychainError::new_err(format!("{shown}: invalid keychains_json: {m}"))),
            Err(e @ txt::TxtError::Parse(_)) | Err(e @ txt::TxtError::Panic(_)) => {
                Err(ParseError::new_err(format!("{shown}: {e}")))
            }
        }
    }

    /// Parse a DJI DAT. Currently a stub: frames empty, warning states it plainly.
    #[pyfunction]
    #[pyo3(name = "parse_dat")]
    fn py_parse_dat<'py>(py: Python<'py>, path: PathBuf) -> PyResult<Bound<'py, PyDict>> {
        let p = path.clone();
        let r = py.allow_threads(move || dat::parse_dat(&p));
        match r {
            Ok(r) => result_to_py(py, &r),
            Err(dat::DatError::Io(e)) => Err(io_err(&path.display().to_string(), e)),
        }
    }

    #[pymodule]
    fn dronedog_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(py_version, m)?)?;
        m.add_function(wrap_pyfunction!(py_detect, m)?)?;
        m.add_function(wrap_pyfunction!(py_parse_txt, m)?)?;
        m.add_function(wrap_pyfunction!(py_parse_dat, m)?)?;
        m.add("ParseError", m.py().get_type::<ParseError>())?;
        m.add("KeychainError", m.py().get_type::<KeychainError>())?;
        m.add("__version__", model::CRATE_VERSION)?;
        m.add("DJI_LOG_PARSER_VERSION", model::DJI_LOG_PARSER_VERSION)?;
        m.add("C3_COLUMNS", crate::frame::C3_COLUMNS.to_vec())?;
        m.add("C2_KEYS", model::C2_KEYS.to_vec())?;
        Ok(())
    }
}
