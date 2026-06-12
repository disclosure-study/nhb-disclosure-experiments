"""
End-to-end walkthrough test against a RUNNING server.

    python run_local.py            # in one shell (DATA_DIR=data_test recommended)
    python tests/walkthrough.py    # in another

Simulates a full Study-3 and Study-4 participant via the public API, then checks
that the admin counters moved, the JSONL write-ahead logs grew, idempotency holds,
and the Study-4 AI-use flag was recorded. Exits non-zero on any failure.
"""
from __future__ import annotations

import json
import os
import sys
import time
import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:8000")
ADMIN = os.environ.get("ADMIN_TOKEN", "dev-admin-token")
RUN = str(int(time.time()))          # unique per run so PIDs don't collide with the dup-guard
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        fails.append(msg)


def post(path, body):
    return httpx.post(BASE + path, json=body, timeout=20).json()


def ev(token, study, etype, page=None, payload=None, ceid=None):
    return post("/api/event", {
        "token": token, "study": study, "type": etype, "page": page,
        "payload": payload or {}, "client_ts": 0, "client_event_id": ceid,
    })


def run_s3():
    print("\n=== Study 3 walkthrough ===")
    r = post("/api/session/start", {"study": "s3", "prolific": {"pid": f"TESTPID_S3_{RUN}"},
                                    "screen": {"w": 1440, "h": 900}})
    check(r.get("ok"), "session/start ok")
    token = r["token"]
    cond = r["cond"]
    check("arm" in cond and "story" in cond, f"assigned cond {cond}")
    ev(token, "s3", "consent_accept", "consent")
    ev(token, "s3", "page_enter", "story")
    ev(token, "s3", "story_read", "story", {"arm": cond["arm"], "dwell_ms": 95000, "scroll_max_pct": 98})
    ev(token, "s3", "responses", "reactions_main", {"values": {"KUD1": 5, "VBI2": 3, "VBI3": 4}})
    ev(token, "s3", "click_next", "choice", {"choice": "read", "choice_latency_ms": 2200})
    ev(token, "s3", "responses", "checks", {"values": {"manip_check": "created"}, "comprehension_pass": True})
    ev(token, "s3", "debrief_reconsent", "debrief", {"agreed": True})
    # idempotency: same client_event_id twice
    a = ev(token, "s3", "test_dupe", "x", {}, ceid="fixed-id-1")
    b = ev(token, "s3", "test_dupe", "x", {}, ceid="fixed-id-1")
    check(a.get("duplicate") is False and b.get("duplicate") is True, "duplicate client_event_id ignored")
    c = post("/api/session/complete", {"token": token, "study": "s3", "summary": {"pages_seen": 9}})
    check(c.get("ok") and c.get("completion_code"), f"completion code returned: {c.get('completion_code')}")
    # duplicate participation blocked
    dup = post("/api/session/start", {"study": "s3", "prolific": {"pid": f"TESTPID_S3_{RUN}"}})
    check(dup.get("ok") is False and dup.get("reason") == "already_participated", "duplicate PID blocked")
    return token


def run_s4():
    print("\n=== Study 4 walkthrough ===")
    r = post("/api/session/start", {"study": "s4", "prolific": {"pid": f"TESTPID_S4_{RUN}"}})
    check(r.get("ok"), "session/start ok")
    token = r["token"]
    cond = r["cond"]
    check("regime" in cond and "prompt" in cond, f"assigned cond {cond}")
    ev(token, "s4", "consent_accept", "consent")
    ev(token, "s4", "gallery_view", "gallery", {"regime": cond["regime"]})
    ev(token, "s4", "gallery_rate", "gallery", {"piece": "g03", "rating": 5})
    ev(token, "s4", "writing_started", "writing", {"prompt_id": cond["prompt"]})
    # assistant request through the proxy (offline mode -> deterministic)
    a = post("/api/s4/assistant", {"token": token, "affordance": "suggest_opening",
                                   "draft": "", "prompt_theme": "An unexpected letter"})
    check(a.get("ok") and a.get("text"), f"assistant responded (source={a.get('source')})")
    # insert -> sets any_use
    ev(token, "s4", "assistant_insert", "writing", {"chars_inserted": 40, "insertion_offset": 0})
    ev(token, "s4", "writing_final", "writing",
       {"final_text": "A short test piece. " * 12, "word_count": 130, "writing_ms": 240000})
    ev(token, "s4", "label_choice", "label",
       {"regime": cond["regime"], "label_choice": "assisted_drafting", "decision_latency_ms": 8000,
        "toggle_sequence": [{"value": "none", "t_ms": 1000}, {"value": "assisted_drafting", "t_ms": 6000}]})
    ev(token, "s4", "debrief_reconsent", "debrief", {"agreed": True})
    c = post("/api/session/complete", {"token": token, "study": "s4"})
    check(c.get("ok") and c.get("completion_code"), f"completion code returned: {c.get('completion_code')}")
    return token


def check_admin_and_logs():
    print("\n=== Admin + raw logs ===")
    s = httpx.get(BASE + "/api/admin/stats", params={"key": ADMIN}, timeout=20).json()
    check(s.get("ok"), "admin stats authorized")
    check(s["s3"]["total"] >= 1, f"s3 total counted ({s['s3']['total']})")
    check(s["s4"]["total"] >= 1, f"s4 total counted ({s['s4']['total']})")
    aiu = sum(s["s4"]["aiusers_by_regime"].values())
    check(aiu >= 1, f"s4 AI-users counted ({aiu}) — any_use flag works")
    # unauthorized
    u = httpx.get(BASE + "/api/admin/stats", params={"key": "wrong"}, timeout=20)
    check(u.status_code == 401, "admin rejects wrong key")
    # JSONL write-ahead present (data dir relative to the server working dir)
    from pathlib import Path
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    for study in ("s3", "s4"):
        evp = data_dir / study / "events.jsonl"
        n = sum(1 for _ in open(evp, encoding="utf-8")) if evp.exists() else 0
        check(n > 0, f"{study}/events.jsonl has {n} write-ahead lines")
    # export endpoint returns a zip
    z = httpx.get(BASE + "/api/admin/export", params={"key": ADMIN, "study": "s4"}, timeout=20)
    check(z.status_code == 200 and z.headers.get("content-type") == "application/zip",
          f"export zip returned ({len(z.content)} bytes)")


if __name__ == "__main__":
    print(f"Testing {BASE}")
    h = httpx.get(BASE + "/api/health", timeout=20).json()
    print("health:", json.dumps(h))
    run_s3()
    run_s4()
    check_admin_and_logs()
    print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} CHECK(S) FAILED"))
    sys.exit(1 if fails else 0)
