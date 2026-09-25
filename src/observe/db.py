"""SQLite storage. The hook path only touches raw_events; everything else is derived."""

import sqlite3
from pathlib import Path

from observe import paths

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_events (
    id          INTEGER PRIMARY KEY,
    agent       TEXT NOT NULL,
    hook_event  TEXT,
    session_id  TEXT,
    received_at REAL NOT NULL,
    payload     TEXT NOT NULL,
    processed   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS raw_events_pending ON raw_events(processed, id);

CREATE TABLE IF NOT EXISTS sessions (
    id                 TEXT PRIMARY KEY,
    agent              TEXT NOT NULL,
    cwd                TEXT,
    model              TEXT,
    title              TEXT,
    transcript_path    TEXT,
    transcript_mtime   REAL,
    started_at         REAL,
    ended_at           REAL,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    prompt_count       INTEGER NOT NULL DEFAULT 0,
    tool_call_count    INTEGER NOT NULL DEFAULT 0,
    error_count        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL,
    agent       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    category    TEXT,
    tool_name   TEXT,
    tool_use_id TEXT,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    duration_ms INTEGER,
    status      TEXT,
    target      TEXT,
    summary     TEXT,
    detail      TEXT,
    source      TEXT NOT NULL DEFAULT 'hook'
);
CREATE UNIQUE INDEX IF NOT EXISTS events_tool_use
    ON events(session_id, tool_use_id) WHERE tool_use_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, started_at);

CREATE TABLE IF NOT EXISTS files (
    event_id   INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    path       TEXT NOT NULL,
    op         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_session ON files(session_id);
CREATE INDEX IF NOT EXISTS files_event ON files(event_id);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    return conn
