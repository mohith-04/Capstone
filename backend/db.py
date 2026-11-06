"""
db.py — SQLite storage for organisations and users
===================================================
Uses the stdlib sqlite3 module (no ORM) — the schema is tiny and a capstone
doesn't need the extra dependency. A new connection is opened per call, which
is safe across FastAPI's threadpool workers.

Tables
------
orgs   one row per hospital organisation; `join_code` lets ambulances join.
users  hospital admins (role='hospital') and ambulances (role='ambulance').
       Ambulances carry a globally unique `unit_id` (e.g. "AMB-101") which is
       also the ambulance_id used by the simulator, so live telemetry can be
       matched back to its organisation.
"""

import os
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    join_code  TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('hospital', 'ambulance')),
    org_id        INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    unit_id       TEXT UNIQUE,
    display_name  TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with connect() as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(_TRIPS_SCHEMA)
        conn.executescript(_CAPACITY_SCHEMA)
        # Migration: link an org to a registry hospital (column added after first release)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(orgs)")]
        if "hospital_id" not in cols:
            conn.execute("ALTER TABLE orgs ADD COLUMN hospital_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_orgs_hospital ON orgs(hospital_id) "
            "WHERE hospital_id IS NOT NULL"
        )

_CAPACITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS hospital_status (
    hospital_id       TEXT PRIMARY KEY,      -- id from the hospital registry
    er_beds_total     INTEGER NOT NULL,
    er_beds_available INTEGER NOT NULL,
    icu_available     INTEGER NOT NULL,
    diversion         INTEGER NOT NULL DEFAULT 0,   -- 1 = not accepting new patients
    specialties       TEXT NOT NULL,                -- JSON list of tags
    updated_at        REAL NOT NULL
);
"""


_TRIPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id            INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    unit_id           TEXT NOT NULL,
    hospital_id       TEXT,
    hospital_name     TEXT,
    emergency_type    TEXT,
    severity          TEXT,
    started_at        REAL NOT NULL,
    ended_at          REAL,
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'completed', 'cancelled')),
    distance_m        REAL,
    duration_s        REAL,      -- real-world-equivalent travel time
    estimated_s       REAL,      -- route estimate at dispatch
    reroute_count     INTEGER,
    preemptions       INTEGER,   -- signals cleared for this ambulance
    signal_saved_s    REAL       -- red-light waiting avoided
);
CREATE INDEX IF NOT EXISTS idx_trips_org ON trips(org_id, started_at DESC);
"""

