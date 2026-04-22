# process_log.md — T1.2 Informal Address Resolver

> **Constraint**: ≤ 2 pages. Hour-by-hour timeline, all LLM/tool use declared,
> 3 sample prompts sent, 1 prompt discarded + reason, 1 hardest-decision paragraph.

---

## Hour-by-Hour Timeline

| Time (CAT) | Activity |
|------------|----------|
| H+0:00     | Read brief end-to-end. Identified 5 deliverables + video structure. Noted the `correction_flow.md` is weighted equally with technical quality — prioritised it early. |
| H+0:15     | Scaffolded repo: empty `generate_data.py`, `resolver.py`, `eval.ipynb`. Committed README skeleton. Signed `SIGNED.md`. |
| H+0:30     | Designed modifier table and gazetteer schema. Manually authored all 50 Kigali landmarks (no LLM — needed accurate real coordinates across 3 districts). |
| H+1:00     | Implemented `generate_data.py`: `build_gazetteer()`, `geodesic_destination()`, `compute_true_coords()`, `inject_noise()` (all 5 noise types), `generate_description()`, `generate_all()`. Ran generator; verified 200 descriptions and 50 gold rows. |
| H+1:30     | Implemented `resolver.py`: `normalize_text()`, `detect_language()`, `extract_modifier()`, `fuzzy_match_landmark()`, `apply_offset()`, `compute_confidence()`, `resolve()`. |
| H+2:00     | Wrote 37 unit tests. Debugged 2 failures: (a) emoji regex was too narrow — extended Unicode range; (b) `_single_mutation` crashed on last character during transpose — added fallback substitute. All 37 tests green. |
| H+2:30     | Implemented `eval.ipynb` (8 cells). Ran notebook end-to-end: 50/50 resolved, 74 % within 100 m, 90 % within 300 m, mean latency 1.43 ms. |
| H+3:00     | Wrote `correction_flow.md` with real numbers (MTN Rwanda pricing, rider wage, AWS Lambda cost). Verified data volume arithmetic independently. |
| H+3:30     | README polish. Verified 2-command Colab reproducibility in a clean venv. Recorded 4-minute video. |
| H+3:50     | Final review: checked all 5 deliverables present, unit tests pass, notebook executes cleanly, video URL in README. |

---

## LLM / Tool Use Declared

| Tool | Version | Purpose |
|------|---------|---------|
| Claude (Anthropic) | claude-sonnet-4-6 | Architecture pseudocode audit, `correction_flow.md` cost paragraph structure, README phrasing review |
| GitHub Copilot | GPT-4o base | Inline autocomplete for boilerplate (CSV writer, `_write_csv`, matplotlib axis labels) |

---

## 3 Sample Prompts Sent

**Prompt 1** (Claude — architecture audit):
> "You are a senior software engineer. Audit this pseudocode for T1.2 against the requirements. Find every bug, missing case, and weight error. Then give me a corrected, complete, parsimonious version."

**Prompt 2** (Claude — cost paragraph):
> "Write a one-paragraph argument (concrete numbers, real users, Rwanda context) that the 3-button digital correction flow costs less per event than paper bug reports. Include labour cost breakdown and latency comparison."

**Prompt 3** (Copilot — notebook cell):
> "Write a matplotlib histogram of `resolved['error_m']` with log-scale x-axis, bins at [0,50,100,200,300,500,1000,3000], and vertical lines at 100 m and 300 m."

---

## 1 Prompt Discarded and Why

**Discarded prompt** (Claude):
> "Generate 50 realistic Kigali landmarks with accurate lat/lon coordinates."

**Why discarded**: The LLM produced plausible-sounding but inaccurate coordinates
(e.g. placed Kimironko Market at -1.9650, 30.0980 — 340 m from its true location).
Any error in gazetteer coordinates propagates directly into resolver accuracy and
gold-row offsets. The landmark table was authored manually using Google Maps
verification for each of the 50 entries.

---

## Hardest Decision

The single hardest decision was choosing **one fuzzy-match threshold** (`FUZZY_CUTOFF = 65`)
rather than two (a "match" threshold and a separate "escalate" threshold). The original
pseudocode defined `FUZZY_THRESHOLD = 75` and `ESCALATION_THRESHOLD = 60` as distinct
values, but this created a silent dead zone where scores of 60–74 would produce a match
returned to the caller with no escalation flag, yet the confidence formula would assign
scores as low as 0.49 — misleadingly implying a usable coordinate. The simpler design
(one cutoff; everything below it returns `None` and triggers escalation) makes the
contract binary and transparent: either we have a match we're willing to defend or we
don't. The trade-off is a marginally higher escalation rate on very noisy inputs, but
this is preferable to silently returning a low-confidence coordinate that a rider might
follow to the wrong location.
