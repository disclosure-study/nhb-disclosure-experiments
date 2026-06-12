# NHB Disclosure Experiments — platform

A self-contained, deployable web platform that runs the two pre-registered
behavioral studies for *"The Social Cost of Disclosing AI Use"*:

| | Study 3 — **Disclosure-Label Experiment** | Study 4 — **Disclosure Decision Under Regimes** |
|---|---|---|
| Question | Does an *"Created Using Generative AI"* label lower readers' endorsement of **identical** human-written fiction? | Do stigma / benefit regimes change whether writers **truthfully** label their own AI use? |
| Design | between-subjects, 2 arms × 3 story blocks | between-subjects, 3 regimes; regime revealed **after** writing |
| Primary outcome | `kudos_intent` (endorsement intention) | `truthful` disclosure given **logged** AI use |
| Key instrument | archive-style reading page + behavioral click-through | gallery (regime carrier) + writing editor with a **logged AI assistant** |

It is the instrument described in [`../EXPERIMENT_PLATFORM.md`](../EXPERIMENT_PLATFORM.md);
the full scientific specs are [`study3_label_experiment/DESIGN.md`](../study3_label_experiment/DESIGN.md)
and [`study4_supply_experiment/DESIGN.md`](../study4_supply_experiment/DESIGN.md).

> **Two ways to run the exact same pages**
> - **Live (server) mode** — the FastAPI backend records every action server-side
>   (write-ahead JSONL + SQLite). This is the data-collection instrument.
> - **Preview mode** — open the static pages with no backend (e.g. GitHub Pages).
>   Randomization happens in the browser, the AI assistant runs offline, and a
>   live event-log panel shows everything that *would* be recorded. Nothing leaves
>   the browser. Use it to walk through the whole experience.

---

## Quickstart (live mode, ~1 min)

```bash
cd server
pip install -r requirements.txt
python run_local.py
```

- Participant entry: <http://127.0.0.1:8000/>
- Study 3: <http://127.0.0.1:8000/study3/>  ·  Study 4: <http://127.0.0.1:8000/study4/>
- Researcher monitor: <http://127.0.0.1:8000/admin?key=dev-admin-token>

Run the end-to-end check against it:

```bash
python tests/walkthrough.py          # simulates a full S3 + S4 participant, asserts logging
```

### Preview the participant experience (no backend)

Open `web/index.html` over any static server, or just append `?preview=1` to a
study URL. Force a specific cell for review with URL params, e.g.
`study3/?preview=1&arm=L1_disclosed&story=story_c` or
`study4/?preview=1&regime=R_STIGMA&prompt=prompt_b`. The bottom-right **event log**
shows every recorded action; "download JSON" exports it.

---

## What gets logged

Every participant action is an append-only event written **ahead** to
`data/<study>/events.jsonl` and mirrored into SQLite. Highlights:

- **Study 3:** page enter/exit + dwell, story scroll depth, the label shown,
  `click_next` (behavioral click-through) + latency, every questionnaire response
  with the randomized item order, comprehension/attention/manipulation checks.
- **Study 4:** gallery views/ratings, **every AI-assistant request and response**
  (full text, model, latency, source), inserts/copies/dismisses, paste events
  (internal vs external flag), focus/blur, autosave snapshots, the label choice
  with decision latency and the full toggle sequence.

`any_use` (logged AI use — the conditioning variable for Study 4's primary
analysis) is set server-side the moment an assistant suggestion is inserted, or on
a copy→external-paste of assistant text.

---

## Repository layout

```
experiment_platform/
├── server/                  FastAPI backend (the instrument)
│   ├── app/
│   │   ├── main.py          routes: session/event/assistant/complete/admin
│   │   ├── randomizer.py    stratified assignment with logged seeds
│   │   ├── events.py        write-ahead JSONL + SQLite mirror
│   │   ├── llm_proxy.py     pinned Claude/OpenAI proxy + offline fallback
│   │   ├── admin.py         live dashboard + adaptive top-up counter + kill switch
│   │   ├── export.py        immutable timestamped export CLI
│   │   ├── db.py / config.py
│   │   └── ...
│   ├── tests/walkthrough.py end-to-end API test
│   └── requirements.txt
├── web/                     static frontend (vanilla JS, no build step)
│   ├── index.html           landing
│   ├── shared/              runner, dual-mode logger, components, api client
│   ├── study3/ study4/      per-study custom modules
│   └── config/              study3.json, study4.json, stimuli/ (the instrument as data)
├── analysis/                audit.py → recomputes NUMBERS_FOR_MS.md from raw JSONL
├── docs/                    generated GitHub Pages preview build (= web/)
├── Dockerfile · render.yaml one-click deploy
└── DEPLOY.md · PLATFORM_NOTES.md
```

The entire instrument — arms, regimes, every scale item, fixed block order, regime
scripts, the label menu — lives in `web/config/study{3,4}.json`, so you can read the
whole study without reading code, and the config hash is recorded as a version with
every session.

---

## Data → numbers (the audit discipline)

Same rule as Studies 1–2: no reported number is hand-maintained.

```bash
python analysis/s3_audit.py --data server/data/s3   # writes NUMBERS_FOR_MS.md + s3_tidy.csv
python analysis/s4_audit.py --data server/data/s4
```

`audit.py` materializes one tidy row per participant from the raw JSONL and
recomputes the descriptive quantities, the Study-4 **randomization flatness check**
(`any_use` rate must be flat across regimes), and the pre-registered exclusion
flags. The inferential models (HC2 regressions, causal mediation, regime logistic)
belong in the study analysis folders as `s3_models.py` / `s4_models.py`.

---

## Deploy

See **[DEPLOY.md](DEPLOY.md)**. In short:
- **Live preview link** → GitHub Pages on `docs/` (browser-only, zero config).
- **Real data collection** → one-click [Render](render.yaml) Blueprint, **or self-host on EC2 / any VPS** with `docker compose up -d` (Caddy auto-HTTPS, persistent volume).
- **Recruitment** → Prolific or a regional panel (URL params + completion codes wired in).
- **Multi-country (UK / US / Malaysia / China)?** The instrument is country-neutral; see DEPLOY.md §4 for data storage and China (PIPL) residency.

---

## Status & honesty notes

- The three Study-3 stories and the 12 Study-4 gallery pieces are **functional
  drafts**. Before any main run they must pass the pre-registered gates: Story
  equivalence via **Pilot A** (pairwise quality-composite *d* < 0.30, run R304) and
  the **OSF measure-wording freeze** (R305 / R405) — including pulling the GAAIS
  items verbatim (the two/four GAAIS items in the configs are canonical
  placeholders). See `web/config/stimuli/STIMULI_README.md`.
- This is the **experiment software** only. Recruitment is bought (Prolific); a
  real community site is deliberately *not* built (see `../EXPERIMENT_PLATFORM.md` §6).
- Architecture rationale, the oTree decision, and the provenance/GDPR design are in
  **[PLATFORM_NOTES.md](PLATFORM_NOTES.md)**.

MIT licensed — released as a research artifact with the paper.
