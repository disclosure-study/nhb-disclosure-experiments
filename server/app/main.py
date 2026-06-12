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


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class StartReq(BaseModel):
    study: str
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
        "llm_provider": config.LLM_PROVIDER,
        "llm_model": config.LLM_MODEL if config.LLM_PROVIDER != "offline" else "offline-canned",
    }


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #
_AUTO_TYPES_SET_USE = {"assistant_insert", "any_use_true"}


@app.post("/api/session/start")
def session_start(req: StartReq) -> dict[str, Any]:
    study = req.study.lower()
    if study not in ("s3", "s4"):
        return {"ok": False, "reason": "bad_study"}
    if not db.intake_open():
        return {"ok": False, "reason": "intake_closed"}

    pid = (req.prolific or {}).get("pid") or (req.prolific or {}).get("PROLIFIC_PID")
    pid_hash = _hash_pid(pid)
    if pid_hash and db.pid_already_used(study, pid_hash):
        return {"ok": False, "reason": "already_participated"}

    a = randomizer.assign(study)
    token = secrets.token_urlsafe(16)

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
        events.record_event(
            ev.study, ev.token, ev.type, page=ev.page, payload=ev.payload,
            client_ts=ev.client_ts, seq=ev.seq, client_event_id=ev.client_event_id,
        )
        _apply_event_side_effects(ev)
        n += 1
    return {"ok": True, "logged": n}


@app.post("/api/s4/assistant")
async def s4_assistant(req: AssistantReq) -> dict[str, Any]:
    part = await run_in_threadpool(db.get_participant, req.token)
    if part is None:
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


# --------------------------------------------------------------------------- #
# Static frontend (mounted LAST so API routes win)
# --------------------------------------------------------------------------- #
if config.WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")
