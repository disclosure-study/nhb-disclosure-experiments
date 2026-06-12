# Stimuli — Functional Draft

These are **FUNCTIONAL DRAFT** stimuli for the experiment platform. They are
wired to run the platform end-to-end, but they are **not** the frozen final copy.

Two gates must clear before any main run:

1. **S3 story equivalence (Pilot A).** The three `stories.json` chapters must pass
   pairwise quality-composite equivalence (target Cohen's *d* < 0.30 on every pair).
   Run **R304**. If any pair exceeds the bound, the offending chapter is revised or
   replaced and Pilot A is re-run before the S3 main collection.
2. **Measure wording freeze.** Exact wording of all outcome/manipulation-check
   measures is frozen at OSF preregistration (**R305** for S3, **R405** for S4).
   Comprehension MCQs and any rating-scale text here are placeholders until then.

The `gallery_seed.json` pieces are **quality-staggered by design** (≈4 strong,
4 competent, 4 flat) to exercise the S4 label-blind quality rubric and to give the
likes-transform regimes a realistic spread to act on. Stagger is intentional, not
a defect.

## File inventory

- `stories.json` — 3 S3 reading-stimulus chapters (+ ch.2 hooks, comprehension MCQs)
- `gallery_seed.json` — 2 fixed S4 prompts + 12 staggered gallery seed pieces
- `STIMULI_README.md` — this note

## Balance actually produced

**Stories (3), one per register:**

- `story_a` — found_family — "The Two-Thirty Crowd" (1,334 words, ch.1)
- `story_b` — light_mystery — "The Wrong Umbrella" (1,172 words, ch.1)
- `story_c` — bittersweet_romance — "Low Tide at Halverston" (1,189 words, ch.1)

Each ch.1 lands in the 1,100–1,400 band, ends on a genuine hook, and ships a
~300-word ch.2 plus one factual comprehension MCQ (one correct option of four).

**Gallery (12 pieces), quality × AI-label:**

- Quality: 4 strong (g01, g05, g08, g11) · 4 competent (g02, g04, g07, g10) ·
  4 flat (g03, g06, g09, g12)
- AI-labeled = **true** on exactly 4 (g01, g04, g09, g11) — deliberately spanning
  the quality range, incl. two strong AI-labeled pieces; remaining 8 false.
- `likes_base` spread ≈ 9–112, positively but imperfectly correlated with quality.
