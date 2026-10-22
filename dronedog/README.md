# dronedog (Python application layer)

Case store, custody log, evidence ingest, parser adapters and the bulk import
pipeline. Contracts: `ARCHITECTURE.md` C4-C7. The Rust core `dronedog_core` (C1) is
optional at import time: without it detection falls back to header sniffing and
parsing waits (`status='pending'`) until the core is built, then `case run` finishes.

## CLI

```
python -m dronedog case new  <dir> --name "Op Kite" --examiner "Det. Smith" --case-number 26-0142
python -m dronedog case add  <dir> E:\  D:\img\card.E01  C:\x\flight_logs.zip  [--run] [--copy|--reference]
python -m dronedog case run  <dir> [--retry-failed]     # detect + parse everything pending
python -m dronedog case info <dir> [--json]
python -m dronedog custody verify <dir>                 # exit 2 + first bad line if tampered
python -m dronedog scan <paths...>                      # dry run: what would be extracted
```

Drop anything: loose logs, folders / mounted SD cards (drive letters), zips (nested),
iOS backups (folder or zipped; Manifest.db or Manifest.mbdb), Android logical dumps,
DJI Assistant .DAT export bundles, raw images (.dd/.img/.bin, split .001/.002...,
whole-device mmcblk dumps), E01/Ex01 segment sets, raw devices (`\\.\PhysicalDrive1`,
admin). A dropped device folder is split: each image, zip and iOS backup inside it
becomes its own evidence item.

## Modules

| module | role |
|---|---|
| `case/case.py` | `Case.create/open`, folder layout, evidence intake (copy vs reference), extraction + dedupe, `store_flight` bulk insert |
| `case/store.py` | SQLite schema + migrations (WAL, `schema_version`), thread-local connections |
| `case/hashing.py` | streamed multi-hash (threads per algorithm), copy-with-hash, read-only attribute |
| `case/custody.py` | C6 hash-chained JSONL, `verify_chain()` |
| `ingest/sources.py` | classify inputs, find segment sets, split dropped folders |
| `ingest/walker.py` | one walker for folders, zips, iOS backups, Assistant bundles, images; recursion |
| `ingest/image.py` | pyewf / split-raw backends, TSK volume + filesystem walk incl. deleted, FAT overwrite check |
| `ingest/ios.py` | Manifest.db / Manifest.mbdb mapping, Info.plist summary |
| `ingest/candidates.py` | configurable selection policy (names, log dirs, DJI app dirs, sniff) |
| `ingest/sniff.py` | header sniffer + C1 fallback detector |
| `core_bridge.py` | `dronedog_core` access, fallback, `set_core()` for tests |
| `adapters/` | C4 protocol + registry, `txt_djilog`, `dat_core` |
| `pipeline.py` | `import_paths` (thread, progress, cancel), `run_import`, `process_pending` |
| `cli.py` | argparse front end |

## Evidence handling rules

* Loose files and zips <= 4 GiB are **copied** to `evidence/<id>/` (SHA-256, SHA-1 and
  MD5 computed while copying), re-read and verified, then set read-only. A mismatch
  deletes the copy and refuses the evidence (logged).
* Disk images are **referenced in place** by default (hash only; `--copy` to
  duplicate). Segment sets are hashed as one stream (= the imager's media hash) plus
  per segment; imager sidecars (.md5/.sha1/.txt) next to the image are compared;
  E01 media is hashed through libewf and compared with the hashes stored in the E01.
  Size/mtime are re-checked after reading.
* Folders / drives are referenced; their recorded hash is a manifest hash over the
  extracted candidates (`hash_scope='candidate-manifest'`).
* Extracted artifacts go to `extracted/<sha256[:12]>_<name>` (read-only), one copy per
  unique SHA-256, one `artifact_sources` row per place it was found.
* Deleted files are included where TSK exposes them, flagged, with meta address,
  partition index/offset, first-byte image offset, data runs and, on FAT, a check of
  whether the clusters have been reallocated (`overwrite_status`). Reads that fail
  part-way (data beyond the end of a partial image) keep the recovered prefix and mark
  it partial.

## Schema additions to C5 v1

`evidence`: `md5, storage, stored_path, hash_scope, subkind, meta_json`;
new `evidence_files` (per-segment hashes); `artifacts`: `name, added_utc` (+ unique
index on sha256); new `artifact_sources` (provenance per find); `flights`:
`records_*`, `encrypted_unreadable`, `result_sha256`, `parsed_utc`; new `frame_extra`
(non-C3 columns); new `custody_head` (truncation check).

## Tests

`.venv\Scripts\python -m pytest tests -q` (synthetic FAT16 images, Manifest.db, zips;
a fake core via `core_bridge.set_core`). `tests/test_ingest_corpus.py` walks finished
CFReDS downloads in `corpus/raw/cfreds` and skips when absent.
