"""
resolver.py
════════════════════════════════════════════════════════════════════════════════
ACCEPTS  : data/gazetteer.json produced by generate_data.py.
           Loaded ONCE at module import into module-level state.
           The public API then accepts raw free-text strings in EN/FR/KIN.

PROCESSES:
  1. normalize_text   — strip emoji, lowercase, collapse whitespace (NFC)
  2. detect_language  — vocabulary-fingerprint heuristic; langid fallback
  3. extract_modifier — regex scan for spatial modifiers (specific → general)
  4. fuzzy_match_landmark — rapidfuzz WRatio over flat surface index
  5. apply_offset     — geodesic displacement using geopy
  6. compute_confidence — weighted formula (fuzzy score, modifier, language)

PRODUCES : resolve(text) → dict with keys:
    lat               float | None
    lon               float | None
    confidence        float  [0.0, 1.0]
    matched_landmark  str   | None
    landmark_id       str   | None
    rationale         str
    escalate          bool   (True → no reliable match; dispatcher should act)
    latency_ms        float

  Unit tests at the bottom of this file.
  Run:  python resolver.py   OR   pytest resolver.py -v
════════════════════════════════════════════════════════════════════════════════
"""

import json
import os
import re
import sys
import time
import unicodedata
from math import radians, sin, cos, asin, atan2, degrees

import geopy.distance
import langid
from rapidfuzz import fuzz as rf_fuzz
from rapidfuzz import process as rf_process

# ─── Paths ────────────────────────────────────────────────────────────────────
_HERE          = os.path.dirname(os.path.abspath(__file__))
GAZETTEER_PATH = os.path.join(_HERE, "data", "gazetteer.json")

# ─── Modifier patterns ────────────────────────────────────────────────────────
# Each entry: (compiled regex, bearing_degrees, dist_metres)
# Ordered most-specific → least-specific to prevent short-circuit mismatches.
# e.g. "inyuma ya" must be caught before the generic "near" (0°).
MODIFIER_PATTERNS = [
    (re.compile(r'\binyuma\s+ya\b',       re.I | re.U), 180, 40),
    (re.compile(r'\bhafi\s+ya\b',         re.I | re.U),  90, 20),
    (re.compile(r'\bhejuru\s+ya\b',       re.I | re.U), 350, 25),
    (re.compile(r'\bderrière\b',          re.I | re.U), 180, 40),
    (re.compile(r'\ben\s+face\s+de\b',    re.I | re.U), 180, 15),
    (re.compile(r'\bà\s+côté\s+de\b',    re.I | re.U),  90, 20),
    (re.compile(r'\bprès\s+de\b',         re.I | re.U),  90, 25),
    (re.compile(r'\bau[\-\s]dessus\s+de\b', re.I | re.U), 350, 25),
    (re.compile(r'\bbehind\b',            re.I | re.U), 180, 40),
    (re.compile(r'\bopposite\b',          re.I | re.U), 180, 15),
    (re.compile(r'\bnext\s+to\b',         re.I | re.U),  90, 20),
    (re.compile(r'\bnear\b',              re.I | re.U),   0, 30),
    (re.compile(r'\babove\b',             re.I | re.U), 350, 25),
]

# ─── Language vocabulary fingerprints ────────────────────────────────────────
# Sets of lowercase tokens characteristic of each language.
# Overlap is intentional (e.g. "hafi ya" covers both KIN senses).
_KIN = frozenset({
    "inyuma", "hafi", "ya", "ni", "mu", "ku", "na", "nka", "kwa",
    "rya", "bya", "isoko", "agatehe", "ifarumasi", "kiliziya",
    "itorero", "ishuri", "ibiro", "oteli", "stadiyumu", "hejuru",
    "farumasi", "masigiti",
})
_FR = frozenset({
    "derrière", "côté", "près", "face", "pharmacie", "marché",
    "église", "à", "de", "du", "le", "la", "en", "au", "les",
    "arrêt", "hôtel", "carrefour", "terminus", "école", "bureau",
    "stade", "mosquée", "rond", "point",
})
_EN = frozenset({
    "behind", "next", "near", "opposite", "above", "pharmacy",
    "market", "church", "the", "on", "at", "bus", "stop", "school",
    "hotel", "roundabout", "stadium", "office", "stage", "mosque",
})

# ─── Fuzzy-match threshold ────────────────────────────────────────────────────
# extractOne returns None for any score below FUZZY_CUTOFF.
# Scores in [65, 74] produce low confidence but are not escalated —
# the caller decides escalation based on the None check.
FUZZY_CUTOFF = 65

# ─── Emoji strip regex (compiled once at import) ──────────────────────────────
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\uFE00-\uFE0F"
    "]+",
    flags=re.UNICODE,
)

# ─── Module-level gazetteer state (populated once at import) ─────────────────
_GAZETTEER:    list = []   # raw landmark dicts from gazetteer.json
_FLAT_INDEX:   list = []   # [{"surface": str, "landmark_id": str}, ...]
_SURFACE_LIST: list = []   # parallel list of surface strings for rapidfuzz


def _load_gazetteer() -> None:
    """
    Load data/gazetteer.json and build the flat surface index.
    Called exactly once at module import. Raises FileNotFoundError if the
    gazetteer is missing (run generate_data.py first).

    Index entries per landmark:
      • canonical English name
      • French alias
      • Kinyarwanda alias
      • type-stripped name (e.g. "kimironko" from "kimironko market")
        — helps when the input omits the type word entirely.
    """
    if not os.path.exists(GAZETTEER_PATH):
        raise FileNotFoundError(
            f"Gazetteer not found at {GAZETTEER_PATH}.\n"
            "Run:  python generate_data.py"
        )

    with open(GAZETTEER_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)

    _GAZETTEER.extend(raw)

    for lm in _GAZETTEER:
        # All three language surfaces
        for surface in [lm["name"],
                        lm["aliases"]["fr"],
                        lm["aliases"]["kin"]]:
            _FLAT_INDEX.append({
                "surface":     surface.lower().strip(),
                "landmark_id": lm["id"],
            })

        # Type-stripped name variant for robustness
        type_word = lm["type"].replace("_", " ")
        stripped  = lm["name"].lower().replace(type_word, "").strip()
        if stripped and len(stripped) >= 4:
            _FLAT_INDEX.append({
                "surface":     stripped,
                "landmark_id": lm["id"],
            })

    _SURFACE_LIST.extend(entry["surface"] for entry in _FLAT_INDEX)


_load_gazetteer()  # executed once when the module is first imported


# ─── Private lookup ───────────────────────────────────────────────────────────

def _lookup(landmark_id: str) -> dict:
    """Return the landmark dict for a given id. O(50) — negligible."""
    for lm in _GAZETTEER:
        if lm["id"] == landmark_id:
            return lm
    raise KeyError(f"landmark_id {landmark_id!r} not found in gazetteer")


# ════════════════════════════════════════════════════════════════════════════════
# PUBLIC PURE FUNCTIONS
# Each function is stateless: same input → same output; no side effects.
# ════════════════════════════════════════════════════════════════════════════════

def normalize_text(text: str) -> str:
    """
    ACCEPTS : raw user-supplied description string (any encoding mix).
    PROCESS : NFC-normalise unicode → strip emoji → lowercase → collapse whitespace.
    PRODUCES: clean lowercase string ready for pattern matching and fuzzy search.
    """
    text = unicodedata.normalize("NFC", text)
    text = _EMOJI_RE.sub(" ", text)          # replace emoji blocks with space
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_language(text: str) -> dict:
    """
    ACCEPTS : normalized text string (output of normalize_text).
    PROCESS : Count intersecting tokens with EN/FR/KIN vocabulary fingerprints.
              If total vocabulary matches == 0, fall back to langid.classify().
              Mixed = True when second-highest language score ≥ 40 % of highest
              (guards against zero-division; top_score > 0 is guaranteed in
              the heuristic branch because total > 0).
    PRODUCES: {"primary": "EN"|"FR"|"KIN"|"UNKNOWN",
               "mixed":   bool,
               "scores":  {"EN": int, "FR": int, "KIN": int}}
    """
    tokens = set(text.split())

    scores = {
        "KIN": len(tokens & _KIN),
        "FR":  len(tokens & _FR),
        "EN":  len(tokens & _EN),
    }
    total = sum(scores.values())

    if total == 0:
        # Vocabulary fingerprint found nothing — use langid
        lang_code, _ = langid.classify(text)
        lang_map = {"rw": "KIN", "fr": "FR", "en": "EN"}
        primary  = lang_map.get(lang_code, "UNKNOWN")
        return {"primary": primary, "mixed": False, "scores": scores}

    # Sort descending by score
    ranked       = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    primary      = ranked[0][0]
    top_score    = ranked[0][1]    # > 0 because total > 0
    second_score = ranked[1][1]

    mixed = (second_score / top_score) >= 0.40

    return {"primary": primary, "mixed": mixed, "scores": scores}


def extract_modifier(text: str) -> dict:
    """
    ACCEPTS : normalized text string (output of normalize_text).
    PROCESS : Scans MODIFIER_PATTERNS in priority order (most-specific first).
              Returns on first match — prevents generic patterns from shadowing
              specific ones (e.g. 'near' must not fire when 'inyuma ya' is present).
    PRODUCES: {"bearing": float | None, "dist_m": float | None}
              bearing is None when no modifier is detected (no directional offset applied).
    """
    for pattern, bearing, dist_m in MODIFIER_PATTERNS:
        if pattern.search(text):
            return {"bearing": float(bearing), "dist_m": float(dist_m)}

    return {"bearing": None, "dist_m": None}


def fuzzy_match_landmark(text: str) -> dict:
    """
    ACCEPTS : normalized text string (output of normalize_text).
    PROCESS : rapidfuzz WRatio search over _SURFACE_LIST (all EN/FR/KIN
              landmark surfaces + type-stripped variants). Returns the
              best match if score ≥ FUZZY_CUTOFF, else None.
              WRatio handles token reordering and partial matches — well-suited
              for informal multi-word descriptions.
    PRODUCES: {"landmark": dict | None,
               "score":    float,       # normalised [0.0, 1.0]
               "surface":  str}
    """
    if not _SURFACE_LIST:
        raise RuntimeError("Gazetteer not loaded. Run generate_data.py first.")

    result = rf_process.extractOne(
        query        = text,
        choices      = _SURFACE_LIST,
        scorer       = rf_fuzz.WRatio,
        score_cutoff = FUZZY_CUTOFF,
    )

    if result is None:
        return {"landmark": None, "score": 0.0, "surface": ""}

    best_surface, best_score, best_idx = result
    landmark_id = _FLAT_INDEX[best_idx]["landmark_id"]
    landmark    = _lookup(landmark_id)

    return {
        "landmark": landmark,
        "score":    round(best_score / 100.0, 4),
        "surface":  best_surface,
    }


def apply_offset(lat: float, lon: float,
                 bearing, dist_m) -> tuple:
    """
    ACCEPTS : origin (lat, lon) and optional bearing (degrees) + dist_m.
              bearing=None or dist_m=None or dist_m=0 → return origin unchanged.
    PROCESS : Uses geopy.distance.geodesic.destination for accurate ellipsoidal
              displacement. Rounds output to 7 decimal places (~1 cm precision).
    PRODUCES: (new_lat, new_lon) as floats.
    """
    if bearing is None or dist_m is None or dist_m == 0:
        return (lat, lon)

    origin = geopy.Point(lat, lon)
    dest   = geopy.distance.geodesic(meters=dist_m).destination(
        point   = origin,
        bearing = bearing,
    )
    return (round(dest.latitude, 7), round(dest.longitude, 7))


def compute_confidence(fuzzy_score: float,
                       modifier_found: bool,
                       lang_known: bool) -> float:
    """
    ACCEPTS : fuzzy_score [0.0,1.0], modifier_found bool, lang_known bool.
    PROCESS : Weighted sum — weights sum exactly to 1.0:
                fuzzy match quality : 0.75
                modifier resolved   : 0.15
                language identified : 0.10
              Result clamped to [0.0, 1.0].
    PRODUCES: float confidence score rounded to 4 decimal places.
    """
    base       = fuzzy_score * 0.75
    mod_bonus  = 0.15 if modifier_found else 0.0
    lang_bonus = 0.10 if lang_known     else 0.0
    return round(min(max(base + mod_bonus + lang_bonus, 0.0), 1.0), 4)


# ════════════════════════════════════════════════════════════════════════════════
# MAIN PUBLIC API
# ════════════════════════════════════════════════════════════════════════════════

def resolve(text: str) -> dict:
    """
    ACCEPTS : Raw free-text delivery description (any language, any noise level).
              Examples:
                "inyuma ya big pharmacy on RN3, red gate"
                "derrière le marché de Kimironko"
                "near the bus stop, stage Remera"

    PROCESS : Pipeline — normalize → detect_language → extract_modifier
              → fuzzy_match_landmark → escalation check → apply_offset
              → compute_confidence → build rationale.
              Latency budget: mean < 100 ms (verified in eval.ipynb).

    PRODUCES: dict:
      lat               float | None   — predicted latitude
      lon               float | None   — predicted longitude
      confidence        float          — [0.0, 1.0]
      matched_landmark  str | None     — canonical English name
      landmark_id       str | None     — e.g. "lm_016"
      rationale         str            — human-readable explanation
      escalate          bool           — True → pass to dispatcher
      latency_ms        float          — wall-clock time for this call
    """
    t0 = time.perf_counter()

    # 1. Normalise
    norm = normalize_text(text)

    # 2. Detect language
    lang_result = detect_language(norm)
    lang_known  = lang_result["primary"] != "UNKNOWN"

    # 3. Extract modifier (directional offset)
    mod_result  = extract_modifier(norm)
    mod_found   = mod_result["bearing"] is not None

    # 4. Fuzzy-match landmark
    match = fuzzy_match_landmark(norm)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

    # 5. Escalation — no landmark found above threshold
    if match["landmark"] is None:
        if elapsed_ms > 100:
            print(
                f"[WARN] resolve() took {elapsed_ms} ms > 100 ms budget",
                file=sys.stderr,
            )
        return {
            "lat":              None,
            "lon":              None,
            "confidence":       0.0,
            "matched_landmark": None,
            "landmark_id":      None,
            "rationale":        "No landmark matched above threshold — escalated to dispatcher",
            "escalate":         True,
            "latency_ms":       elapsed_ms,
        }

    # 6. Apply directional offset from modifier
    lm = match["landmark"]
    final_lat, final_lon = apply_offset(
        lm["lat"], lm["lon"],
        mod_result["bearing"],
        mod_result["dist_m"],
    )

    # 7. Compute confidence
    confidence = compute_confidence(
        fuzzy_score    = match["score"],
        modifier_found = mod_found,
        lang_known     = lang_known,
    )

    # 8. Build human-readable rationale
    mod_desc = (
        f"modifier bearing={mod_result['bearing']}° dist={mod_result['dist_m']} m"
        if mod_found else "no spatial modifier detected"
    )
    rationale = (
        f"Matched '{lm['name']}' via surface '{match['surface']}' "
        f"(fuzzy={match['score']:.2f}, lang={lang_result['primary']}, {mod_desc})"
    )

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    if elapsed_ms > 100:
        print(
            f"[WARN] resolve() took {elapsed_ms} ms > 100 ms budget",
            file=sys.stderr,
        )

    return {
        "lat":              final_lat,
        "lon":              final_lon,
        "confidence":       confidence,
        "matched_landmark": lm["name"],
        "landmark_id":      lm["id"],
        "rationale":        rationale,
        "escalate":         False,
        "latency_ms":       elapsed_ms,
    }


# ════════════════════════════════════════════════════════════════════════════════
# UNIT TESTS
# Run:  python resolver.py   OR   pytest resolver.py -v
# All tests are self-contained and require only a valid gazetteer.json.
# ════════════════════════════════════════════════════════════════════════════════

def _run_tests():
    """Inline test runner used when pytest is not available."""
    import traceback
    passed = failed = 0

    def ok(name):
        nonlocal passed
        passed += 1
        print(f"  PASS  {name}")

    def fail(name, exc):
        nonlocal failed
        failed += 1
        print(f"  FAIL  {name}: {exc}")

    cases = []

    # ── normalize_text ────────────────────────────────────────────────────────
    cases.append(("normalize: emoji stripped",
                  lambda: assert_(normalize_text("near 📍 Kimironko") == "near kimironko")))
    cases.append(("normalize: lowercases",
                  lambda: assert_(normalize_text("Behind KIMIRONKO") == "behind kimironko")))
    cases.append(("normalize: collapses whitespace",
                  lambda: assert_(normalize_text("near   the   market") == "near the market")))
    cases.append(("normalize: empty string",
                  lambda: assert_(normalize_text("") == "")))
    cases.append(("normalize: only emoji",
                  lambda: assert_(normalize_text("📍🔴") == "")))
    cases.append(("normalize: NFC round-trip",
                  lambda: assert_(
                      normalize_text(unicodedata.normalize("NFD", "derrière")) == "derrière"
                  )))

    # ── detect_language ───────────────────────────────────────────────────────
    cases.append(("lang: KIN detected",
                  lambda: assert_(detect_language("inyuma ya isoko rya kimironko")["primary"] == "KIN")))
    cases.append(("lang: FR detected",
                  lambda: assert_(detect_language("derrière la pharmacie de kicukiro")["primary"] == "FR")))
    cases.append(("lang: EN detected",
                  lambda: assert_(detect_language("near the bus stop on kn5 ave")["primary"] == "EN")))
    cases.append(("lang: mixed flagged",
                  lambda: assert_(detect_language("near inyuma ya market")["mixed"] is True)))
    cases.append(("lang: empty no crash",
                  lambda: assert_("primary" in detect_language(""))))
    cases.append(("lang: no zero division",
                  lambda: assert_(detect_language("xyzzy qqqq 1234")["scores"] == {"KIN": 0, "FR": 0, "EN": 0})))

    # ── extract_modifier ──────────────────────────────────────────────────────
    cases.append(("modifier: inyuma ya → 180°",
                  lambda: assert_(extract_modifier("inyuma ya isoko")["bearing"] == 180.0)))
    cases.append(("modifier: derrière → 180°",
                  lambda: assert_(extract_modifier("derrière la pharmacie")["bearing"] == 180.0)))
    cases.append(("modifier: next to → 90°",
                  lambda: assert_(extract_modifier("next to the market")["bearing"] == 90.0)))
    cases.append(("modifier: none → None",
                  lambda: assert_(extract_modifier("kimironko market")["bearing"] is None)))
    cases.append(("modifier: specificity order",
                  lambda: assert_(
                      extract_modifier("inyuma ya near the pharmacy")["bearing"] == 180.0
                  )))

    # ── fuzzy_match_landmark ──────────────────────────────────────────────────
    cases.append(("fuzzy: exact EN match lm_016",
                  lambda: assert_(fuzzy_match_landmark("kimironko market")["landmark"]["id"] == "lm_016")))
    cases.append(("fuzzy: lev-1 typo still matches",
                  lambda: assert_(fuzzy_match_landmark("kimiromko market")["landmark"]["id"] == "lm_016")))
    cases.append(("fuzzy: FR alias matches lm_016",
                  lambda: assert_(fuzzy_match_landmark("marché de kimironko")["landmark"]["id"] == "lm_016")))
    cases.append(("fuzzy: KIN alias matches lm_016",
                  lambda: assert_(fuzzy_match_landmark("isoko rya kimironko")["landmark"]["id"] == "lm_016")))
    cases.append(("fuzzy: unknown → None",
                  lambda: assert_(fuzzy_match_landmark("xyzzy gibberish qqqq")["landmark"] is None)))
    cases.append(("fuzzy: score in [0,1]",
                  lambda: assert_(0.0 <= fuzzy_match_landmark("kimironko market")["score"] <= 1.0)))

    # ── apply_offset ──────────────────────────────────────────────────────────
    cases.append(("offset: None bearing → identity",
                  lambda: assert_(apply_offset(-1.94, 30.06, None, None) == (-1.94, 30.06))))
    cases.append(("offset: zero dist → identity",
                  lambda: assert_(apply_offset(-1.94, 30.06, 90, 0) == (-1.94, 30.06))))
    cases.append(("offset: East 100 m increases lon",
                  lambda: assert_(apply_offset(-1.94, 30.06, 90, 100)[1] > 30.06)))

    # ── compute_confidence ────────────────────────────────────────────────────
    cases.append(("confidence: max = 1.0",
                  lambda: assert_(compute_confidence(1.0, True, True) == 1.0)))
    cases.append(("confidence: no extras = 0.75",
                  lambda: assert_(compute_confidence(1.0, False, False) == 0.75)))
    cases.append(("confidence: 0.5 score = 0.375",
                  lambda: assert_(compute_confidence(0.5, False, False) == round(0.375, 4))))
    cases.append(("confidence: always in [0,1]",
                  lambda: assert_(0.0 <= compute_confidence(0.0, False, False) <= 1.0)))

    # ── resolve (integration) ─────────────────────────────────────────────────
    REQUIRED_KEYS = {"lat", "lon", "confidence", "matched_landmark",
                     "landmark_id", "rationale", "escalate", "latency_ms"}
    cases.append(("resolve: schema complete",
                  lambda: assert_(REQUIRED_KEYS.issubset(
                      resolve("inyuma ya big pharmacy on RN3, red gate").keys()
                  ))))
    cases.append(("resolve: escalation on gibberish",
                  lambda: assert_(resolve("xyzzy gibberish qqqq")["escalate"] is True)))
    cases.append(("resolve: confidence in [0,1]",
                  lambda: assert_(0.0 <= resolve("near Kimironko Market")["confidence"] <= 1.0)))
    cases.append(("resolve: offset moves coord",
                  lambda: assert_(
                      (resolve("kimironko market")["lat"],
                       resolve("kimironko market")["lon"]) !=
                      (resolve("behind kimironko market")["lat"],
                       resolve("behind kimironko market")["lon"])
                  )))
    cases.append(("resolve: French input works",
                  lambda: assert_(not resolve("derrière le marché de kimironko")["escalate"])))
    cases.append(("resolve: Kinyarwanda input works",
                  lambda: assert_(not resolve("hafi ya isoko rya kimironko")["escalate"])))

    # ── latency budget ────────────────────────────────────────────────────────
    def _latency_check():
        import statistics
        times = [resolve("near kimironko market")["latency_ms"] for _ in range(50)]
        avg = statistics.mean(times)
        assert avg < 100.0, f"Mean latency {avg:.1f} ms > 100 ms budget"

    cases.append(("resolve: mean latency < 100 ms (50 calls)", _latency_check))

    # ── Run all cases ─────────────────────────────────────────────────────────
    print(f"\nRunning {len(cases)} unit tests …\n")
    for name, fn in cases:
        try:
            fn()
            ok(name)
        except Exception as exc:
            fail(name, exc)

    print(f"\n{'─'*55}")
    print(f"Results: {passed} passed, {failed} failed out of {len(cases)} tests.")
    if failed:
        sys.exit(1)


def assert_(cond, msg="assertion failed"):
    if not cond:
        raise AssertionError(msg)


if __name__ == "__main__":
    # Try pytest first; fall back to inline runner
    try:
        import pytest  # type: ignore
        sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
    except ImportError:
        _run_tests()
