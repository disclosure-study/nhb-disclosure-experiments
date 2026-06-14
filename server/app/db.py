"""
SQLite persistence layer.

Design notes
------------
* The append-only JSONL files in data/<study>/ are the PRIMARY record (written
  ahead of the DB by events.py). This SQLite database is a queryable mirror that
  powers the live admin dashboard and the adaptive top-up counter.
* Idempotency: events carry a client_event_id; (token, client_event_id) is
  UNIQUE, so a refreshed page or double-clicked button never double-logs.
* Concurrency: WAL mode + a process-wide write lock keeps bursts of ~150
  concurrent Prolific sessions from tripping "database is locked".
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from . import config

_WRITE_LOCK = threading.Lock()
_INIT_DONE = False


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    global _INIT_DONE
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS participants (
                    token          TEXT PRIMARY KEY,
                    study          TEXT NOT NULL,
                    pid_hash       TEXT,
                    cond_json      TEXT,        -- {arm, story} or {regime, prompt}
                    rng_seed       INTEGER,
                    batch_no       INTEGER,
                    status         TEXT DEFAULT 'started',  -- started|completed|withdrawn
                    any_use        INTEGER DEFAULT 0,       -- S4 logged AI use (0/1)
                    consent_ts     TEXT,
                    started_ts     TEXT,
                    completed_ts   TEXT,
                    completion_code TEXT,
                    prolific_json  TEXT,
                    meta_json      TEXT,
                    platform_version TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    token          TEXT NOT NULL,
                    study          TEXT NOT NULL,
                    seq            INTEGER,
                    type           TEXT NOT NULL,
                    page           TEXT,
                    client_ts      INTEGER,
                    server_ts      TEXT NOT NULL,
                    payload_json   TEXT,
                    client_event_id TEXT,
                    platform_version TEXT,
                    UNIQUE(token, client_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_events_token ON events(token);
                CREATE INDEX IF NOT EXISTS idx_events_type  ON events(study, type);

                CREATE TABLE IF NOT EXISTS invite_codes (
                    code        TEXT PRIMARY KEY,
                    label       TEXT,
                    max_uses    INTEGER,                 -- NULL = unlimited
                    used_count  INTEGER DEFAULT 0,
                    is_test     INTEGER DEFAULT 0,
                    active      INTEGER DEFAULT 1,
                    created_ts  TEXT
                );
                """
            )
            conn.commit()
        finally:
            conn.close()
    # Default settings
    if get_setting("intake_open") is None:
        set_setting("intake_open", "1")
    _seed_test_invite()
    _INIT_DONE = True


# --------------------------------------------------------------------------- #
# Settings (kill switch etc.) — survive restarts
# --------------------------------------------------------------------------- #
def get_setting(key: str) -> Optional[str]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()


def intake_open() -> bool:
    return get_setting("intake_open") == "1"


# --------------------------------------------------------------------------- #
# Invitation codes (access control) + test-session token registry
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Tokens belonging to TEST sessions. Server-authoritative (set at session start
# from the test invite code) and consulted by events.record_event so a test run
# never persists anything. In-memory only — a server restart mid-test session is
# rare and at worst leaves a few orphan test events (benign), never lost real data.
_TEST_TOKENS: set[str] = set()


def mark_test_token(token: str) -> None:
    _TEST_TOKENS.add(token)


def is_test_token(token: str) -> bool:
    return token in _TEST_TOKENS


# Tokens known to belong to a real session (created via /api/session/start) or a
# test session. Event/complete endpoints reject anything else, so a fabricated
# token cannot inject orphan rows into the canonical JSONL or the audit counts.
_KNOWN_TOKENS: set[str] = set()


def is_known_token(token: str) -> bool:
    if token in _KNOWN_TOKENS or token in _TEST_TOKENS:
        return True
    if get_participant(token) is not None:   # DB fallback (survives restart), then cache
        _KNOWN_TOKENS.add(token)
        return True
    return False


def _seed_test_invite() -> None:
    code = (config.INVITE_TEST_CODE or "").strip()
    if not code:
        return
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO invite_codes(code,label,max_uses,used_count,is_test,active,created_ts) "
                "VALUES(?,?,NULL,0,1,1,?) "
                "ON CONFLICT(code) DO UPDATE SET is_test=1, active=1, label=excluded.label",
                (code, "TEST CODE - runs the real study but stores no data", _now()),
            )
            conn.commit()
        finally:
            conn.close()


def insert_invite(code: str, label: str, max_uses: Optional[int], is_test: int = 0) -> None:
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO invite_codes(code,label,max_uses,used_count,is_test,active,created_ts) "
                "VALUES(?,?,?,0,?,1,?)",
                (code, label, max_uses, is_test, _now()),
            )
            conn.commit()
        finally:
            conn.close()


def validate_and_consume_invite(code: str) -> tuple[bool, bool, Optional[str]]:
    """Return (valid, is_test, reason). For a valid non-test limited-use code this
    atomically increments used_count. Test codes are validated but never consumed."""
    code = (code or "").strip()
    if not code:
        return (False, False, "invite_required")
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM invite_codes WHERE code=?", (code,)).fetchone()
            if row is None:
                return (False, False, "bad_invite")
            if not row["active"]:
                return (False, False, "inactive")
            if row["is_test"]:
                return (True, True, None)
            if row["max_uses"] is not None and row["used_count"] >= row["max_uses"]:
                return (False, False, "invite_used")
            conn.execute("UPDATE invite_codes SET used_count=used_count+1 WHERE code=?", (code,))
            conn.commit()
            return (True, False, None)
        finally:
            conn.close()


def list_invites(limit: int = 1000) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT code,label,max_uses,used_count,is_test,active,created_ts FROM invite_codes "
            "ORDER BY is_test DESC, created_ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def invite_summary() -> dict[str, int]:
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) n FROM invite_codes WHERE is_test=0").fetchone()["n"]
        used = conn.execute(
            "SELECT COUNT(*) n FROM invite_codes WHERE is_test=0 AND used_count>0").fetchone()["n"]
        return {"total": int(total), "used": int(used)}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Participants
# --------------------------------------------------------------------------- #
def insert_participant(row: dict[str, Any]) -> None:
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO participants
                   (token, study, pid_hash, cond_json, rng_seed, batch_no, status,
                    consent_ts, started_ts, prolific_json, meta_json, platform_version)
                   VALUES (:token,:study,:pid_hash,:cond_json,:rng_seed,:batch_no,'started',
                    :consent_ts,:started_ts,:prolific_json,:meta_json,:platform_version)""",
                row,
            )
            conn.commit()
        finally:
            conn.close()
    _KNOWN_TOKENS.add(row["token"])


def update_participant(token: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=:{k}" for k in fields)
    fields["token"] = token
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute(f"UPDATE participants SET {cols} WHERE token=:token", fields)
            conn.commit()
        finally:
            conn.close()


def get_participant(token: str) -> Optional[sqlite3.Row]:
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM participants WHERE token=?", (token,)).fetchone()
    finally:
        conn.close()


def pid_already_used(study: str, pid_hash: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM participants WHERE study=? AND pid_hash=? LIMIT 1",
            (study, pid_hash),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def set_any_use(token: str) -> None:
    update_participant(token, any_use=1)


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
def insert_event(ev: dict[str, Any]) -> bool:
    """Insert one event; returns False if it was a duplicate (idempotent)."""
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO events
                   (token, study, seq, type, page, client_ts, server_ts,
                    payload_json, client_event_id, platform_version)
                   VALUES (:token,:study,:seq,:type,:page,:client_ts,:server_ts,
                    :payload_json,:client_event_id,:platform_version)""",
                ev,
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def count_assistant_requests(token: str) -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE token=? AND type='assistant_request'",
            (token,),
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()


def recent_events(limit: int = 40) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT token, study, type, page, server_ts FROM events "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Stats for the admin dashboard + randomizer + top-up rule
# --------------------------------------------------------------------------- #
def cell_counts(study: str) -> dict[str, int]:
    """Counts per condition cell, keyed by the JSON cond string."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT cond_json, COUNT(*) AS n FROM participants WHERE study=? GROUP BY cond_json",
            (study,),
        ).fetchall()
        return {r["cond_json"]: int(r["n"]) for r in rows}
    finally:
        conn.close()


def study_totals(study: str) -> dict[str, int]:
    conn = get_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) n FROM participants WHERE study=?", (study,)
        ).fetchone()["n"]
        completed = conn.execute(
            "SELECT COUNT(*) n FROM participants WHERE study=? AND status='completed'",
            (study,),
        ).fetchone()["n"]
        return {"total": int(total), "completed": int(completed)}
    finally:
        conn.close()


def s4_aiusers_by_regime() -> dict[str, int]:
    """The adaptive top-up counter: completed AI-users per regime (DESIGN §5)."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT cond_json, COUNT(*) AS n FROM participants "
            "WHERE study='s4' AND any_use=1 AND status='completed' GROUP BY cond_json",
        ).fetchall()
        out = {r: 0 for r in config.S4_REGIMES}
        for r in rows:
            try:
                regime = json.loads(r["cond_json"]).get("regime")
            except Exception:
                regime = None
            if regime in out:
                out[regime] += int(r["n"])
        return out
    finally:
        conn.close()
