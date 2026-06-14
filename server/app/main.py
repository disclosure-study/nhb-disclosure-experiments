"""
FastAPI application — the experiment instrument.

Routes
------
GET  /api/health                  liveness + version + intake flag
POST /api/session/start           hash PID, assign condition, open a session
POST /api/event                   log one participant action (write-ahead)
POST /api/events/batch            log many (sendBeacon flush on page unload)
POST /api/s4/assistant            Study-4 LLM writing assistant (logged proxy)
POST /api/session/complete        close a session, return completion code
GET  /admin                       live monitoring dashboard (key-gated)
GET  /api/admin/stats             dashboard JSON
POST /api/admin/intake            kill switch (open/close intake)
GET  /api/admin/export            download raw JSONL + participants.csv (zip)

Static frontend (web/) is mounted at / so the same server can host the study.
"""
from __future__ import annotations

import hashlib
import io
import secrets
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import admin, config, db, events, llm_proxy, randomizer

app = FastAPI(title="NHB Disclosure Experiments Platform", version=config.PLATFORM_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _hash_pid(pid: Optional[str]) -> Optional[str]:
    if not pid:
        return None
    return hashlib.sha256((config.PID_SALT + pid).encode("utf-8")).hexdigest()


_CONFIG_VERSIONS: dict[str, str] = {}


def _config_version(study: str) -> str:
    if study in _CONFIG_VERSIONS:
        return _CONFIG_VERSIONS[study]
    fname = "study3.json" if study == "s3" else "study4.json"
    path = config.WEB_DIR / "config" / fname
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except Exception:
        digest = "missing"
    _CONFIG_VERSIONS[study] = digest
    return digest


# Lightweight per-IP throttle on FAILED invite attempts (brute-force / DoS guard).
# Only failures count, so a legitimate participant (who succeeds first try) is
# never affected. The real client IP comes from X-Forwarded-For (set by Caddy).
_INVITE_FAILS: dict[str, list[float]] = {}
_FAIL_WINDOW = 600.0
_FAIL_MAX = 30


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _invite_rate_limited(ip: str) -> bool:
    now = time.time()
    fails = [t for t in _INVITE_FAILS.get(ip, []) if now - t < _FAIL_WINDOW]
    _INVITE_FAILS[ip] = fails
    return len(fails) >= _FAIL_MAX


def _record_invite_fail(ip: str) -> None:
    _INVITE_FAILS.setdefault(ip, []).append(time.time())


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class StartReq(BaseModel):
    study: str
    invite: str | None = None
    prolific: dict[str, Any] | None = None
    screen: dict[str, Any] | None = None
    user_agent: str | None = None


class EventReq(BaseModel):
    token: str
    study: str
    type: str
    page: str | None = None
    payload: dict[str, Any] | None = None
    client_ts: int | None = None
    seq: int | None = None
    client_event_id: str | None = None


class BatchReq(BaseModel):
    events: list[EventReq]


class AssistantReq(BaseModel):
    token: str
    affordance: str
    draft: str | None = ""
    selected: str | None = ""
    prompt_theme: str | None = ""
    user_message: str | None = ""


class CompleteReq(BaseModel):
    token: str
    study: str
    summary: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "platform_version": config.PLATFORM_VERSION,
        "intake_open": db.intake_open(),
        "invite_required": config.INVITE_REQUIRED,
        "llm_provider": config.LLM_PROVIDER,
        "llm_model": config.LLM_MODEL if config.LLM_PROVIDER != "offline" else "offline-canned",
    }


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #
_AUTO_TYPES_SET_USE = {"assistant_insert", "any_use_true"}


@app.post("/api/session/start")
def session_start(req: StartReq, request: Request) -> dict[str, Any]:
    study = req.study.lower()
    if study not in ("s3", "s4"):
        return {"ok": False, "reason": "bad_study"}
    if not db.intake_open():
        return {"ok": False, "reason": "intake_closed"}

    pid = (req.prolific or {}).get("pid") or (req.prolific or {}).get("PROLIFIC_PID")
    pid_hash = _hash_pid(pid)
    if pid_hash and db.pid_already_used(study, pid_hash):
        return {"ok": False, "reason": "already_participated"}

    # Invitation-code gate (access control). Consumed only once we know we will
    # open a session (i.e. after the duplicate-PID check above).
    is_test = False
    if config.INVITE_REQUIRED:
        ip = _client_ip(request)
        if _invite_rate_limited(ip):
            return {"ok": False, "reason": "rate_limited"}
        valid, is_test, reason = db.validate_and_consume_invite(req.invite or "")
        if not valid:
            _record_invite_fail(ip)
            return {"ok": False, "reason": reason or "bad_invite"}

    a = randomizer.assign(study)
    token = secrets.token_urlsafe(16)

    # TEST session: assign a condition + token and run the real flow, but persist
    # NOTHING (no participant row, no session/event records — see events.record_*).
    if is_test:
        db.mark_test_token(token)
        return {
            "ok": True, "token": token, "study": study, "cond": a["cond"],
            "batch_no": a["batch_no"], "test": True,
            "platform_version": config.PLATFORM_VERSION,
            "config_version": _config_version(study),
        }

    import json as _json
    db.insert_participant({
        "token": token,
        "study": study,
        "pid_hash": pid_hash,
        "cond_json": a["cond_json"],
        "rng_seed": a["rng_seed"],
        "batch_no": a["batch_no"],
        "consent_ts": None,
        "started_ts": events._now_iso(),
        "prolific_json": _json.dumps(req.prolific or {}),
        "meta_json": _json.dumps({"screen": req.screen, "user_agent": req.user_agent}),
        "platform_version": config.PLATFORM_VERSION,
    })
    events.record_session(study, token, {
        "pid_hash": pid_hash,
        "cond": a["cond"],
        "rng_seed": a["rng_seed"],
        "arrival_index": a["arrival_index"],
        "batch_no": a["batch_no"],
        "config_version": _config_version(study),
        "prolific": req.prolific or {},
        "screen": req.screen,
        "user_agent": req.user_agent,
    })
    events.record_event(study, token, "session_start", page="entry",
                        payload={"cond": a["cond"], "batch_no": a["batch_no"]})

    return {
        "ok": True,
        "token": token,
        "study": study,
        "cond": a["cond"],
        "batch_no": a["batch_no"],
        "test": False,
        "platform_version": config.PLATFORM_VERSION,
        "config_version": _config_version(study),
    }


def _apply_event_side_effects(ev: EventReq) -> None:
    if ev.type in _AUTO_TYPES_SET_USE and ev.study == "s4":
        db.set_any_use(ev.token)
    if ev.type == "consent_accept":
        db.update_participant(ev.token, consent_ts=events._now_iso())
    if ev.type == "withdraw":
        db.update_participant(ev.token, status="withdrawn")


@app.post("/api/event")
def log_event(req: EventReq) -> dict[str, Any]:
    if not db.is_known_token(req.token):     # reject fabricated tokens (no orphan rows)
        return {"ok": False, "reason": "unknown_token"}
    res = events.record_event(
        req.study, req.token, req.type, page=req.page, payload=req.payload,
        client_ts=req.client_ts, seq=req.seq, client_event_id=req.client_event_id,
    )
    _apply_event_side_effects(req)
    return res


@app.post("/api/events/batch")
def log_batch(req: BatchReq) -> dict[str, Any]:
    n = 0
    for ev in req.events:
        if not db.is_known_token(ev.token):
            continue
        events.record_event(
            ev.study, ev.token, ev.type, page=ev.page, payload=ev.payload,
            client_ts=ev.client_ts, seq=ev.seq, client_event_id=ev.client_event_id,
        )
        _apply_event_side_effects(ev)
        n += 1
    return {"ok": True, "logged": n}


@app.post("/api/s4/assistant")
async def s4_assistant(req: AssistantReq) -> dict[str, Any]:
    known = await run_in_threadpool(db.is_known_token, req.token)
    if not known:                              # reject fabricated tokens
        return {"ok": False, "reason": "unknown_token"}

    used = await run_in_threadpool(db.count_assistant_requests, req.token)
    if used >= config.LLM_REQUEST_CAP:
        await run_in_threadpool(
            events.record_event, "s4", req.token, "assistant_cap_hit", "writing",
            {"request_no": used},
        )
        return {"ok": False, "reason": "cap",
                "text": "You've reached the assistant request limit for this session."}

    user_text = req.user_message or req.selected or ""
    if llm_proxy.is_blocked(user_text):
        await run_in_threadpool(
            events.record_event, "s4", req.token, "assistant_blocked", "writing",
            {"affordance": req.affordance},
        )
        return {"ok": False, "reason": "blocked",
                "text": "I can't help with that request, but I'm happy to help with your story."}

    request_no = used + 1
    await run_in_threadpool(
        events.record_event, "s4", req.token, "assistant_request", "writing",
        {
            "affordance": req.affordance,
            "request_no": request_no,
            "draft_len": len(req.draft or ""),
            "selected_text": req.selected or "",
            "user_message": req.user_message or "",
            "prompt_theme": req.prompt_theme or "",
        },
    )

    result = await llm_proxy.generate(
        req.affordance, draft=req.draft or "", selected=req.selected or "",
        prompt_theme=req.prompt_theme or "", user_message=req.user_message or "",
    )

    await run_in_threadpool(
        events.record_event, "s4", req.token, "assistant_response", "writing",
        {
            "affordance": req.affordance,
            "request_no": request_no,
            "response_text": result["text"],
            "source": result["source"],
            "model": result["model"],
            "latency_ms": result["latency_ms"],
            "error": result["error"],
        },
    )
    return {
        "ok": True, "text": result["text"], "source": result["source"],
        "model": result["model"], "request_no": request_no,
        "remaining": max(0, config.LLM_REQUEST_CAP - request_no),
    }


@app.post("/api/session/complete")
def session_complete(req: CompleteReq) -> dict[str, Any]:
    if not db.is_known_token(req.token):
        return {"ok": False, "reason": "unknown_token"}
    code = config.COMPLETION_CODES.get(req.study, "COMPLETE")
    db.update_participant(
        req.token, status="completed", completed_ts=events._now_iso(), completion_code=code
    )
    events.record_event(req.study, req.token, "session_complete", page="done",
                        payload=req.summary or {})
    return {"ok": True, "completion_code": code}


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
def _check_key(request: Request) -> bool:
    key = request.query_params.get("key") or request.headers.get("x-admin-key")
    return key == config.ADMIN_TOKEN


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request) -> HTMLResponse:
    if not _check_key(request):
        return HTMLResponse(admin.render_login(), status_code=401)
    return HTMLResponse(admin.render_dashboard(admin.gather_stats()))


@app.get("/api/admin/stats")
def admin_stats(request: Request) -> JSONResponse:
    if not _check_key(request):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    return JSONResponse(admin.gather_stats())


@app.post("/api/admin/intake")
def admin_intake(request: Request) -> JSONResponse:
    if not _check_key(request):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    open_flag = request.query_params.get("open", "true").lower() in ("1", "true", "yes")
    db.set_setting("intake_open", "1" if open_flag else "0")
    return JSONResponse({"ok": True, "intake_open": open_flag})


@app.get("/api/admin/export")
def admin_export(request: Request) -> Response:
    if not _check_key(request):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    study = request.query_params.get("study", "s3")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        sdir = config.DATA_DIR / study
        for f in sorted(sdir.glob("*.jsonl")):
            z.write(f, arcname=f"{study}/{f.name}")
        z.writestr(f"{study}/participants.csv", admin.participants_csv(study))
        z.writestr("MANIFEST.txt",
                   f"platform_version={config.PLATFORM_VERSION}\nstudy={study}\n"
                   f"exported_at={events._now_iso()}\n")
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{study}_export.zip"'})


_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no ambiguous chars (0/O/1/I/L)


def _gen_code(n: int = 8) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))


@app.post("/api/admin/invites/generate")
def admin_invites_generate(request: Request) -> JSONResponse:
    if not _check_key(request):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    try:
        count = max(1, min(2000, int(request.query_params.get("count", "50"))))
    except ValueError:
        count = 50
    mu = request.query_params.get("max_uses", "1").strip().lower()
    if mu in ("", "0", "unlimited", "inf"):
        max_uses: Optional[int] = None
    elif mu.isdigit():
        max_uses = max(1, int(mu))
    else:
        max_uses = 1
    label = (request.query_params.get("label", "") or "")[:80]
    codes = []
    for _ in range(count):
        c = _gen_code()
        db.insert_invite(c, label, max_uses, is_test=0)
        codes.append(c)
    return JSONResponse({"ok": True, "codes": codes, "max_uses": max_uses, "count": len(codes)})


@app.get("/api/admin/invites")
def admin_invites_list(request: Request) -> JSONResponse:
    if not _check_key(request):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    return JSONResponse({
        "ok": True,
        "invite_required": config.INVITE_REQUIRED,
        "test_code": config.INVITE_TEST_CODE,
        "invites": db.list_invites(),
    })


# --------------------------------------------------------------------------- #
# Static frontend (mounted LAST so API routes win)
# --------------------------------------------------------------------------- #
if config.WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")
