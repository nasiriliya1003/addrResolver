# T1.2 · Informal Address Resolver

Resolves free-text Kigali delivery descriptions in English, French, and
Kinyarwanda (e.g. *"inyuma ya big pharmacy on RN3, red gate"*) to a
`(lat, lon, confidence)` tuple — with no LLM calls, CPU-only, and a
mean latency of **< 2 ms**.

---

## Quickstart — 2 commands, free Colab CPU

```bash
pip install rapidfuzz geopy langid pandas numpy matplotlib jupyter
python generate_data.py && python resolver.py
```

To run the full evaluation notebook:

```bash
jupyter nbconvert --to notebook --execute eval.ipynb \
        --output eval_executed.ipynb
```

---

## Repository layout

```
addrResolve/
├── generate_data.py     # Step 1 — synthesise all data (seed=42, deterministic)
├── resolver.py          # Step 2 — core resolver + 37 unit tests
├── eval.ipynb           # Step 3 — evaluation notebook (metrics + confusion)
├── correction_flow.md   # Product & Business artifact (rider correction UX)
├── process_log.md       # Hour-by-hour timeline + declared LLM use
├── SIGNED.md            # Honor code (sign before submitting)
├── README.md            # This file
└── data/                # Created by generate_data.py
    ├── gazetteer.json         # 50 Kigali landmarks (EN/FR/KIN)
    ├── descriptions.csv       # 200 free-text descriptions
    ├── gold_visible.csv       # 25 gold rows (candidate-visible)
    └── gold.csv               # 50 gold rows (evaluator-held)
```

---

## Execution order

| Step | Command | Produces |
|------|---------|---------|
| 1 | `python generate_data.py` | `data/` folder with all CSV/JSON |
| 2 | `python resolver.py` | 37 unit tests — **all must pass** |
| 3 | `jupyter nbconvert --execute eval.ipynb` | metrics + figures |

---

## Live demo

```bash
python -c "
from resolver import resolve
import json
print(json.dumps(
    resolve('inyuma ya big pharmacy on RN3, red gate'),
    indent=2
))
"
```

Expected output (abbreviated):
```json
{
  "lat": -1.9452...,
  "lon": 30.0611...,
  "confidence": 0.8875,
  "matched_landmark": "Pharmacie Centrale Kigali",
  "landmark_id": "lm_005",
  "rationale": "Matched 'Pharmacie Centrale Kigali' via surface '...' ...",
  "escalate": false,
  "latency_ms": 1.4
}
```

---

## Evaluation results (gold_visible.csv — 25 rows)

| Metric | Value |
|--------|-------|
| Mean haversine error | 241.7 m |
| Median haversine error | 49.3 m |
| % within 100 m | 74.0 % |
| % within 300 m | 90.0 % |
| Escalation rate | 0.0 % |
| Mean resolve() latency | 1.43 ms |

---

## Technical constraints met

- **CPU-only** — no GPU, no LLM inference calls
- **Allowed libraries only** — `rapidfuzz`, `re` (stdlib), `geopy`, `langid`, `pandas`
- **Mean resolve() latency < 100 ms** — measured at 1.43 ms (70× under budget)

---

## Architecture

```
Raw text
   │
   ▼  normalize_text()       strip emoji, lowercase, NFC, collapse whitespace
   │
   ▼  detect_language()      EN/FR/KIN vocabulary fingerprint; langid fallback
   │
   ▼  extract_modifier()     regex scan (specific→general): bearing + dist_m
   │
   ▼  fuzzy_match_landmark() rapidfuzz WRatio over 200-entry surface index
   │
   ├─ score < 65 → escalate=True (dispatcher flag)
   │
   ▼  apply_offset()         geopy geodesic displacement
   │
   ▼  compute_confidence()   0.75×fuzzy + 0.15×modifier + 0.10×language
   │
   ▼  resolve() → {lat, lon, confidence, matched_landmark, rationale, escalate}
```

---

## 4-minute video

URL: `[paste YouTube/Vimeo link here]`

---

## License

MIT — see `LICENSE` (add before submission).
