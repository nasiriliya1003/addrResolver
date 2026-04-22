"""
generate_data.py
════════════════════════════════════════════════════════════════════════════════
ACCEPTS  : Nothing (self-contained). Reads no external files.
PROCESSES: Builds 50 real Kigali landmarks, then synthesises
           200 free-text delivery descriptions in EN/FR/KIN with
           injected noise (typos, alias swaps, emoji, minibus stop appends).
           True coordinates are computed via a geodesic formula from each
           landmark coordinate + a signed Gaussian offset in the direction
           implied by the spatial modifier.
PRODUCES :
  data/gazetteer.json      — 50 landmark objects (consumed by resolver.py)
  data/descriptions.csv    — 200 description rows (consumed by eval.ipynb)
  data/gold_visible.csv    — 25 gold rows given to the candidate
  data/gold.csv            — 50 gold rows (evaluator-held; first 25 = visible)
════════════════════════════════════════════════════════════════════════════════
"""

import json
import os
import csv
from math import radians, sin, cos, asin, atan2, degrees, sqrt
import numpy as np

# ─── Reproducibility ──────────────────────────────────────────────────────────
RANDOM_SEED = 42
N_DESC      = 200   # total descriptions to generate
N_GOLD      = 50    # gold rows (25 visible + 25 evaluator-held)
NOISE_PROB  = 0.35  # 35 % of descriptions get noise injection

# ─── Modifier table ───────────────────────────────────────────────────────────
# key → (EN surface, FR surface, KIN surface, bearing_degrees, dist_metres)
# bearing: 0 = North, 90 = East, 180 = South, 270 = West
MODIFIERS = {
    "behind"  : ("behind",    "derrière",     "inyuma ya",  180, 40),
    "next_to" : ("next to",   "à côté de",    "hafi ya",     90, 20),
    "near"    : ("near",      "près de",      "hafi ya",      0, 30),
    "opposite": ("opposite",  "en face de",   "inyuma ya",  180, 15),
    "above"   : ("above",     "au-dessus de", "hejuru ya",  350, 25),
}

LANG_PROBS   = [0.50, 0.25, 0.25]   # EN, FR, KIN sampling weights
LANG_CHOICES = ["EN", "FR", "KIN"]

NOISE_TYPES = [
    "typo_lev1",       # single-char mutation on a word ≥ 5 chars
    "typo_lev2",       # two-char mutations on a word ≥ 5 chars
    "alias_swap",      # replace landmark surface with alternate-language alias
    "emoji_inject",    # insert one emoji at a random character position
    "minibus_append",  # append ", stage <stop_name>"
]

EMOJIS = ["📍", "🏍️", "🏪", "🔴", "🟢"]

KIGALI_ROADS = [
    "RN3", "RN4", "KN5 Ave", "KG 11 Ave",
    "KG 7 Ave", "KN 3 Rd", "KG 9 Ave",
]

COLOR_GATES = [
    "red gate", "blue gate", "green fence",
    "yellow wall", "white gate",
]

MINIBUS_STOPS = [
    "Nyabugogo", "Remera", "Sonatubes", "Kimironko",
    "Gikondo", "Kanombe", "Kacyiru", "Kicukiro Centre",
]

# Type descriptors in each language — used to decorate descriptions
TYPE_DESCRIPTORS = {
    "EN": {
        "pharmacy":   ["the pharmacy", "the big pharmacy", "the chemist"],
        "church":     ["the church", "the big church", "the cathedral"],
        "market":     ["the market", "the big market"],
        "bus_stop":   ["the bus stop", "the stage", "the terminus"],
        "hotel":      ["the hotel"],
        "roundabout": ["the roundabout", "the junction"],
        "school":     ["the school", "the college"],
        "stadium":    ["the stadium"],
        "office":     ["the offices", "the building"],
    },
    "FR": {
        "pharmacy":   ["la pharmacie", "la grande pharmacie"],
        "church":     ["l'église", "la grande église"],
        "market":     ["le marché", "le grand marché"],
        "bus_stop":   ["le terminus", "l'arrêt"],
        "hotel":      ["l'hôtel"],
        "roundabout": ["le carrefour", "le rond-point"],
        "school":     ["l'école", "le collège"],
        "stadium":    ["le stade"],
        "office":     ["les bureaux", "le bâtiment"],
    },
    "KIN": {
        "pharmacy":   ["farumasi", "ifarumasi nini"],
        "church":     ["kiliziya", "itorero"],
        "market":     ["isoko", "isoko rinini"],
        "bus_stop":   ["agatehe", "aho imodoka ihagarara"],
        "hotel":      ["oteli"],
        "roundabout": ["ahazunguruka", "junction"],
        "school":     ["ishuri", "kaminuza"],
        "stadium":    ["stadiyumu"],
        "office":     ["ibiro", "inyubako"],
    },
}


# ─── Gazetteer ────────────────────────────────────────────────────────────────

def build_gazetteer():
    """
    Return the fixed list of 50 real Kigali landmarks.
    Each landmark is a plain dict matching the gazetteer.json schema.
    Covers all three districts: Nyarugenge, Gasabo, Kicukiro.
    """
    landmarks = [
        # ── Nyarugenge ──────────────────────────────────────────────────────
        {"id": "lm_001", "name": "Nyabugogo Bus Terminal",
         "aliases": {"fr": "Terminus de Nyabugogo", "kin": "Agatehe ka Nyabugogo"},
         "type": "bus_stop", "lat": -1.9323, "lon": 30.0441, "district": "Nyarugenge"},
        {"id": "lm_002", "name": "Nyarugenge Market",
         "aliases": {"fr": "Marché de Nyarugenge", "kin": "Isoko ya Nyarugenge"},
         "type": "market", "lat": -1.9500, "lon": 30.0590, "district": "Nyarugenge"},
        {"id": "lm_003", "name": "Kigali City Tower",
         "aliases": {"fr": "Tour de Kigali", "kin": "Inyubako ya Kigali"},
         "type": "office", "lat": -1.9441, "lon": 30.0619, "district": "Nyarugenge"},
        {"id": "lm_004", "name": "Saint Michel Cathedral",
         "aliases": {"fr": "Cathédrale Saint Michel", "kin": "Kiliziya ya Saint Michel"},
         "type": "church", "lat": -1.9469, "lon": 30.0590, "district": "Nyarugenge"},
        {"id": "lm_005", "name": "Pharmacie Centrale Kigali",
         "aliases": {"fr": "Pharmacie Centrale", "kin": "Ifarumasi Nkuru ya Kigali"},
         "type": "pharmacy", "lat": -1.9452, "lon": 30.0611, "district": "Nyarugenge"},
        {"id": "lm_006", "name": "Kigali Central Post Office",
         "aliases": {"fr": "Bureau de Poste Central", "kin": "Poste Nkuru ya Kigali"},
         "type": "office", "lat": -1.9460, "lon": 30.0600, "district": "Nyarugenge"},
        {"id": "lm_007", "name": "Muhima Health Centre",
         "aliases": {"fr": "Centre de Santé Muhima", "kin": "Ivuriro rya Muhima"},
         "type": "office", "lat": -1.9410, "lon": 30.0530, "district": "Nyarugenge"},
        {"id": "lm_008", "name": "Kigali Central Roundabout",
         "aliases": {"fr": "Carrefour Central de Kigali", "kin": "Ahazunguruka ka Kigali"},
         "type": "roundabout", "lat": -1.9445, "lon": 30.0625, "district": "Nyarugenge"},
        {"id": "lm_009", "name": "Bralirwa Depot Nyarugenge",
         "aliases": {"fr": "Dépôt Bralirwa", "kin": "Aho Bralirwa ikorera"},
         "type": "office", "lat": -1.9380, "lon": 30.0510, "district": "Nyarugenge"},
        {"id": "lm_010", "name": "Nyarugenge Secondary School",
         "aliases": {"fr": "École Secondaire de Nyarugenge", "kin": "Ishuri Rikuru rya Nyarugenge"},
         "type": "school", "lat": -1.9490, "lon": 30.0570, "district": "Nyarugenge"},
        {"id": "lm_011", "name": "Pharmacie du Peuple",
         "aliases": {"fr": "Pharmacie du Peuple Nyarugenge", "kin": "Ifarumasi y'Abaturage"},
         "type": "pharmacy", "lat": -1.9435, "lon": 30.0595, "district": "Nyarugenge"},
        {"id": "lm_012", "name": "Hotel des Mille Collines",
         "aliases": {"fr": "Hôtel des Mille Collines", "kin": "Oteli y'Imisozi"},
         "type": "hotel", "lat": -1.9454, "lon": 30.0602, "district": "Nyarugenge"},
        {"id": "lm_013", "name": "Rwandan Parliament Building",
         "aliases": {"fr": "Parlement Rwandais", "kin": "Inzu Nkuru y'Abadepite"},
         "type": "office", "lat": -1.9530, "lon": 30.0612, "district": "Nyarugenge"},
        {"id": "lm_014", "name": "Kigali Private Hospital",
         "aliases": {"fr": "Hôpital Privé de Kigali", "kin": "Ibitaro bya Kigali"},
         "type": "office", "lat": -1.9465, "lon": 30.0640, "district": "Nyarugenge"},
        {"id": "lm_015", "name": "Station Total Nyarugenge",
         "aliases": {"fr": "Station Total Nyarugenge", "kin": "Sitasiyo ya Total Nyarugenge"},
         "type": "office", "lat": -1.9420, "lon": 30.0555, "district": "Nyarugenge"},
        # ── Gasabo ───────────────────────────────────────────────────────────
        {"id": "lm_016", "name": "Kimironko Market",
         "aliases": {"fr": "Marché de Kimironko", "kin": "Isoko rya Kimironko"},
         "type": "market", "lat": -1.9355, "lon": 30.1021, "district": "Gasabo"},
        {"id": "lm_017", "name": "Kigali Convention Centre",
         "aliases": {"fr": "Centre de Convention de Kigali", "kin": "Inyubako ya Nama"},
         "type": "office", "lat": -1.9536, "lon": 30.0934, "district": "Gasabo"},
        {"id": "lm_018", "name": "Remera Bus Stop",
         "aliases": {"fr": "Arrêt de Remera", "kin": "Agatehe ka Remera"},
         "type": "bus_stop", "lat": -1.9411, "lon": 30.1101, "district": "Gasabo"},
        {"id": "lm_019", "name": "Kacyiru Police Station",
         "aliases": {"fr": "Commissariat de Kacyiru", "kin": "Polisi ya Kacyiru"},
         "type": "office", "lat": -1.9342, "lon": 30.0895, "district": "Gasabo"},
        {"id": "lm_020", "name": "Amahoro National Stadium",
         "aliases": {"fr": "Stade National Amahoro", "kin": "Stadiyumu y'Amahoro"},
         "type": "stadium", "lat": -1.9444, "lon": 30.1175, "district": "Gasabo"},
        {"id": "lm_021", "name": "Kigali Public Library",
         "aliases": {"fr": "Bibliothèque Publique de Kigali", "kin": "Inyubako y'Ibitabo"},
         "type": "office", "lat": -1.9500, "lon": 30.0914, "district": "Gasabo"},
        {"id": "lm_022", "name": "Eglise Restauration Kimironko",
         "aliases": {"fr": "Église Restauration", "kin": "Kiliziya ya Restauration"},
         "type": "church", "lat": -1.9340, "lon": 30.1040, "district": "Gasabo"},
        {"id": "lm_023", "name": "Pharmacie Kimironko",
         "aliases": {"fr": "Pharmacie de Kimironko", "kin": "Ifarumasi ya Kimironko"},
         "type": "pharmacy", "lat": -1.9360, "lon": 30.1035, "district": "Gasabo"},
        {"id": "lm_024", "name": "Kacyiru Hospital",
         "aliases": {"fr": "Hôpital de Kacyiru", "kin": "Ibitaro bya Kacyiru"},
         "type": "office", "lat": -1.9328, "lon": 30.0901, "district": "Gasabo"},
        {"id": "lm_025", "name": "Gasabo District Office",
         "aliases": {"fr": "Bureau du District Gasabo", "kin": "Ibiro bya Akarere ka Gasabo"},
         "type": "office", "lat": -1.9389, "lon": 30.0982, "district": "Gasabo"},
        {"id": "lm_026", "name": "Sonatubes Bus Stop",
         "aliases": {"fr": "Arrêt Sonatubes", "kin": "Agatehe ka Sonatubes"},
         "type": "bus_stop", "lat": -1.9500, "lon": 30.1050, "district": "Gasabo"},
        {"id": "lm_027", "name": "Kigali Heights Shopping Mall",
         "aliases": {"fr": "Centre Commercial Kigali Heights", "kin": "Isoko Rinini rya Kigali Heights"},
         "type": "market", "lat": -1.9381, "lon": 30.0903, "district": "Gasabo"},
        {"id": "lm_028", "name": "Pharmacie Kacyiru",
         "aliases": {"fr": "Pharmacie de Kacyiru", "kin": "Ifarumasi ya Kacyiru"},
         "type": "pharmacy", "lat": -1.9335, "lon": 30.0898, "district": "Gasabo"},
        {"id": "lm_029", "name": "Rwandair Headquarters",
         "aliases": {"fr": "Siège de Rwandair", "kin": "Ibiro Nkuru bya Rwandair"},
         "type": "office", "lat": -1.9290, "lon": 30.1320, "district": "Gasabo"},
        {"id": "lm_030", "name": "Remera Roundabout",
         "aliases": {"fr": "Carrefour de Remera", "kin": "Ahazunguruka ka Remera"},
         "type": "roundabout", "lat": -1.9397, "lon": 30.1115, "district": "Gasabo"},
        {"id": "lm_031", "name": "Ecole Primaire Kimironko",
         "aliases": {"fr": "École Primaire de Kimironko", "kin": "Ishuri Ryibanze rya Kimironko"},
         "type": "school", "lat": -1.9371, "lon": 30.1025, "district": "Gasabo"},
        {"id": "lm_032", "name": "Kigali Marriott Hotel",
         "aliases": {"fr": "Hôtel Marriott Kigali", "kin": "Oteli ya Marriott"},
         "type": "hotel", "lat": -1.9540, "lon": 30.0929, "district": "Gasabo"},
        {"id": "lm_033", "name": "Vision 2020 Roundabout",
         "aliases": {"fr": "Rond-Point Vision 2020", "kin": "Ahazunguruka ka Vision"},
         "type": "roundabout", "lat": -1.9358, "lon": 30.0870, "district": "Gasabo"},
        {"id": "lm_034", "name": "Pharmacie Remera",
         "aliases": {"fr": "Pharmacie de Remera", "kin": "Ifarumasi ya Remera"},
         "type": "pharmacy", "lat": -1.9405, "lon": 30.1108, "district": "Gasabo"},
        {"id": "lm_035", "name": "Kigali Arena",
         "aliases": {"fr": "Arène de Kigali", "kin": "Inyubako ya Kigali Arena"},
         "type": "stadium", "lat": -1.9544, "lon": 30.0938, "district": "Gasabo"},
        # ── Kicukiro ─────────────────────────────────────────────────────────
        {"id": "lm_036", "name": "Kicukiro Centre Bus Stop",
         "aliases": {"fr": "Arrêt Centre Kicukiro", "kin": "Agatehe ka Kicukiro"},
         "type": "bus_stop", "lat": -1.9741, "lon": 30.0877, "district": "Kicukiro"},
        {"id": "lm_037", "name": "Gikondo Market",
         "aliases": {"fr": "Marché de Gikondo", "kin": "Isoko rya Gikondo"},
         "type": "market", "lat": -1.9755, "lon": 30.0687, "district": "Kicukiro"},
        {"id": "lm_038", "name": "Kigali International Airport",
         "aliases": {"fr": "Aéroport International de Kigali", "kin": "Inzira y'Indege ya Kigali"},
         "type": "office", "lat": -1.9685, "lon": 30.1395, "district": "Kicukiro"},
        {"id": "lm_039", "name": "Nyamirambo Mosque",
         "aliases": {"fr": "Mosquée de Nyamirambo", "kin": "Masigiti ya Nyamirambo"},
         "type": "church", "lat": -1.9720, "lon": 30.0451, "district": "Kicukiro"},
        {"id": "lm_040", "name": "Pharmacie Kicukiro",
         "aliases": {"fr": "Pharmacie de Kicukiro", "kin": "Ifarumasi ya Kicukiro"},
         "type": "pharmacy", "lat": -1.9738, "lon": 30.0881, "district": "Kicukiro"},
        {"id": "lm_041", "name": "Centre Hospitalier Universitaire CHUK",
         "aliases": {"fr": "CHU de Kigali", "kin": "Ibitaro bya Kaminuza CHUK"},
         "type": "office", "lat": -1.9559, "lon": 30.0622, "district": "Kicukiro"},
        {"id": "lm_042", "name": "Kicukiro District Office",
         "aliases": {"fr": "Bureau du District Kicukiro", "kin": "Ibiro bya Akarere ka Kicukiro"},
         "type": "office", "lat": -1.9750, "lon": 30.0870, "district": "Kicukiro"},
        {"id": "lm_043", "name": "Kanombe Market",
         "aliases": {"fr": "Marché de Kanombe", "kin": "Isoko rya Kanombe"},
         "type": "market", "lat": -1.9674, "lon": 30.1285, "district": "Kicukiro"},
        {"id": "lm_044", "name": "Gikondo Roundabout",
         "aliases": {"fr": "Carrefour de Gikondo", "kin": "Ahazunguruka ka Gikondo"},
         "type": "roundabout", "lat": -1.9731, "lon": 30.0699, "district": "Kicukiro"},
        {"id": "lm_045", "name": "Eglise Catholique Kicukiro",
         "aliases": {"fr": "Église Catholique de Kicukiro", "kin": "Kiliziya Gatorika ya Kicukiro"},
         "type": "church", "lat": -1.9745, "lon": 30.0865, "district": "Kicukiro"},
        {"id": "lm_046", "name": "Pharmacie Gikondo",
         "aliases": {"fr": "Pharmacie de Gikondo", "kin": "Ifarumasi ya Gikondo"},
         "type": "pharmacy", "lat": -1.9748, "lon": 30.0701, "district": "Kicukiro"},
        {"id": "lm_047", "name": "Lycee de Kigali",
         "aliases": {"fr": "Lycée de Kigali", "kin": "Ishuri Rikuru rya Kigali"},
         "type": "school", "lat": -1.9625, "lon": 30.0741, "district": "Kicukiro"},
        {"id": "lm_048", "name": "Sonatubes Roundabout Kicukiro",
         "aliases": {"fr": "Carrefour Sonatubes Kicukiro", "kin": "Ahazunguruka ka Sonatubes"},
         "type": "roundabout", "lat": -1.9680, "lon": 30.0810, "district": "Kicukiro"},
        {"id": "lm_049", "name": "Gatenga Market",
         "aliases": {"fr": "Marché de Gatenga", "kin": "Isoko rya Gatenga"},
         "type": "market", "lat": -1.9800, "lon": 30.0780, "district": "Kicukiro"},
        {"id": "lm_050", "name": "Hotel Chez Lando",
         "aliases": {"fr": "Hôtel Chez Lando", "kin": "Oteli ya Chez Lando"},
         "type": "hotel", "lat": -1.9535, "lon": 30.0657, "district": "Kicukiro"},
    ]
    assert len(landmarks) == 50, f"Expected 50 landmarks, got {len(landmarks)}"
    ids = [lm["id"] for lm in landmarks]
    assert len(set(ids)) == 50, "Duplicate landmark ids detected"
    return landmarks


# ─── Geodesic helpers ─────────────────────────────────────────────────────────

def _geodesic_destination(lat, lon, bearing_deg, dist_m):
    """
    Return the destination point (lat, lon) reached by travelling
    dist_m metres from (lat, lon) on the given bearing (degrees).
    Uses the spherical-Earth approximation — accurate to < 0.3 % for
    distances ≤ 500 m (sufficient for our 60 m noise scale).
    """
    R     = 6_371_000.0
    lat_r = radians(lat)
    lon_r = radians(lon)
    b_r   = radians(bearing_deg)
    d     = dist_m / R  # angular distance in radians

    new_lat_r = asin(
        sin(lat_r) * cos(d) + cos(lat_r) * sin(d) * cos(b_r)
    )
    new_lon_r = lon_r + atan2(
        sin(b_r) * sin(d) * cos(lat_r),
        cos(d) - sin(lat_r) * sin(new_lat_r),
    )
    return degrees(new_lat_r), degrees(new_lon_r)


def compute_true_coords(landmark, modifier_key, rng):
    """
    Compute the synthetic 'true' delivery coordinate.

    True coord = landmark coord displaced by (dist_m + N(0,60 m))
    in the direction implied by the modifier. A negative total
    displacement reverses the bearing (moves to the opposite side)
    rather than using abs(), preserving realistic spread.
    """
    _, _, _, bearing, dist_m = MODIFIERS[modifier_key]
    noise_m    = rng.normal(0, 60)           # signed Gaussian noise
    total_dist = dist_m + noise_m            # can be negative

    if total_dist >= 0:
        actual_bearing = bearing
        actual_dist    = total_dist
    else:
        actual_bearing = (bearing + 180) % 360   # reverse direction
        actual_dist    = abs(total_dist)

    new_lat, new_lon = _geodesic_destination(
        landmark["lat"], landmark["lon"], actual_bearing, actual_dist
    )
    return round(new_lat, 7), round(new_lon, 7)


# ─── Noise injection ──────────────────────────────────────────────────────────

def _single_mutation(word, rng):
    """Apply one of {delete, substitute, transpose} to a random char."""
    op = rng.choice(["delete", "substitute", "transpose"])
    i  = int(rng.integers(0, len(word)))
    if op == "delete":
        return word[:i] + word[i + 1:]
    if op == "substitute":
        alphabet = "abcdefghijklmnopqrstuvwxyz"
        repl = rng.choice([c for c in alphabet if c != word[i]])
        return word[:i] + repl + word[i + 1:]
    # transpose
    if i < len(word) - 1:
        lst = list(word)
        lst[i], lst[i + 1] = lst[i + 1], lst[i]
        return "".join(lst)
    # fallback: substitute last char
    return word[:i] + rng.choice(list("abcdefghijklmnopqrstuvwxyz")) + word[i + 1:]


def inject_noise(text, noise_type, landmark, rng):
    """
    Inject one noise event into text.
    Requires the source landmark so alias_swap can find/replace
    the correct surface form with an alternate-language alias.
    All noise types are fully implemented — no silent no-ops except
    alias_swap when no landmark surface appears in text (safe fallback).
    """
    if noise_type == "typo_lev1":
        words = text.split()
        candidates = [w for w in words if len(w) >= 5 and w.isalpha()]
        if not candidates:
            return text
        target = str(rng.choice(candidates))
        noisy  = _single_mutation(target, rng)
        return text.replace(target, noisy, 1)

    if noise_type == "typo_lev2":
        words = text.split()
        candidates = [w for w in words if len(w) >= 5 and w.isalpha()]
        if not candidates:
            return text
        target = str(rng.choice(candidates))
        noisy  = _single_mutation(_single_mutation(target, rng), rng)
        return text.replace(target, noisy, 1)

    if noise_type == "alias_swap":
        # All surface forms for this landmark, longest-first to avoid
        # partial substring replacement
        surfaces = sorted(
            [landmark["name"],
             landmark["aliases"]["fr"],
             landmark["aliases"]["kin"]],
            key=len, reverse=True,
        )
        for surface in surfaces:
            if surface.lower() in text.lower():
                idx = text.lower().index(surface.lower())
                alternates = [s for s in surfaces if s != surface]
                replacement = str(rng.choice(alternates))
                return text[:idx] + replacement + text[idx + len(surface):]
        return text  # safe fallback: surface not found in text

    if noise_type == "emoji_inject":
        pos   = int(rng.integers(0, len(text) + 1))
        emoji = str(rng.choice(EMOJIS))
        return text[:pos] + emoji + text[pos:]

    if noise_type == "minibus_append":
        stop = str(rng.choice(MINIBUS_STOPS))
        return text + ", stage " + stop

    return text  # unknown type — no-op


# ─── Description generator ────────────────────────────────────────────────────

def generate_description(landmark, modifier_key, lang, rng):
    """
    Construct one free-text delivery description from a landmark,
    a spatial modifier, and a target language.
    Optionally appends a Kigali road name (30 % chance) and a
    colour/gate descriptor (20 % chance).
    """
    en_mod, fr_mod, kin_mod, _, _ = MODIFIERS[modifier_key]
    modifier_str = {"EN": en_mod, "FR": fr_mod, "KIN": kin_mod}[lang]

    name_str = {
        "EN":  landmark["name"],
        "FR":  landmark["aliases"]["fr"],
        "KIN": landmark["aliases"]["kin"],
    }[lang]

    type_desc = str(rng.choice(TYPE_DESCRIPTORS[lang][landmark["type"]]))

    text = f"{modifier_str} {type_desc} {name_str}"

    if rng.random() < 0.30:                        # road suffix
        text += " on " + str(rng.choice(KIGALI_ROADS))
    if rng.random() < 0.20:                        # gate suffix
        text += ", " + str(rng.choice(COLOR_GATES))

    return text


# ─── CSV writer ───────────────────────────────────────────────────────────────

def _write_csv(filepath, rows, fieldnames):
    """Write a list-of-dicts to CSV with explicit fieldnames."""
    with open(filepath, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ─── Main entry point ─────────────────────────────────────────────────────────

def generate_all(seed=RANDOM_SEED):
    """
    Synthesise and write all data files.
    Execution is fully deterministic for any fixed seed.
    Gold rows (0–49) are the first N_GOLD descriptions, ensuring
    every gold description_id is present in descriptions.csv.
    """
    rng       = np.random.default_rng(seed)
    gazetteer = build_gazetteer()

    os.makedirs("data", exist_ok=True)

    descriptions = []
    gold_rows    = []

    # ── Gold rows first (desc_0000 – desc_0049) ────────────────────────────
    for i in range(N_GOLD):
        landmark  = gazetteer[int(rng.integers(0, len(gazetteer)))]
        mod_key   = str(rng.choice(list(MODIFIERS.keys())))
        lang      = str(rng.choice(LANG_CHOICES, p=LANG_PROBS))

        text               = generate_description(landmark, mod_key, lang, rng)
        true_lat, true_lon = compute_true_coords(landmark, mod_key, rng)

        # Noise is textual only — applied after coords are recorded
        if rng.random() < NOISE_PROB:
            noise_type = str(rng.choice(NOISE_TYPES))
            text = inject_noise(text, noise_type, landmark, rng)

        descriptions.append({
            "description_id":   f"desc_{i:04d}",
            "description_text": text,
            "language_hint":    lang,
        })
        gold_rows.append({
            "description_id": f"desc_{i:04d}",
            "true_lat":       true_lat,
            "true_lon":       true_lon,
        })

    # ── Remaining descriptions (desc_0050 – desc_0199) ─────────────────────
    for i in range(N_GOLD, N_DESC):
        landmark = gazetteer[int(rng.integers(0, len(gazetteer)))]
        mod_key  = str(rng.choice(list(MODIFIERS.keys())))
        lang     = str(rng.choice(LANG_CHOICES, p=LANG_PROBS))

        text = generate_description(landmark, mod_key, lang, rng)
        if rng.random() < NOISE_PROB:
            noise_type = str(rng.choice(NOISE_TYPES))
            text = inject_noise(text, noise_type, landmark, rng)

        descriptions.append({
            "description_id":   f"desc_{i:04d}",
            "description_text": text,
            "language_hint":    lang,
        })

    # ── Write output files ─────────────────────────────────────────────────
    with open("data/gazetteer.json", "w", encoding="utf-8") as fh:
        json.dump(gazetteer, fh, ensure_ascii=False, indent=2)

    _write_csv("data/descriptions.csv", descriptions,
               ["description_id", "description_text", "language_hint"])

    _write_csv("data/gold_visible.csv", gold_rows[:25],
               ["description_id", "true_lat", "true_lon"])

    _write_csv("data/gold.csv", gold_rows,
               ["description_id", "true_lat", "true_lon"])

    print("✓ data/gazetteer.json      — 50 landmarks")
    print("✓ data/descriptions.csv   — 200 descriptions")
    print("✓ data/gold_visible.csv   — 25 rows (visible to candidate)")
    print("✓ data/gold.csv           — 50 rows (full, evaluator-held)")


if __name__ == "__main__":
    generate_all(seed=RANDOM_SEED)
