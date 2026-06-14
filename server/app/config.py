"""
Central configuration for the experiment platform.

Everything that is provenance-relevant (LLM model/params/system prompt, salts,
seeds, completion codes, sample targets) lives here or in environment variables,
so the methods appendix and the audit scripts can read one source of truth.

All values are overridable via environment variables for deployment, but ship
with safe local-development defaults so the platform runs with zero config.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
SERVER_DIR = Path(__file__).resolve().parent.parent          # .../server
REPO_DIR = SERVER_DIR.parent                                  # repo root
WEB_DIR = Path(_env("WEB_DIR", str(REPO_DIR / "web")))        # static frontend
DATA_DIR = Path(_env("DATA_DIR", str(REPO_DIR / "data")))     # raw exports live here
DB_PATH = Path(_env("DB_PATH", str(DATA_DIR / "platform.sqlite3")))

DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "s3").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "s4").mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Platform version (provenance) — git short hash if available, else VERSION
# --------------------------------------------------------------------------- #
VERSION = "1.0.0"


def _git_hash() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_DIR), capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "nogit"


PLATFORM_VERSION = f"{VERSION}+{_git_hash()}"


# --------------------------------------------------------------------------- #
# Identity / security
# --------------------------------------------------------------------------- #
# PID_SALT must be set to a real secret in production and kept OFF-SERVER for
# the deletion-on-request lookup table (per EXPERIMENT_PLATFORM.md §5).
PID_SALT = _env("PID_SALT", "dev-only-salt-CHANGE-ME")
ADMIN_TOKEN = _env("ADMIN_TOKEN", "dev-admin-token")
RNG_BASE_SEED = _env_int("RNG_BASE_SEED", 20260611)


# --------------------------------------------------------------------------- #
# Recruitment / sampling targets (read by the admin dashboard + top-up rule)
# --------------------------------------------------------------------------- #
# Study 3: two arms x three story blocks, 140/cell, 420/arm (DESIGN §3).
S3_ARMS = ["L0_control", "L1_disclosed"]
S3_STORIES = ["story_a", "story_b", "story_c"]
S3_PER_ARM_TARGET = _env_int("S3_PER_ARM_TARGET", 420)
S3_BATCH_SIZE = _env_int("S3_BATCH_SIZE", 350)

# Study 4: three regimes; adaptive top-up to >=135 AI-USERS/regime; cap N=1000.
S4_REGIMES = ["R_CTRL", "R_STIGMA", "R_BENEFIT"]
S4_PROMPTS = ["prompt_a", "prompt_b"]
S4_AIUSER_TARGET = _env_int("S4_AIUSER_TARGET", 135)
S4_BATCH_SIZE = _env_int("S4_BATCH_SIZE", 150)
S4_HARD_CAP = _env_int("S4_HARD_CAP", 1000)

# Prolific completion codes (set the real ones from the Prolific study page).
COMPLETION_CODES = {
    "s3": _env("COMPLETION_CODE_S3", "S3DEMO-COMPLETE"),
    "s4": _env("COMPLETION_CODE_S4", "S4DEMO-COMPLETE"),
}


# --------------------------------------------------------------------------- #
# Access control — invitation codes
# --------------------------------------------------------------------------- #
# When INVITE_REQUIRED, a participant must enter a valid invite code (server mode)
# before the study begins. Codes are generated/managed in the admin dashboard.
# One always-present TEST code runs the real study but stores NOTHING and shows an
# alert; it is displayed in the admin dashboard.
INVITE_REQUIRED = _env("INVITE_REQUIRED", "1").lower() not in ("0", "false", "no", "off")
INVITE_TEST_CODE = _env("INVITE_TEST_CODE", "TEST-RUN")


# --------------------------------------------------------------------------- #
# LLM assistant proxy (Study 4) — pinned + fully logged (provenance discipline)
# --------------------------------------------------------------------------- #
# Provider: "anthropic" | "openai" | "offline".
# With no API key the platform automatically runs in "offline" mode using
# deterministic canned suggestions, so preview/testing needs no secrets.
LLM_PROVIDER = _env("LLM_PROVIDER", "anthropic").lower()
LLM_MODEL = _env("LLM_MODEL", "claude-haiku-4-5-20251001")
LLM_API_KEY = _env("ANTHROPIC_API_KEY", _env("OPENAI_API_KEY", _env("LLM_API_KEY", "")))
LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0.7)
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 220)
LLM_REQUEST_CAP = _env_int("LLM_REQUEST_CAP", 20)        # per participant (DESIGN §4.2)
LLM_TIMEOUT_S = _env_float("LLM_TIMEOUT_S", 10.0)        # 10 s timeout w/ retry
ANTHROPIC_VERSION = _env("ANTHROPIC_VERSION", "2023-06-01")

# Pinned system prompt — recorded for the methods appendix; do not edit after freeze.
LLM_SYSTEM_PROMPT = _env(
    "LLM_SYSTEM_PROMPT",
    "You are a neutral writing assistant embedded in a creative-writing tool. "
    "Help the writer with short, concrete suggestions for a piece of original "
    "short fiction. "
    "Always reply with ONLY the suggested story text itself, ready to paste "
    "straight into the draft: no preamble or sign-off, no explanation, no "
    "quotation marks around it, no labels, and never multiple or numbered options "
    "(do not write 'Here is', 'Option 1', '1.', etc.) — give one single best "
    "suggestion. Keep responses brief (a sentence or two for an opening; two or "
    "three sentences when continuing). Keep all content suitable for a general "
    "audience (no explicit sexual content, graphic violence, or slurs). Never ask "
    "for or use personal information about the writer.",
)

# If no key is present, force offline mode regardless of provider.
if not LLM_API_KEY and LLM_PROVIDER != "offline":
    LLM_PROVIDER = "offline"


# --------------------------------------------------------------------------- #
# CORS — permissive by default so a Pages-hosted frontend can optionally call a
# deployed backend. Lock down via ALLOWED_ORIGINS (comma-separated) in prod.
# --------------------------------------------------------------------------- #
ALLOWED_ORIGINS = [o.strip() for o in _env("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
