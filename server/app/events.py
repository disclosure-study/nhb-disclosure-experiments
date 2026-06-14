"""
Write-ahead event logging.

Every participant action is appended to an immutable JSONL file FIRST (flushed +
fsync'd), and only then mirrored into SQLite. The JSONL files in data/<study>/
are the canonical record that the s3_audit.py / s4_audit.py scripts recompute the
published numbers from; the database is a convenience mirror for live monitoring.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from . import config, db

_FILE_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonl_path(study: str, name: str):
    return config.DATA_DIR / study / f"{name}.jsonl"


def _append_jsonl(study: str, name: str, record: dict[str, Any]) -> None:
    path = _jsonl_path(study, name)
    line = json.dumps(record, ensure_ascii=False)
    with _FILE_LOCK:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def record_session(study: str, token: str, record: dict[str, Any]) -> None:
    if db.is_test_token(token):     # TEST session — persist nothing
        return
    record = {
        "v": config.PLATFORM_VERSION,
        "study": study,
        "token": token,
        "server_ts": _now_iso(),
        **record,
    }
    _append_jsonl(study, "sessions", record)


def record_event(
    study: str,
    token: str,
    etype: str,
    page: str | None = None,
    payload: dict[str, Any] | None = None,
    client_ts: int | None = None,
    seq: int | None = None,
    client_event_id: str | None = None,
) -> dict[str, Any]:
    """Append to JSONL (write-ahead), then mirror into SQLite. Idempotent.
    TEST-session tokens are never persisted (the test invite code is no-store)."""
    server_ts = _now_iso()
    if db.is_test_token(token):
        return {"ok": True, "server_ts": server_ts, "duplicate": False, "stored": False, "test": True}
    payload = payload or {}
    record = {
        "v": config.PLATFORM_VERSION,
        "study": study,
        "token": token,
        "seq": seq,
        "type": etype,
        "page": page,
        "client_ts": client_ts,
        "server_ts": server_ts,
        "client_event_id": client_event_id,
        "payload": payload,
    }
    # 1) durable write-ahead log
    _append_jsonl(study, "events", record)
    # 2) queryable mirror
    inserted = db.insert_event(
        {
            "token": token,
            "study": study,
            "seq": seq,
            "type": etype,
            "page": page,
            "client_ts": client_ts,
            "server_ts": server_ts,
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "client_event_id": client_event_id,
            "platform_version": config.PLATFORM_VERSION,
        }
    )
    return {"ok": True, "server_ts": server_ts, "duplicate": not inserted}
