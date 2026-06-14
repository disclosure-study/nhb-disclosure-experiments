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
import re
import secrets
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
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


# Hard ceiling on any request body, enforced BEFORE FastAPI parses the form (so an
# oversized multipart upload is rejected before it is ever spooled to a temp file).
# Caddy also caps bodies at the edge; this keeps the app safe even if run without it.
_MAX_BODY_BYTES = 4 * 1024 * 1024


@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        cl = request.headers.get("content-length", "")
        if cl.isdigit() and int(cl) > _MAX_BODY_BYTES:
            return JSONResponse({"ok": False, "reason": "too_large"}, status_code=413)
    return await call_next(request)


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
        # Use the LAST hop — the address our own reverse proxy (Caddy) appended.
        # The leftmost entries are client-supplied and spoofable, so don't trust them.
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "?"


def _invite_rate_limited(ip: str) -> bool:
    now = time.time()
    fails = [t for t in _INVITE_FAILS.get(ip, []) if now - t < _FAIL_WINDOW]
    _INVITE_FAILS[ip] = fails
    return len(fails) >= _FAIL_MAX


def _record_invite_fail(ip: str) -> None:
    _INVITE_FAILS.setdefault(ip, []).append(time.time())


# Anti-spam throttle for the public "apply / leave a message" form.
_APPLY_HITS: dict[str, list[float]] = {}
_APPLY_WINDOW = 600.0
_APPLY_MAX = 8


def _apply_rate_limited(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _APPLY_HITS.get(ip, []) if now - t < _APPLY_WINDOW]
    _APPLY_HITS[ip] = hits
    return len(hits) >= _APPLY_MAX


# Throttle the payment-details endpoint per IP (it accepts file uploads, so it is
# a more expensive surface than a plain form post).
_PAYMENT_HITS: dict[str, list[float]] = {}
_PAYMENT_WINDOW = 600.0
_PAYMENT_MAX = 6


def _payment_rate_limited(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _PAYMENT_HITS.get(ip, []) if now - t < _PAYMENT_WINDOW]
    _PAYMENT_HITS[ip] = hits
    return len(hits) >= _PAYMENT_MAX


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


class ApplyReq(BaseModel):
    name: str | None = ""
    contact: str | None = ""
    message: str


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "platform_version": config.PLATFORM_VERSION,
        "intake_open": db.intake_open(),
        "experiment_status": db.experiment_status(),
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

    pid = (req.prolific or {}).get("pid") or (req.prolific or {}).get("PROLIFIC_PID")
    pid_hash = _hash_pid(pid)
    is_test = False

    if db.experiment_closed():
        # Demonstration mode: data collection is finished. Only the test code works
        # (a no-store demo); real codes are turned away with the "finished" page.
        row = db.peek_invite(req.invite or "")
        if not (row and row["is_test"]):
            return {"ok": False, "reason": "experiment_closed"}
        is_test = True
    else:
        if not db.intake_open():
            return {"ok": False, "reason": "intake_closed"}
        if pid_hash and db.pid_already_used(study, pid_hash):
            return {"ok": False, "reason": "already_participated"}
        # Invitation-code gate. A limited-use code is consumed only here, once we
        # know intake is open and this PID has not already taken part.
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
    if db.is_test_token(req.token):       # test sessions get a distinct, non-payable code
        return {"ok": True, "completion_code": "TEST-COMPLETE", "test": True}
    code = config.COMPLETION_CODES.get(req.study, "COMPLETE")
    db.update_participant(
        req.token, status="completed", completed_ts=events._now_iso(), completion_code=code
    )
    events.record_event(req.study, req.token, "session_complete", page="done",
                        payload=req.summary or {})
    return {"ok": True, "completion_code": code}


@app.post("/api/apply")
def apply(req: ApplyReq, request: Request) -> dict[str, Any]:
    """Public 'apply to take part / leave a message' form (shown when the
    experiment is finished). Stored for the researcher; rate-limited per IP."""
    ip = _client_ip(request)
    if _apply_rate_limited(ip):
        return {"ok": False, "reason": "rate_limited"}
    message = (req.message or "").strip()[:2000]
    if not message:
        return {"ok": False, "reason": "empty"}
    # Store a hashed (pseudonymous) IP only — enough to spot abuse, not raw PII.
    db.insert_application(
        (req.name or "").strip()[:200], (req.contact or "").strip()[:200], message,
        meta=(_hash_pid(ip) or "")[:16],
    )
    _APPLY_HITS.setdefault(ip, []).append(time.time())
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Payment details (participant-provided receiving method incl. a QR-code image)
# --------------------------------------------------------------------------- #
PAYMENTS_DIR = config.DATA_DIR / "payments"
_IMG_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
            "image/gif": ".gif", "image/webp": ".webp"}
_PAYMENT_MAX_BYTES = 3 * 1024 * 1024
_SAFE_QR_NAME = re.compile(r"^[a-f0-9]{16}\.(png|jpg|gif|webp)$")


@app.post("/api/payment")
async def payment(
    request: Request,
    token: str = Form(...),
    study: str = Form(""),
    method: str = Form(""),
    account: str = Form(""),
    name: str = Form(""),
    note: str = Form(""),
    qr: UploadFile | None = File(None),
) -> dict[str, Any]:
    if not await run_in_threadpool(db.is_known_token, token):
        return {"ok": False, "reason": "unknown_token"}
    if await run_in_threadpool(db.is_test_token, token):
        return {"ok": True, "test": True}          # demo session: store nothing
    if _payment_rate_limited(_client_ip(request)):
        return {"ok": False, "reason": "rate_limited"}

    method = (method or "")[:40]
    account = (account or "").strip()[:200]
    name = (name or "").strip()[:120]
    note = (note or "").strip()[:500]

    qr_file = ""
    qr_type = ""
    if qr is not None and qr.filename:
        ct = (qr.content_type or "").lower()
        ext = _IMG_EXT.get(ct)
        if not ext:
            return {"ok": False, "reason": "bad_image_type"}
        # Read in bounded chunks and abort the moment we cross the cap, so we never
        # build a >3 MB bytes object in RAM (the old post-read len() check allocated
        # the whole upload first). The body itself is already bounded before it gets
        # here: the Content-Length middleware + Caddy reject anything over 4 MB.
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await qr.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > _PAYMENT_MAX_BYTES:
                return {"ok": False, "reason": "too_large"}
            chunks.append(chunk)
        content = b"".join(chunks)
        PAYMENTS_DIR.mkdir(parents=True, exist_ok=True)
        qr_file = secrets.token_hex(8) + ext      # random name; never trust the upload's filename
        qr_type = ct
        await run_in_threadpool((PAYMENTS_DIR / qr_file).write_bytes, content)

    if not (account or note or qr_file):
        return {"ok": False, "reason": "empty"}
    _PAYMENT_HITS.setdefault(_client_ip(request), []).append(time.time())
    # One row per token: a re-submission overwrites the old one and we delete the
    # now-orphaned QR image, so a reusable token can't flood the table or the disk.
    old_qr = await run_in_threadpool(
        db.upsert_payment, token, study, method, account, name, note, qr_file, qr_type)
    if old_qr and _SAFE_QR_NAME.match(old_qr):
        try:
            await run_in_threadpool((PAYMENTS_DIR / old_qr).unlink, True)
        except Exception:
            pass
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
_ADMIN_COOKIE = "admin_session"
_NOREF = {"Referrer-Policy": "no-referrer"}
# For responses carrying participant PII (payment accounts / QR images): also tell
# the admin's browser never to write them to its on-disk cache.
_PII_HDRS = {"Referrer-Policy": "no-referrer", "Cache-Control": "no-store", "Pragma": "no-cache"}


def _key_ok(key: Optional[str]) -> bool:
    # Constant-time compare so an attacker can't recover the token byte-by-byte.
    if not key:
        return False
    return secrets.compare_digest(key, config.ADMIN_TOKEN)


def _check_key(request: Request) -> bool:
    # Header / cookie are preferred (never logged in the request line); the query
    # param is kept only as a bootstrap + backward-compatible fallback.
    return (
        _key_ok(request.headers.get("x-admin-key"))
        or _key_ok(request.cookies.get(_ADMIN_COOKIE))
        or _key_ok(request.query_params.get("key"))
    )


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https" or request.url.scheme == "https"


def _set_admin_cookie(resp: Response, request: Request) -> None:
    # HttpOnly (JS can't read it) + SameSite=strict; Secure only over HTTPS so the
    # cookie still works for local http testing. The key then rides requests
    # (incl. <img> QR thumbnails) automatically, so it never appears in any URL.
    resp.set_cookie(
        _ADMIN_COOKIE, config.ADMIN_TOKEN, max_age=12 * 3600, httponly=True,
        samesite="strict", secure=_is_https(request), path="/",
    )


@app.post("/api/admin/login")
def admin_login(request: Request) -> JSONResponse:
    # The dashboard posts the key here (in a header, never a URL) and gets an
    # HttpOnly cookie back; this keeps the admin key out of browser history,
    # access logs, and Referer headers.
    if not _key_ok(request.headers.get("x-admin-key") or request.query_params.get("key")):
        return JSONResponse({"ok": False}, status_code=401, headers=_NOREF)
    resp = JSONResponse({"ok": True}, headers=_NOREF)
    _set_admin_cookie(resp, request)
    return resp


@app.post("/api/admin/logout")
def admin_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True}, headers=_NOREF)
    resp.delete_cookie(_ADMIN_COOKIE, path="/")
    return resp


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request) -> HTMLResponse:
    if not _check_key(request):
        return HTMLResponse(admin.render_login(), status_code=401, headers=_NOREF)
    resp = HTMLResponse(admin.render_dashboard(admin.gather_stats()), headers=_PII_HDRS)
    _set_admin_cookie(resp, request)   # refresh / establish the cookie session
    return resp


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


@app.post("/api/admin/experiment")
def admin_experiment(request: Request) -> JSONResponse:
    if not _check_key(request):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    status = request.query_params.get("status", "open").lower()
    status = "closed" if status in ("closed", "done", "stop", "finished") else "open"
    db.set_setting("experiment_status", status)
    return JSONResponse({"ok": True, "experiment_status": status})


@app.get("/api/admin/applications")
def admin_applications(request: Request) -> JSONResponse:
    if not _check_key(request):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    return JSONResponse({"ok": True, "applications": db.list_applications()})


@app.get("/api/admin/payments")
def admin_payments(request: Request) -> JSONResponse:
    if not _check_key(request):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401, headers=_NOREF)
    return JSONResponse({"ok": True, "payments": db.list_payments()}, headers=_PII_HDRS)


@app.get("/api/admin/payment-qr")
def admin_payment_qr(request: Request) -> Response:
    if not _check_key(request):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401, headers=_NOREF)
    try:
        pid = int(request.query_params.get("id", "0"))
    except ValueError:
        return JSONResponse({"ok": False}, status_code=400, headers=_NOREF)
    row = db.get_payment(pid)
    if not row or not row["qr_file"] or not _SAFE_QR_NAME.match(row["qr_file"]):
        return JSONResponse({"ok": False}, status_code=404, headers=_NOREF)
    path = PAYMENTS_DIR / row["qr_file"]
    if not path.exists():
        return JSONResponse({"ok": False}, status_code=404, headers=_NOREF)
    return FileResponse(
        str(path), media_type=row["qr_type"] or "application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline",
                 "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                 "Pragma": "no-cache"},
    )


# --------------------------------------------------------------------------- #
# Static frontend (mounted LAST so API routes win)
# --------------------------------------------------------------------------- #
if config.WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")
