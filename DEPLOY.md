# Deploy guide

Three independent things can be deployed; pick what you need.

| Goal | Path | Cost | Persistent data? |
|---|---|---|---|
| A clickable **live preview** of both studies | GitHub Pages on `docs/` | free | n/a (browser-only) |
| **Real data collection** (managed) | Render Blueprint | free tier for piloting | needs a disk/DB (below) |
| **Real data collection** (your own server) | EC2 / VPS + Docker Compose | ~US$10–20/mo | yes (Docker volume) |
| **Recruitment** | Prolific or a regional panel | per-participant | — |

> **Recruiting across the UK, US, Malaysia, and China?** That mainly affects *where*
> you host and *which* panel you use — see **§4 Data storage & residency**. The
> instrument itself is country-neutral (no UK/US-only wording; Malaysia and China are
> in the demographics; the data-protection notice covers GDPR / PIPL / PDPA).

---

## 1. Live preview — GitHub Pages (browser-only)

`docs/` is the static frontend. On `*.github.io` it auto-runs in preview mode
(client randomization, offline assistant, visible event log; nothing is collected).

1. Push this repo to GitHub.
2. **Settings → Pages → Source: Deploy from a branch → `main` / `/docs`** → Save.
3. After ~1 min your site is at `https://<user>.github.io/<repo>/`.

Re-run `python scripts/build_pages.py` whenever `web/` changes, then commit `docs/`.

> Good for: reviewers, IRB/OSF reviewers, co-authors, and sanity-checking the flow.
> Not for collecting data — there is no server.

---

## 2. Real backend — Render (one click)

`render.yaml` is a Blueprint.

1. In Render: **New + → Blueprint → connect this repo → Apply.**
2. Render generates `PID_SALT` and `ADMIN_TOKEN`, installs deps, and starts
   `uvicorn`. Health check: `/api/health`.
3. Your instrument is at `https://<service>.onrender.com/` and the monitor at
   `https://<service>.onrender.com/admin?key=<ADMIN_TOKEN>` (copy the value from the
   Render dashboard → Environment).

**⚠ Persistence.** Render's *free* instances have an **ephemeral** filesystem —
data is lost on redeploy/restart. For a real run either:
- attach a paid **Render Disk** mounted at the `DATA_DIR` path in `render.yaml`, or
- run the export endpoint frequently / move to Postgres (`db.py` is the only file to
  swap). For piloting and demos the free tier is fine.

---

## 3. Self-host on EC2 / VPS (your infrastructure)

Best when you need the data on hardware you control (IRB / data-residency, §4). Works
on any Ubuntu box — AWS EC2, Lightsail, a university VM, Alibaba Cloud, etc.

**On a fresh Ubuntu 22.04 instance (t3.small / 2 GB+ is plenty):**

```bash
# 1. install Docker + compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# 2. get the code + configure  (replace disclosure-study with your GitHub org)
git clone https://github.com/disclosure-study/nhb-disclosure-experiments.git
cd nhb-disclosure-experiments
cp .env.example .env && nano .env     # set SITE_ADDRESS, PID_SALT, ADMIN_TOKEN, codes

# 3. launch (Caddy auto-provisions HTTPS)
docker compose up -d --build
```

Open ports **80, 443** (and 22) in the security group, point your domain's DNS
A-record at the instance, and set `SITE_ADDRESS` to that domain — Caddy fetches a
Let's Encrypt certificate automatically. Live at `https://<domain>/`, monitor at
`https://<domain>/admin?key=<ADMIN_TOKEN>`. To test before DNS, set `SITE_ADDRESS=:80`
and hit `http://<instance-ip>/`.

Throwaway single-container run (no HTTPS):

```bash
docker build -t nhb-platform .
docker run -p 8000:8000 -v $PWD/data:/app/data \
  -e PID_SALT=... -e ADMIN_TOKEN=... -e ANTHROPIC_API_KEY=sk-... nhb-platform
```

---

## 4. Data storage & residency

**What is written.** The platform writes two things to `DATA_DIR`: an immutable,
append-only `events.jsonl` (the record of truth) plus a SQLite mirror for the live
dashboard. In Compose that's the `nhb_data` volume; on EC2 put `DATA_DIR` on a mounted
**EBS volume** so it survives instance replacement; on Render attach a disk.

**How to store it:**

| Option | When | How |
|---|---|---|
| **SQLite + JSONL on a volume** (default) | up to a few hundred concurrent | mount EBS at `DATA_DIR`; nightly snapshot + periodic export |
| **Managed Postgres** (RDS / Cloud SQL) | large/long studies, central audit | swap `server/app/db.py` for a Postgres driver; keep JSONL as the log |

Either way: **export early and often** (dashboard "export zip" or `python -m
app.export`), keep the timestamped exports as the archival record, and back up the
volume. `s3_audit.py` / `s4_audit.py` recompute every number from the JSONL — the
export *is* the dataset.

**Residency — matters most for China.** Your four countries differ:
- **China (PIPL):** personal data collected in mainland China should generally be
  stored in-country, with extra steps for cross-border transfer. Host the China arm in
  a China region (**AWS China / Alibaba / Tencent Cloud**) as a *separate* deployment
  rather than shipping rows abroad. Western panels (incl. Prolific) have thin mainland
  coverage — you'll likely use a regional panel (Credamo / Wenjuanxing-style) there.
- **Malaysia (PDPA)** and **UK/EU (GDPR):** transfer is fine with a lawful basis; an
  EU/UK-region instance is the clean default for UK + US + Malaysia.
- The platform already minimizes exposure: only a **hashed** participant ID is stored
  (raw ID never persisted), `PID_SALT` stays off-server, and the LLM proxy sends **no**
  identifiers to the provider. Check the model provider's region if your IRB treats the
  assistant text as personal data.

Practical setup: one EU/UK instance for UK + US + Malaysia, a separate China-region
instance for mainland China; combine the de-identified exports for analysis.

---

## 5. Environment variables

| Var | Default | Purpose |
|---|---|---|
| `PID_SALT` | dev salt | SHA-256 salt for participant IDs — **set a secret, keep off-server** |
| `ADMIN_TOKEN` | `dev-admin-token` | gates `/admin` and the export endpoint |
| `RNG_BASE_SEED` | `20260611` | reproducible randomization |
| `DATA_DIR` | `<repo>/data` | where JSONL + SQLite are written |
| `LLM_PROVIDER` | `anthropic` | `anthropic` \| `openai` \| `offline` (auto-`offline` if no key) |
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | pinned assistant model |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | unset ⇒ deterministic offline assistant |
| `COMPLETION_CODE_S3` / `_S4` | demo codes | recruitment completion codes |
| `ALLOWED_ORIGINS` | `*` | CORS allow-list (lock down in prod) |

The Study-4 assistant works with **no key** (offline canned suggestions, still fully
logged). Add `ANTHROPIC_API_KEY` to switch to the pinned Claude Haiku model. Budget
per the DESIGN: ~900 participants × ≤20 calls × ~400 tokens ⇒ well under £50.

---

## 6. Wiring recruitment (Prolific or a regional panel)

1. Set the study URL to your backend with the panel's ID placeholders, e.g. on Prolific
   `https://<host>/study3/?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}`.
   The platform reads `PROLIFIC_PID` and hashes it; with another panel, pass its id as
   `PROLIFIC_PID` (it's just treated as the participant id) or run without one.
2. Use **completion-code** redirect. Put your codes in `COMPLETION_CODE_S3/_S4`; the
   final page shows the code and a "Return" button.
3. Apply the DESIGN §3/§5 screeners on the panel (age, English reading fluency, your
   target countries, approval/quality filters, desktop, not-in-other-study). The in-app
   screener only enforces the fiction-reading-frequency gate (S3). For mainland China,
   use a China-region panel + the China-region instance (§4).
4. Run the **soft-launch** discipline (EXPERIMENT_PLATFORM.md §4): a 25-slot tranche,
   watch `/admin`, then batch. For S4 the dashboard's **AI-users-per-regime** counter
   drives the adaptive top-up (batches of 150 until all three regimes ≥135, hard cap
   N=1000).

---

## 7. During a run

- **Monitor:** `/admin?key=…` — live cell counts, the S4 top-up counter, recent
  event feed, intake status.
- **Pause intake (kill switch):** the dashboard button, or
  `POST /api/admin/intake?open=false&key=…`. Completers still finish + get paid.
- **Export:** dashboard "export (zip)", or `python -m app.export --study s4` from
  `server/` for an immutable timestamped folder. Then run
  `python analysis/s4_audit.py --data <export-folder>`.
