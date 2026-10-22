# dronedog_core

DroneDog's Rust forensic core, exposed to Python as `dronedog_core` (PyO3, abi3 for
CPython >= 3.9, built with maturin). It detects and parses DJI flight logs from a path,
read-only. Interface contract: `ARCHITECTURE.md` C1-C3. Field mapping: `MAPPING.md`.

## Build

```powershell
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
$env:VIRTUAL_ENV = "<repo>\.venv"
.venv\Scripts\maturin.exe develop -m core\Cargo.toml --release
```

Rust tests run without Python (the `python` feature is off):

```powershell
cd core
cargo test --release --no-default-features            # unit + corpus + baseline + offline guard
$env:DRONEDOG_WRITE_BASELINES = "1"; cargo test --release --no-default-features --test baselines_txt   # regenerate goldens
```

Python shape tests: `.venv\Scripts\python.exe -m pytest tests/test_core_txt.py`.
Corpus tests look under `corpus/raw`, `corpus/_downloads`, `corpus/samples` (override with
`DRONEDOG_CORPUS`) and skip cleanly when no samples exist.

## API

```python
import dronedog_core as dc

dc.version()        # "dronedog_core 0.1.0 (dji-log-parser 0.5.7)"
dc.detect(path)     # {"kind","subtype","version","encrypted","confidence","reasons"}
dc.parse_txt(path, keychains_json=None)   # C1 ParseResult dict
dc.parse_dat(path)  # C1 ParseResult dict (stub: frames empty, warning says so)

dc.ParseError       # subclass of ValueError: not a readable log of that format
dc.KeychainError    # subclass of ValueError: keychains_json malformed
dc.C2_KEYS, dc.C3_COLUMNS, dc.DJI_LOG_PARSER_VERSION, dc.__version__
```

Errors: missing file -> `FileNotFoundError`, directory -> `IsADirectoryError`, access ->
`PermissionError`, other I/O -> `OSError`. Malformed input -> `ParseError` with the reason.
Panics inside dependencies are caught and re-raised as `ParseError`; the interpreter is
never aborted (release profile keeps `panic = "unwind"`).

### detect

Reads at most the first 4 KiB plus, for TXT v1-v11, ~100 bytes at the Details offset.
Never reads the whole file and never writes.

* `dji_txt`, subtype `txt_v<N>`: prefix layout from dji-log-parser (`detail_offset` u64,
  `detail_length` u16, version u8 @0x0A) checked for structural consistency, plus an
  independent confirmation (plausible Details start time, v13+ Auxiliary blocks, known
  Details size, file name). `encrypted` is true for v13+.
* `dji_dat`: see `src/dat/detect.rs` (best effort, owned by the DAT workstream):
  `dat_v3` (BUILD @0x10 + `DJI_LOG_V3` @0xF2 + chained 0x55 records at 0x100),
  `dat_legacy` (BUILD + 0x55 records from 0x80, DatCon "V1"), `dat_encrypted` (`LOGH`
  magic), `dat_assistant_zlib`, `dat_unknown`. `encrypted` stays null unless known.
* `unknown`: everything else (empty, truncated, random). `reasons` always lists the
  evidence (offsets, bytes seen).

### parse_txt

Uses `dji-log-parser` 0.5.7 as a library. v1-v12 are decoded in full. v13+ needs DJI
keychains: without `keychains_json` the plaintext details are returned, `frames` are empty
lists, `encrypted_unreadable` is True and a warning says why. Performance: a 15-minute
flight (9 200 frames) parses in ~40 ms in Rust, ~50 ms including the Python conversion;
columns are built directly (no serde round trip).

## Offline guarantee

No runtime network access. The crate never calls `DJILog::fetch_keychains`,
`fetch_keychains_async`, `keychains_request` or `KeychainsRequest::fetch`; keychains are
accepted only as already-cached JSON. `tests/offline_guard.rs` fails the build's tests if
any of those names, or `ureq`/`reqwest`/`TcpStream`/`std::net`, appear in `src/`.

Caveat for the validation dossier: dji-log-parser 0.5.7 declares `ureq` (HTTP client) as a
non-optional dependency, so it is compiled into the extension even though no code path
in dronedog_core can reach it.
