"""
Server-side LLM assistant proxy for Study 4.

The browser never holds an API key. Every request/response is logged by the
caller (main.py) to the event stream before the text is shown to the participant.
Model id, system prompt, temperature, and max tokens are pinned in config.py and
recorded for the methods appendix.

If no API key is configured the proxy runs in deterministic "offline" mode using
a small canned-suggestion library, so the platform is fully functional for
preview and testing without any secret. Offline responses are flagged source=
"offline" in the logs and excluded from "live model" provenance.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any

import httpx

from . import config


# Strip conversational preamble / numbered "Option N" lists / wrapping quotes that
# a chat model sometimes adds, leaving just the snippet the writer can paste.
_PREAMBLE_RE = re.compile(
    r"^\s*(sure|certainly|of course|absolutely|here(?:\s|')?s?|here are|below(?: is| are)?|"
    r"i(?:\s|')?(?:d| would)? (?:suggest|recommend|propose)|how about|try this|"
    r"one (?:option|idea)|a (?:few|couple)|some (?:options|ideas))[^\n:]*:\s*", re.I)
_OPTION_RE = re.compile(r"^\s*(?:option\s*\d+\s*[:.\)\-]?|\d+\s*[.\)]\s*)", re.I)


def clean_output(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    t = _PREAMBLE_RE.sub("", t).strip()
    lines = t.split("\n")
    opt_idx = [i for i, l in enumerate(lines) if _OPTION_RE.match(l)]
    if len(opt_idx) >= 2:                       # multiple options -> keep only the first
        block = lines[opt_idx[0]:opt_idx[1]]
        block[0] = _OPTION_RE.sub("", block[0])
        t = "\n".join(block).strip()
    elif opt_idx and opt_idx[0] == 0:           # single leading "1." / "Option 1:"
        lines[0] = _OPTION_RE.sub("", lines[0])
        t = "\n".join(lines).strip()
    if len(t) >= 2 and t[0] in "\"“‘'" and t[-1] in "\"”’'":
        t = t[1:-1].strip()
    return t or (text or "").strip()

# Light safety filter — refuse obviously out-of-scope prompts; full moderation is
# the pinned model's job, this is just a cheap guard + an audit hook.
_BANNED = ("kill myself", "suicide", "child sexual", "csam", "make a bomb", "how to kill")


def is_blocked(text: str) -> bool:
    t = (text or "").lower()
    return any(b in t for b in _BANNED)


def build_user_prompt(
    affordance: str,
    draft: str = "",
    selected: str = "",
    prompt_theme: str = "",
    user_message: str = "",
) -> str:
    draft = (draft or "").strip()
    selected = (selected or "").strip()
    theme = (prompt_theme or "a short story").strip()
    if affordance == "suggest_opening":
        return (
            f"Write a single vivid opening — one or two sentences, under 50 words — "
            f"for a short story on this theme: {theme}.\n"
            "Output ONLY the opening text itself: no preamble, no explanation, no "
            "quotation marks, and do not offer multiple options."
        )
    if affordance == "continue":
        return (
            "Continue this draft naturally with the next two or three sentences, "
            "matching the voice and tense.\n"
            "Output ONLY the new continuation text to append: no preamble, no "
            "explanation, no quotation marks, no options.\n\n"
            f"Draft so far:\n{draft or '(empty)'}"
        )
    if affordance == "polish":
        return (
            "Rewrite the following passage to be clearer and more vivid while keeping "
            "the writer's voice and meaning.\n"
            "Output ONLY the rewritten passage: no preamble, no explanation, no "
            "quotation marks, no options.\n\n"
            f"Passage:\n{selected or draft}"
        )
    # free-form ask
    ctx = f"\n\nCurrent draft for context:\n{draft}" if draft else ""
    return (
        f"{user_message}\n\nReply with only the text the writer can use directly — "
        f"no preamble or meta-commentary.{ctx}"
    )


# --------------------------------------------------------------------------- #
# Offline deterministic fallback
# --------------------------------------------------------------------------- #
_OPENINGS = [
    "The envelope had no stamp, only my name in a hand I almost recognized.",
    "By the time the streetlights buzzed on, the shop was the only window still gold.",
    "She found the letter tucked under the doormat, soft from the rain.",
    "The bell over the door hadn't rung in hours, and still he waited.",
    "Nobody wrote letters anymore, which was exactly why this one frightened her.",
    "The last customer left at midnight; the first ghost arrived a minute later.",
]
_CONTINUATIONS = [
    " She read it twice, then a third time, as if the words might rearrange themselves into something kinder.",
    " He set down his pen and listened to the building settle around him, every creak a small confession.",
    " Outside, the rain kept its own counsel, and the room seemed to lean in to hear what came next.",
    " For a long moment nothing moved, and then everything did at once.",
]


def _pick(options: list[str], key: str) -> str:
    idx = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % len(options)
    return options[idx]


def offline_suggest(affordance: str, draft: str, selected: str, prompt_theme: str,
                    user_message: str) -> str:
    key = f"{affordance}|{prompt_theme}|{draft[-80:]}|{selected[:80]}|{user_message[:80]}"
    if affordance == "suggest_opening":
        return _pick(_OPENINGS, key)
    if affordance == "continue":
        return _pick(_CONTINUATIONS, key)
    if affordance == "polish":
        base = (selected or draft).strip()
        # deterministic light "polish": collapse spaces, ensure terminal period.
        cleaned = " ".join(base.split())
        if cleaned and cleaned[-1] not in ".!?":
            cleaned += "."
        return cleaned or "(nothing selected to polish)"
    return _pick(_CONTINUATIONS, key)


# --------------------------------------------------------------------------- #
# Live providers
# --------------------------------------------------------------------------- #
async def _call_anthropic(user_prompt: str) -> str:
    headers = {
        "x-api-key": config.LLM_API_KEY,
        "anthropic-version": config.ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    body = {
        "model": config.LLM_MODEL,
        "max_tokens": config.LLM_MAX_TOKENS,
        "temperature": config.LLM_TEMPERATURE,
        "system": config.LLM_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_S) as client:
        r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        return "".join(parts).strip()


async def _call_openai(user_prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "content-type": "application/json",
    }
    body = {
        "model": config.LLM_MODEL,
        "max_tokens": config.LLM_MAX_TOKENS,
        "temperature": config.LLM_TEMPERATURE,
        "messages": [
            {"role": "system", "content": config.LLM_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_S) as client:
        r = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()


async def generate(
    affordance: str,
    draft: str = "",
    selected: str = "",
    prompt_theme: str = "",
    user_message: str = "",
) -> dict[str, Any]:
    """Return {text, source, model, latency_ms, error}. Never raises."""
    user_prompt = build_user_prompt(affordance, draft, selected, prompt_theme, user_message)
    t0 = time.monotonic()

    if config.LLM_PROVIDER == "offline" or not config.LLM_API_KEY:
        text = offline_suggest(affordance, draft, selected, prompt_theme, user_message)
        return {
            "text": text, "source": "offline", "model": "offline-canned",
            "latency_ms": int((time.monotonic() - t0) * 1000), "error": None,
            "user_prompt": user_prompt,
        }

    last_err = None
    for attempt in range(2):  # one retry on timeout/transient error
        try:
            if config.LLM_PROVIDER == "anthropic":
                text = await _call_anthropic(user_prompt)
            elif config.LLM_PROVIDER == "openai":
                text = await _call_openai(user_prompt)
            else:
                raise ValueError(f"unknown provider {config.LLM_PROVIDER}")
            text = clean_output(text)
            return {
                "text": text, "source": "llm", "model": config.LLM_MODEL,
                "latency_ms": int((time.monotonic() - t0) * 1000), "error": None,
                "user_prompt": user_prompt,
            }
        except Exception as e:  # noqa: BLE001 — proxy must degrade gracefully
            last_err = str(e)

    # Both attempts failed -> cached/offline fallback (DESIGN §13 LLM outage row)
    text = offline_suggest(affordance, draft, selected, prompt_theme, user_message)
    return {
        "text": text, "source": "offline_fallback", "model": config.LLM_MODEL,
        "latency_ms": int((time.monotonic() - t0) * 1000), "error": last_err,
        "user_prompt": user_prompt,
    }
