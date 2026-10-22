//! Offline guarantee: the crate's own source must never reference networked APIs of
//! dji-log-parser (fetch_keychains / KeychainsRequest::fetch) or any HTTP/socket API.
//! dji-log-parser links `ureq` unconditionally; this test proves our code cannot reach it.

use std::path::Path;

const FORBIDDEN: &[&str] = &[
    "fetch_keychains",
    "fetch_async",
    ".fetch(",
    "keychains_request",
    "KeychainsRequest",
    "ureq",
    "reqwest",
    "hyper",
    "TcpStream",
    "UdpSocket",
    "std::net",
];

fn scan(dir: &Path, hits: &mut Vec<String>) {
    for e in std::fs::read_dir(dir).unwrap().flatten() {
        let p = e.path();
        if p.is_dir() {
            scan(&p, hits);
        } else if p.extension().map(|x| x == "rs").unwrap_or(false) {
            let src = std::fs::read_to_string(&p).unwrap();
            for (i, line) in src.lines().enumerate() {
                // Ignore comments (doc and line comments); they explain the policy.
                let code = line.split("//").next().unwrap_or("");
                for f in FORBIDDEN {
                    if code.contains(f) {
                        hits.push(format!("{}:{}: {}", p.display(), i + 1, line.trim()));
                    }
                }
            }
        }
    }
}

#[test]
fn no_network_apis_in_crate_source() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut hits = Vec::new();
    scan(&src, &mut hits);
    assert!(hits.is_empty(), "networked API referenced:\n{}", hits.join("\n"));
}

#[test]
fn linked_parser_version_matches_pin() {
    let toml = std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml")).unwrap();
    let pin = format!(
        "dji-log-parser = {{ version = \"={}\"",
        dronedog_core::model::DJI_LOG_PARSER_VERSION
    );
    assert!(toml.contains(&pin), "Cargo.toml must pin {pin}");
}
