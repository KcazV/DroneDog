"""C5 SQLite case store: schema, migrations, thread-local connections.

WAL journal so the UI can read while an import job writes. Each thread gets its
own connection (sqlite3 connections are not shared across threads). Connections
are in autocommit mode; use ``with store.tx() as c:`` for multi-statement writes.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# C3 normalized frame columns (schema v1) -> SQLite type. Order is the frames table order.
FRAME_COLUMNS: list[tuple[str, str]] = [
    ("t_offset_ms", "INTEGER"), ("t_utc", "TEXT"),
    ("lat", "REAL"), ("lon", "REAL"), ("alt_rel_m", "REAL"), ("alt_msl_m", "REAL"),
    ("vel_n", "REAL"), ("vel_e", "REAL"), ("vel_d", "REAL"), ("speed_h_ms", "REAL"),
    ("roll", "REAL"), ("pitch", "REAL"), ("yaw", "REAL"),
    ("gimbal_pitch", "REAL"), ("gimbal_roll", "REAL"), ("gimbal_yaw", "REAL"),
    ("rc_aileron", "REAL"), ("rc_elevator", "REAL"), ("rc_throttle", "REAL"), ("rc_rudder", "REAL"),
    ("rc_mode_switch", "TEXT"), ("flight_mode", "TEXT"),
    ("gps_sats", "INTEGER"), ("gps_health", "INTEGER"),
    ("battery_pct", "REAL"), ("battery_v", "REAL"), ("battery_cells_v", "TEXT"),
    ("battery_temp_c", "REAL"),
    ("home_lat", "REAL"), ("home_lon", "REAL"), ("distance_m", "REAL"), ("height_m", "REAL"),
    ("motor_on", "INTEGER"), ("is_flying", "INTEGER"),
]
_frame_cols_sql = ",\n    ".join(f"{c} {t}" for c, t in FRAME_COLUMNS)   # v1 table body (frozen)

# C3 v1.1 additions (added to the table by migration 2)
FRAME_COLUMNS_V1_1: list[tuple[str, str]] = [
    ("vps_height_m", "REAL"), ("distance_app_m", "REAL"),
    ("rc_signal_up_pct", "REAL"), ("rc_signal_down_pct", "REAL"),
]
FRAME_COLUMNS = FRAME_COLUMNS + FRAME_COLUMNS_V1_1
FRAME_COLUMN_NAMES = [c for c, _ in FRAME_COLUMNS]

# Each migration: (version, description, sql). Append only; never edit a shipped one.
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "C5 v1 base schema + ingest provenance additions", f"""
CREATE TABLE evidence(
    id INTEGER PRIMARY KEY,
    label TEXT,
    src_path TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('file','folder','zip','image','sd')),
    size INTEGER,
    sha256 TEXT,
    sha1 TEXT,
    added_utc TEXT NOT NULL,
    examiner TEXT,
    -- additions (C5 v1.1)
    md5 TEXT,
    storage TEXT NOT NULL DEFAULT 'copied' CHECK(storage IN ('copied','referenced')),
    stored_path TEXT,           -- case-relative path of the copy, or absolute src when referenced
    hash_scope TEXT,            -- what sha256 covers: 'file' | 'segments-concat' | 'candidate-manifest'
    subkind TEXT,               -- e.g. 'ios_backup', 'e01', 'split_raw', 'raw', 'raw_device'
    meta_json TEXT              -- E01 header values, iOS Info.plist, sidecar checks ...
);
CREATE TABLE evidence_files(
    id INTEGER PRIMARY KEY,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id),
    seq INTEGER NOT NULL,
    src_path TEXT NOT NULL,
    stored_path TEXT,
    size INTEGER, sha256 TEXT, sha1 TEXT, md5 TEXT
);
CREATE TABLE artifacts(
    id INTEGER PRIMARY KEY,
    evidence_id INTEGER REFERENCES evidence(id),   -- first provenance
    rel_path TEXT,              -- case-relative path of the extracted working copy
    inner_path TEXT,            -- path inside image/zip/backup (first provenance)
    offset INTEGER,             -- byte offset in image when known (first provenance)
    size INTEGER,
    sha256 TEXT,
    sha1 TEXT,
    detected_kind TEXT,
    detected_subtype TEXT,
    detection_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','parsed','failed','unsupported','encrypted')),
    error TEXT,
    -- additions
    name TEXT,
    added_utc TEXT
);
CREATE UNIQUE INDEX artifacts_sha256 ON artifacts(sha256) WHERE sha256 IS NOT NULL;
CREATE INDEX artifacts_status ON artifacts(status);
-- Addition: every place an artifact was found (dedup keeps one copy, many sources).
CREATE TABLE artifact_sources(
    id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES artifacts(id),
    evidence_id INTEGER NOT NULL REFERENCES evidence(id),
    inner_path TEXT,
    container TEXT,             -- 'file'|'folder'|'zip'|'ios_backup'|'image'|'assistant'
    offset INTEGER,             -- absolute byte offset of first data byte in the image
    partition_index INTEGER,
    partition_offset INTEGER,
    fs_type TEXT,
    meta_addr INTEGER,          -- inode / MFT entry / FAT dir-entry address
    deleted INTEGER NOT NULL DEFAULT 0,
    overwrite_status TEXT,      -- 'allocated'|'none_detected'|'partial'|'full'|'meta_reallocated'|'unknown'
    size INTEGER,
    mtime_utc TEXT, atime_utc TEXT, ctime_utc TEXT, crtime_utc TEXT,
    ios_domain TEXT, ios_relative_path TEXT, ios_file_id TEXT,
    app_hint TEXT,
    reasons_json TEXT,
    extra_json TEXT,
    found_utc TEXT NOT NULL
);
CREATE INDEX artifact_sources_artifact ON artifact_sources(artifact_id);
CREATE TABLE flights(
    id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES artifacts(id),
    adapter TEXT, adapter_version TEXT,
    source TEXT CHECK(source IN ('aircraft-dat','controller-dat','app-txt')),
    details_json TEXT,
    start_utc TEXT,
    duration_s REAL,
    frame_count INTEGER,
    bbox_json TEXT,
    warnings_json TEXT,
    -- additions
    records_total INTEGER, records_decoded INTEGER, records_failed INTEGER,
    encrypted_unreadable INTEGER,
    result_sha256 TEXT,         -- sha256 of canonical ParseResult JSON (reproducibility)
    parsed_utc TEXT
);
CREATE TABLE frames(
    flight_id INTEGER NOT NULL REFERENCES flights(id),
    idx INTEGER NOT NULL,
    {_frame_cols_sql}
);
CREATE INDEX frames_flight_idx ON frames(flight_id, idx);
-- Addition: adapter frame columns not (yet) in C3, stored columnar per flight.
CREATE TABLE frame_extra(
    flight_id INTEGER NOT NULL REFERENCES flights(id),
    name TEXT NOT NULL,
    values_json TEXT NOT NULL,
    PRIMARY KEY(flight_id, name)
);
CREATE TABLE events(
    id INTEGER PRIMARY KEY,
    flight_id INTEGER NOT NULL REFERENCES flights(id),
    t_offset_ms INTEGER, kind TEXT, severity TEXT, message TEXT, raw TEXT
);
CREATE INDEX events_flight ON events(flight_id);
CREATE TABLE links(
    id INTEGER PRIMARY KEY,
    flight_a INTEGER, flight_b INTEGER, kind TEXT, score REAL, reasons_json TEXT
);
-- Addition: mirror of the custody chain head, to detect log truncation.
CREATE TABLE custody_head(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    seq INTEGER NOT NULL,
    hash TEXT NOT NULL
);
"""),
    (2, "C3 v1.1 frame columns",
     "\n".join(f"ALTER TABLE frames ADD COLUMN {c} {t};" for c, t in FRAME_COLUMNS_V1_1)),
    # derived examiner summary (dronedog.analysis.summary): kept beside, never inside, the
    # hashed parse result so result_sha256 stays reproducible from the parser output alone
    (3, "flight analysis summary", "ALTER TABLE flights ADD COLUMN analysis_json TEXT;"),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    # -- connections ------------------------------------------------------
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, isolation_level=None, timeout=60,
                                check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=60000")
            self._local.conn = c
            with self._lock:
                self._all.append(c)
        return c

    def close(self) -> None:
        with self._lock:
            for c in self._all:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
            self._all.clear()
        self._local = threading.local()

    @contextmanager
    def tx(self):
        c = self.conn()
        if c.in_transaction:  # nested: join the outer transaction
            yield c
            return
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def execute(self, sql: str, args=()) -> sqlite3.Cursor:
        return self.conn().execute(sql, args)

    def query(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.conn().execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        r = self.conn().execute(sql, args).fetchone()
        return dict(r) if r is not None else None

    # -- schema -----------------------------------------------------------
    def current_version(self) -> int:
        c = self.conn()
        has = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
        if not has:
            return 0
        r = c.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(r[0] or 0)

    def migrate(self) -> int:
        c = self.conn()
        c.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, "
                  "description TEXT, applied_utc TEXT NOT NULL)")
        cur = self.current_version()
        if cur > SCHEMA_VERSION:
            raise RuntimeError(f"case.db schema v{cur} is newer than this DroneDog (v{SCHEMA_VERSION})")
        for ver, desc, sql in MIGRATIONS:
            if ver <= cur:
                continue
            c.execute("BEGIN IMMEDIATE")
            try:
                for stmt in _split_sql(sql):
                    c.execute(stmt)
                c.execute("INSERT INTO schema_version(version, description, applied_utc) VALUES (?,?,?)",
                          (ver, desc, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")))
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
        return self.current_version()


def _split_sql(script: str) -> list[str]:
    out, buf = [], []
    for line in script.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if sqlite3.complete_statement("\n".join(buf)):
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return out
