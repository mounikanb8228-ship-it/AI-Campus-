"""Separate login database (auth.db). It never touches campus.db."""
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_DB = os.environ.get("AUTH_DB", os.path.join(ROOT, "auth.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usn TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',      -- student | faculty | admin
    status TEXT NOT NULL DEFAULT 'pending',    -- pending | active | disabled
    password_hash TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS allowed_usns (
    usn TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'student',
    code_hash TEXT NOT NULL,                   -- hash of the one-time activation code
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS visitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,                 -- visitor pass code shown at the gate
    name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    host TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    valid_until REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    usn TEXT,
    ip TEXT,
    event TEXT NOT NULL,
    detail TEXT
);
"""


def connect():
    con = sqlite3.connect(AUTH_DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_auth_db():
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    try:
        os.chmod(AUTH_DB, 0o600)
    except OSError:
        pass


def audit(event, usn=None, ip=None, detail=None):
    con = connect()
    con.execute(
        "INSERT INTO audit_log (ts, usn, ip, event, detail) VALUES (?, ?, ?, ?, ?)",
        (time.time(), usn, ip, event, (detail or "")[:300]),
    )
    con.commit()
    con.close()
