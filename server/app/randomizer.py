"""
Stratified between-subjects randomization with logged seeds.

* Study 3: 2 arms x 3 story blocks (6 cells). Balanced by always filling the
  least-occupied cell; ties broken by a seeded RNG. Guarantees +-1 cell balance
  (the DESIGN §12 "cell fill +-5%" acceptance criterion).
* Study 4: 3 regimes (balanced) x 2 prompts (counterbalanced). Regime balance is
  what the primary contrasts need; prompt is a nuisance factor.

The seed is derived deterministically from RNG_BASE_SEED + arrival index, so the
whole assignment sequence is reproducible and auditable from the logs.
"""
from __future__ import annotations

import json
import random
from typing import Any

from . import config, db


def _least_filled(candidates: list[str], counts: dict[str, int], rng: random.Random) -> str:
    lo = min(counts.get(c, 0) for c in candidates)
    tied = [c for c in candidates if counts.get(c, 0) == lo]
    return rng.choice(tied)


def assign(study: str) -> dict[str, Any]:
    counts = db.cell_counts(study)            # keyed by cond_json string
    totals = db.study_totals(study)
    arrival_index = totals["total"]
    seed = config.RNG_BASE_SEED + arrival_index
    rng = random.Random(seed)

    if study == "s3":
        # Build per-cell counts over the 6 cells.
        cell_counts: dict[str, int] = {}
        for arm in config.S3_ARMS:
            for story in config.S3_STORIES:
                key = json.dumps({"arm": arm, "story": story}, sort_keys=True)
                cell_counts[key] = counts.get(key, 0)
        chosen_key = _least_filled(list(cell_counts), cell_counts, rng)
        cond = json.loads(chosen_key)
        batch_no = arrival_index // config.S3_BATCH_SIZE + 1

    elif study == "s4":
        # Regime: least-filled across both prompts.
        regime_counts = {r: 0 for r in config.S4_REGIMES}
        prompt_counts = {p: 0 for p in config.S4_PROMPTS}
        for key, n in counts.items():
            try:
                c = json.loads(key)
            except Exception:
                continue
            if c.get("regime") in regime_counts:
                regime_counts[c["regime"]] += n
            if c.get("prompt") in prompt_counts:
                prompt_counts[c["prompt"]] += n
        regime = _least_filled(config.S4_REGIMES, regime_counts, rng)
        prompt = _least_filled(config.S4_PROMPTS, prompt_counts, rng)
        cond = {"regime": regime, "prompt": prompt}
        batch_no = arrival_index // config.S4_BATCH_SIZE + 1

    else:
        raise ValueError(f"unknown study {study!r}")

    return {
        "cond": cond,
        "cond_json": json.dumps(cond, sort_keys=True),
        "rng_seed": seed,
        "arrival_index": arrival_index,
        "batch_no": batch_no,
    }
