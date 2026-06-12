# Platform notes — architecture, provenance, and the build-vs-oTree decision

This file is the methods-appendix companion to the code. It documents the
engineering choices so reviewers can audit the instrument, and records the one
deliberate deviation from `../EXPERIMENT_PLATFORM.md`.

## 1. The oTree deviation (deliberate, scientifically neutral)

`EXPERIMENT_PLATFORM.md` §2 recommends **oTree 5** as the base. This platform
instead uses a **thin FastAPI JSON API + a config-driven static frontend**. The
*scientific* design is unchanged — every arm, regime, scale item, fixed block
order, randomization rule, logging requirement, and the adaptive top-up rule is
implemented exactly as the DESIGN files specify. The change is purely the software
substrate, chosen because it serves the project's own stated goals better:

- **Deployability / a public link.** A static frontend deploys to GitHub Pages as a
  zero-config live preview, and the backend is a single container that runs on any
  PaaS. oTree production needs Redis + Postgres + `otree prodserver`.
- **The rich Study-4 interactions** (editor, embedded assistant, caret-level paste
  and insert telemetry, the live gallery) are vanilla DOM work that fights oTree's
  page model; here they are first-class.
- **Auditability.** Logging is an explicit append-only event stream
  (`events.jsonl`) rather than framework-managed model rows — closer to the
  Studies 1–2 "raw → numbers" discipline, and the whole instrument is readable as
  `config/study{3,4}.json`.
- **One frontend, two modes.** The same files are the live instrument (server
  logging) and the browser-only preview (GitHub Pages), so what reviewers click is
  what participants run.

Everything `EXPERIMENT_PLATFORM.md` §3 asked the skeleton to provide is present:
versioned consent, Prolific glue with PID hashing, stratified seeded randomization,
a write-ahead event logger, the admin dashboard with the Study-4 conditional-cell
counter and kill switch, autosave + idempotent submits, and attention/screener
components.

## 2. Provenance discipline (what is pinned and recorded)

- **Platform version** = `VERSION + git short hash` (`config.PLATFORM_VERSION`),
  stamped onto every event and session record and returned by `/api/health`. Record
  it in both `NUMBERS_FOR_MS.md` headers (the audit script does this automatically).
- **Config version** = SHA-256 (first 12 hex) of the study config JSON, captured at
  session start. Any wording change ⇒ a new hash.
- **LLM proxy** (`llm_proxy.py`) pins and logs model id, system prompt, temperature,
  max tokens (all in `config.py`); each request/response is written to the event
  stream with `source ∈ {llm, offline, offline_fallback}` and latency. The browser
  never holds the API key. A ~20-request/participant cap is enforced server-side.
- **Randomization** is reproducible: seeds derive from `RNG_BASE_SEED + arrival
  index` and are logged per assignment (`randomizer.py`).

## 3. DESIGN data-dictionary → event types

The DESIGN tables (S3 §7, S4 §7) are materialized by `analysis/audit.py` from these
event types:

| DESIGN table | Event types in `events.jsonl` |
|---|---|
| `assignment` | `session_start` (cond, rng_seed, batch_no) + `sessions.jsonl` |
| `behavior` (S3) | `page_enter`/`page_exit` (dwell), `story_read` (dwell, scroll_max_pct), `click_next` (choice, latency), `chapter2_read` |
| `responses` (S3/S4) | `responses` (values + presented item order; comprehension_pass) |
| `gallery_log` (S4) | `gallery_view` (per-regime display likes), `gallery_rate` |
| `writing` (S4) | `writing_started`, `writing_autosave` (snapshots), `writing_final` (word_count, keystroke_count, writing_ms), `focus_change` |
| `assistant_log` (S4) | `assistant_request`, `assistant_response`, `assistant_insert`, `assistant_copy`, `assistant_dismiss`, `assistant_collapse/expand`, `assistant_cap_hit`, `assistant_blocked` |
| `paste_log` (S4) | `paste` (chars, internal/external flag), `any_use_true` |
| `label` (S4) | `label_step_shown`, `label_choice` (choice, decision_latency_ms, toggle_sequence) |
| `checks` | attention item inside `responses`; `debrief_reconsent` |

`any_use ≔ ≥1 assistant_insert OR copy→external-paste of assistant text` (DESIGN
S4 §7), set on the participant record server-side.

## 4. Reliability & zero-loss

- Events are flushed and `fsync`'d to JSONL *before* the SQLite mirror — the file is
  the durable record.
- `(token, client_event_id)` is UNIQUE ⇒ refreshes and double-clicks never
  double-log; failed POSTs are buffered and flushed via `sendBeacon` on page hide.
- SQLite runs in WAL mode behind a process write-lock (fine for Prolific's ~150
  concurrent bursts; switch `DB_PATH`/add Postgres for larger). A load test
  (`platform/loadtest`, per EXPERIMENT_PLATFORM.md §4) should still be run before a
  main run.
- **Kill switch:** `/api/admin/intake?open=false` closes new intake gracefully;
  participants mid-study still finish and receive completion codes.

## 5. Privacy / GDPR (feeds the IRB text)

- Prolific PID is SHA-256-hashed with an **off-server** salt (`PID_SALT`) at
  ingestion; the raw PID is never stored. Keep the salt and the pid→hash lookup
  table off the collection server (deletion-on-request lives there).
- No third-party trackers; the LLM proxy sends no participant identifiers to the
  provider. Free-text fields carry a "no personal information" instruction and
  should get a scrub pass before analysis.
- Raw server DB is wiped after an audited export to `data/` — **the export is the
  record** (`export.py` writes an immutable timestamped folder + manifest).

## 6. What is intentionally still open

- Stimuli are functional drafts pending **Pilot A** equivalence (R304) and the OSF
  **freeze** (R305/R405); GAAIS items are canonical placeholders pending verbatim
  pull from Schepman & Rodway.
- Session resume within a 30-min window (EXPERIMENT_PLATFORM.md §3.6) is not yet
  implemented; the idempotent event stream makes it a localized add.
- Free-text coding (R309/R409) stays blocked on the human-gold gate **G1**, exactly
  as the DESIGN files require.
