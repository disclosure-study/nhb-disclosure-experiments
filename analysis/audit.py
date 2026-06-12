"""
Raw-logs -> numbers audit (stdlib only).

Reproduces the project-wide discipline from Studies 1-2: every reported number is
recomputed from the immutable write-ahead JSONL, never from a hand-maintained
spreadsheet. This script materializes one tidy row per participant from
events.jsonl + sessions.jsonl, writes <study>_tidy.csv, and emits a
NUMBERS_FOR_MS.md with the platform version in its header.

    python analysis/audit.py --study s3 --data server/data/s3
    python analysis/audit.py --study s4 --data server/data/s4

The inferential models (HC2 regressions, causal mediation, logistic regimes) live
in the study analysis folders (s3_models.py / s4_models.py); this audit recomputes
the descriptive quantities and the randomization / exclusion checks those models
are reported alongside.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

try:  # ensure non-ASCII (e.g. the minus sign) prints on Windows consoles
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def materialize(data_dir: Path):
    sessions = {s["token"]: s for s in load_jsonl(data_dir / "sessions.jsonl")}
    events = load_jsonl(data_dir / "events.jsonl")
    by_token: dict[str, list] = defaultdict(list)
    for e in events:
        by_token[e["token"]].append(e)

    rows = {}
    platform_versions = set()
    for token, evs in by_token.items():
        sess = sessions.get(token, {})
        cond = sess.get("cond", {})
        row = {"token": token, **{f"cond_{k}": v for k, v in cond.items()},
               "completed": 0, "withdrawn": 0, "any_use": 0,
               "responses": {}, "n_assistant_requests": 0, "n_assistant_inserts": 0}
        for e in evs:
            platform_versions.add(e.get("v"))
            t, p = e["type"], e.get("payload", {})
            if t == "responses":
                row["responses"].update(p.get("values", {}))
                if "comprehension_pass" in p:
                    row["comprehension_pass"] = p["comprehension_pass"]
            elif t == "session_complete":
                row["completed"] = 1
            elif t == "withdraw":
                row["withdrawn"] = 1
            elif t == "story_read":
                row["story_dwell_ms"] = p.get("dwell_ms")
                row["scroll_max_pct"] = p.get("scroll_max_pct")
            elif t == "click_next":
                row["click_next"] = p.get("choice")
                row["choice_latency_ms"] = p.get("choice_latency_ms")
            elif t == "label_choice":
                row["label_choice"] = p.get("label_choice")
                row["decision_latency_ms"] = p.get("decision_latency_ms")
                row["n_toggles"] = p.get("n_toggles")
            elif t == "writing_final":
                row["word_count"] = p.get("word_count")
                row["writing_ms"] = p.get("writing_ms")
            elif t == "assistant_request":
                row["n_assistant_requests"] += 1
            elif t == "assistant_insert":
                row["n_assistant_inserts"] += 1
                row["any_use"] = 1
            elif t == "any_use_true":
                row["any_use"] = 1
        rows[token] = row
    return rows, platform_versions


def _num(rows, key):
    vals = []
    for r in rows:
        v = r["responses"].get(key)
        if isinstance(v, (int, float)):
            vals.append(v)
        elif isinstance(v, dict) and isinstance(v.get("value"), (int, float)):
            vals.append(v["value"])
    return vals


def _mean(xs):
    return round(st.mean(xs), 3) if xs else None


def summarize_s3(rows):
    out = ["## Study 3 — descriptive recomputation\n"]
    by_arm = defaultdict(list)
    for r in rows.values():
        by_arm[r.get("cond_arm", "?")].append(r)
    out.append(f"- N (rows with a condition): **{len(rows)}**")
    for arm, rs in sorted(by_arm.items()):
        kud = _num(rs, "KUD1")
        clicks = [r.get("click_next") for r in rs if r.get("click_next")]
        click_rate = round(sum(1 for c in clicks if c == "read") / len(clicks), 3) if clicks else None
        out.append(f"- **{arm}**: n={len(rs)} · KUD1 mean={_mean(kud)} (n={len(kud)}) · "
                   f"click-through(read)={click_rate}")
    a0 = _num(by_arm.get("L0_control", []), "KUD1")
    a1 = _num(by_arm.get("L1_disclosed", []), "KUD1")
    if a0 and a1:
        out.append(f"- **KUD1 control − disclosed = {round(_mean(a0) - _mean(a1), 3)}** "
                   "(raw mean difference; HC2 model in s3_models.py)")
    # exclusions (DESIGN §8)
    excl = {"incomplete": 0, "attention_fail": 0, "dwell_lt_60s": 0, "comprehension_fail": 0}
    for r in rows.values():
        if not r.get("completed"):
            excl["incomplete"] += 1
        att = r["responses"].get("ATTN1")
        if isinstance(att, dict) and att.get("pass") is False:
            excl["attention_fail"] += 1
        if isinstance(r.get("story_dwell_ms"), (int, float)) and r["story_dwell_ms"] < 60000:
            excl["dwell_lt_60s"] += 1
        if r.get("comprehension_pass") is False:
            excl["comprehension_fail"] += 1
    out.append(f"- Exclusion flags (pre-registered order): {json.dumps(excl)}")
    return "\n".join(out)


def summarize_s4(rows):
    out = ["## Study 4 — descriptive recomputation\n"]
    by_reg = defaultdict(list)
    for r in rows.values():
        by_reg[r.get("cond_regime", "?")].append(r)
    out.append(f"- N (rows with a condition): **{len(rows)}**")
    AI_LABELS = {"assisted_polish", "assisted_drafting", "created_with_ai"}
    for reg, rs in sorted(by_reg.items()):
        users = [r for r in rs if r.get("any_use") == 1]
        use_rate = round(len(users) / len(rs), 3) if rs else None
        truthful = [1 if r.get("label_choice") in AI_LABELS else 0 for r in users]
        truthful_rate = round(sum(truthful) / len(truthful), 3) if truthful else None
        out.append(f"- **{reg}**: n={len(rs)} · any_use rate={use_rate} "
                   f"(randomization check — should be flat) · AI-users={len(users)} · "
                   f"**truthful-disclosure rate among users={truthful_rate}**")
    # honesty cross-check: self-report vs logs
    mism = 0
    n_users = 0
    for r in rows.values():
        if r.get("any_use") == 1:
            n_users += 1
            sr = r["responses"].get("self_reported_use")
            if sr == "none":
                mism += 1
    if n_users:
        out.append(f"- Honesty cross-check: {mism}/{n_users} logged AI-users self-reported 'none' "
                   f"({round(mism / n_users, 3)})")
    return "\n".join(out)


def write_tidy(rows, out_csv: Path):
    flat = []
    keys = set()
    for r in rows.values():
        base = {k: v for k, v in r.items() if k != "responses"}
        for ik, iv in r["responses"].items():
            base[f"resp_{ik}"] = iv if not isinstance(iv, dict) else json.dumps(iv)
        flat.append(base)
        keys.update(base.keys())
    keys = ["token"] + sorted(k for k in keys if k != "token")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in flat:
            w.writerow(r)


def run(study: str, data_dir: Path, out_dir: Path):
    rows, versions = materialize(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_tidy(rows, out_dir / f"{study}_tidy.csv")
    body = summarize_s3(rows) if study == "s3" else summarize_s4(rows)
    header = (f"# NUMBERS_FOR_MS — {study.upper()} (auto-recomputed from raw JSONL)\n\n"
              f"- platform_version(s) in logs: {sorted(v for v in versions if v)}\n"
              f"- source: `{data_dir}` · participants: {len(rows)}\n"
              f"- tidy data: `{study}_tidy.csv`\n\n")
    (out_dir / "NUMBERS_FOR_MS.md").write_text(header + body + "\n", encoding="utf-8")
    print(f"[audit] {study}: {len(rows)} participants -> {out_dir/'NUMBERS_FOR_MS.md'}")
    print(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", choices=["s3", "s4"], required=True)
    ap.add_argument("--data", required=True, help="folder with events.jsonl + sessions.jsonl")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    data_dir = Path(a.data)
    out_dir = Path(a.out) if a.out else data_dir / "audit_out"
    run(a.study, data_dir, out_dir)


if __name__ == "__main__":
    main()
