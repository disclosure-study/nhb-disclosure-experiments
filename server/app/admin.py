"""
Admin / live-monitoring dashboard.

Surfaces exactly what EXPERIMENT_PLATFORM.md §3.5 asks for: live cell counts, the
Study-4 conditional AI-user-per-regime counter (the adaptive top-up rule reads
this), completion counts, a recent-event feed, and a kill switch.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from . import config, db


def _s3_breakdown(cells: dict[str, int]) -> dict[str, Any]:
    arm_totals = {a: 0 for a in config.S3_ARMS}
    story_totals = {s: 0 for s in config.S3_STORIES}
    grid = {}
    for key, n in cells.items():
        try:
            c = json.loads(key)
        except Exception:
            continue
        arm_totals[c.get("arm", "?")] = arm_totals.get(c.get("arm", "?"), 0) + n
        story_totals[c.get("story", "?")] = story_totals.get(c.get("story", "?"), 0) + n
        grid[f'{c.get("arm")} | {c.get("story")}'] = n
    return {"grid": grid, "arm_totals": arm_totals, "story_totals": story_totals}


def _s4_breakdown(cells: dict[str, int]) -> dict[str, Any]:
    regime_totals = {r: 0 for r in config.S4_REGIMES}
    for key, n in cells.items():
        try:
            c = json.loads(key)
        except Exception:
            continue
        regime_totals[c.get("regime", "?")] = regime_totals.get(c.get("regime", "?"), 0) + n
    return {"regime_totals": regime_totals}


def gather_stats() -> dict[str, Any]:
    s3_cells = db.cell_counts("s3")
    s4_cells = db.cell_counts("s4")
    s3_tot = db.study_totals("s3")
    s4_tot = db.study_totals("s4")
    aiusers = db.s4_aiusers_by_regime()

    topup = {}
    all_done = True
    for r in config.S4_REGIMES:
        have = aiusers.get(r, 0)
        done = have >= config.S4_AIUSER_TARGET
        all_done = all_done and done
        topup[r] = {"have": have, "need": config.S4_AIUSER_TARGET, "done": done}

    return {
        "ok": True,
        "platform_version": config.PLATFORM_VERSION,
        "intake_open": db.intake_open(),
        "llm_provider": config.LLM_PROVIDER,
        "s3": {
            "total": s3_tot["total"],
            "completed": s3_tot["completed"],
            "target_per_arm": config.S3_PER_ARM_TARGET,
            **_s3_breakdown(s3_cells),
        },
        "s4": {
            "total": s4_tot["total"],
            "completed": s4_tot["completed"],
            "hard_cap": config.S4_HARD_CAP,
            "aiuser_target": config.S4_AIUSER_TARGET,
            "aiusers_by_regime": aiusers,
            "topup": topup,
            "topup_complete": all_done,
            **_s4_breakdown(s4_cells),
        },
        "recent": db.recent_events(30),
    }


def participants_csv(study: str) -> str:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT token, study, pid_hash, cond_json, rng_seed, batch_no, status, "
            "any_use, consent_ts, started_ts, completed_ts, completion_code, platform_version "
            "FROM participants WHERE study=? ORDER BY started_ts",
            (study,),
        ).fetchall()
    finally:
        conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([r[k] for k in r.keys()])
    else:
        w.writerow(["token", "study", "pid_hash", "cond_json", "rng_seed", "batch_no",
                    "status", "any_use", "consent_ts", "started_ts", "completed_ts",
                    "completion_code", "platform_version"])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #
def render_login() -> str:
    return """<!doctype html><html><head><meta charset="utf-8">
<title>Admin — login</title>
<style>body{font-family:system-ui,sans-serif;max-width:480px;margin:8vh auto;padding:0 1rem}
input{padding:.5rem;width:100%;font-size:1rem;box-sizing:border-box}
button{padding:.5rem 1rem;margin-top:.75rem;font-size:1rem;cursor:pointer}</style></head>
<body><h2>Admin dashboard</h2><p>Enter the admin key.</p>
<input id="k" type="password" placeholder="admin key" autofocus>
<button onclick="location.href='/admin?key='+encodeURIComponent(document.getElementById('k').value)">Open dashboard</button>
</body></html>"""


def render_dashboard(_stats: dict[str, Any] | None = None, key: str = "") -> str:
    # The shell polls /api/admin/stats live; key is read from the current URL.
    return """<!doctype html><html><head><meta charset="utf-8">
<title>Experiment monitor</title>
<style>
:root{--ink:#14213d;--muted:#6b7280;--ok:#157f3b;--warn:#b45309;--line:#e5e7eb}
body{font-family:system-ui,-apple-system,sans-serif;margin:0;color:var(--ink);background:#f7f8fa}
header{background:#14213d;color:#fff;padding:.8rem 1.2rem;display:flex;justify-content:space-between;align-items:center}
header .v{font-size:.8rem;opacity:.8}
main{max-width:1100px;margin:1.2rem auto;padding:0 1.2rem}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:1rem 1.2rem;margin-bottom:1.2rem}
h2{margin:.2rem 0 .8rem;font-size:1.1rem}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{text-align:left;padding:.35rem .6rem;border-bottom:1px solid var(--line)}
.bar{height:10px;background:#eef0f4;border-radius:6px;overflow:hidden}
.bar>i{display:block;height:100%;background:#2563eb}
.bar>i.done{background:var(--ok)}
.pill{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:.78rem}
.pill.open{background:#dcfce7;color:var(--ok)}.pill.closed{background:#fee2e2;color:#b91c1c}
.kill{background:#b91c1c;color:#fff;border:0;border-radius:8px;padding:.5rem .9rem;cursor:pointer}
.open-btn{background:#157f3b;color:#fff;border:0;border-radius:8px;padding:.5rem .9rem;cursor:pointer}
.feed{font-family:ui-monospace,monospace;font-size:.8rem;max-height:240px;overflow:auto}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}
@media(max-width:760px){.grid2{grid-template-columns:1fr}}
small.m{color:var(--muted)}
a.btn{display:inline-block;margin-right:.6rem;font-size:.85rem}
</style></head><body>
<header><div><strong>Experiment monitor</strong> &nbsp;<span class="v" id="ver"></span></div>
<div id="intake"></div></header>
<main>
<div class="card"><h2>Recruitment intake</h2>
<p>Status: <span id="intakePill"></span> &nbsp; <small class="m">closing intake stops new participants; those mid-study still finish and get paid.</small></p>
<button class="kill" onclick="setIntake(false)">Close intake (kill switch)</button>
<button class="open-btn" onclick="setIntake(true)">Re-open intake</button>
</div>
<div class="grid2">
  <div class="card"><h2>Study 3 — label experiment</h2>
    <p><strong id="s3tot">0</strong> started · <strong id="s3comp">0</strong> completed · target <span id="s3target"></span>/arm</p>
    <div id="s3arms"></div>
    <h3 style="font-size:.95rem">Cell fill (arm × story)</h3>
    <table id="s3grid"></table>
    <p><a class="btn" id="s3exp" href="#">⬇ export s3 (zip)</a></p>
  </div>
  <div class="card"><h2>Study 4 — supply-side experiment</h2>
    <p><strong id="s4tot">0</strong> started · <strong id="s4comp">0</strong> completed · cap <span id="s4cap"></span></p>
    <h3 style="font-size:.95rem">Adaptive top-up — AI-users per regime (need <span id="s4need"></span>)</h3>
    <div id="s4topup"></div>
    <p id="s4done"></p>
    <p><a class="btn" id="s4exp" href="#">⬇ export s4 (zip)</a></p>
  </div>
</div>
<div class="card"><h2>Recent events</h2><div class="feed" id="feed"></div></div>
</main>
<script>
const KEY = new URLSearchParams(location.search).get('key') || '';
function hdr(){return {'x-admin-key':KEY};}
async function setIntake(open){
  await fetch('/api/admin/intake?open='+open,{method:'POST',headers:hdr()});
  load();
}
function bar(have,need){
  const pct=Math.min(100,Math.round(100*have/need));
  const done=have>=need;
  return `<div class="bar"><i class="${done?'done':''}" style="width:${pct}%"></i></div>
    <small class="m">${have} / ${need} ${done?'✓':''}</small>`;
}
async function load(){
  let s; try{ s=await (await fetch('/api/admin/stats?key='+encodeURIComponent(KEY))).json(); }catch(e){return;}
  if(!s.ok) return;
  document.getElementById('ver').textContent = s.platform_version+' · LLM:'+s.llm_provider;
  const ip=document.getElementById('intakePill');
  ip.className='pill '+(s.intake_open?'open':'closed');
  ip.textContent=s.intake_open?'OPEN':'CLOSED';
  // S3
  document.getElementById('s3tot').textContent=s.s3.total;
  document.getElementById('s3comp').textContent=s.s3.completed;
  document.getElementById('s3target').textContent=s.s3.target_per_arm;
  let arms=''; for(const[a,n] of Object.entries(s.s3.arm_totals)){arms+=`<div style="margin:.3rem 0"><small class="m">${a}</small> ${bar(n,s.s3.target_per_arm)}</div>`;}
  document.getElementById('s3arms').innerHTML=arms;
  let g='<tr><th>cell</th><th>n</th></tr>';
  for(const[c,n] of Object.entries(s.s3.grid)){g+=`<tr><td>${c}</td><td>${n}</td></tr>`;}
  document.getElementById('s3grid').innerHTML=g;
  // S4
  document.getElementById('s4tot').textContent=s.s4.total;
  document.getElementById('s4comp').textContent=s.s4.completed;
  document.getElementById('s4cap').textContent=s.s4.hard_cap;
  document.getElementById('s4need').textContent=s.s4.aiuser_target;
  let t=''; for(const[r,o] of Object.entries(s.s4.topup)){t+=`<div style="margin:.4rem 0"><small class="m">${r}</small> ${bar(o.have,o.need)}</div>`;}
  document.getElementById('s4topup').innerHTML=t;
  document.getElementById('s4done').innerHTML = s.s4.topup_complete
    ? '<span class="pill open">All regimes at target — stopping rule met</span>'
    : '<small class="m">recruiting in batches of '+'150'+'…</small>';
  document.getElementById('s3exp').href='/api/admin/export?study=s3&key='+encodeURIComponent(KEY);
  document.getElementById('s4exp').href='/api/admin/export?study=s4&key='+encodeURIComponent(KEY);
  // feed
  let f=''; for(const e of s.recent){f+=`<div>${e.server_ts.slice(11,19)} · <b>${e.study}</b> · ${e.type} <span class="m">${e.page||''}</span> · ${e.token.slice(0,6)}…</div>`;}
  document.getElementById('feed').innerHTML=f;
}
load(); setInterval(load, 5000);
</script></body></html>"""
