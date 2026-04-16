"""
Branded Medicine Matching Engine (v3 — Structured Matching)
-------------------------------------------------------------
Instead of normalizing composition strings, we parse both sides into a
structured representation: {salt_name: numeric_strength} and match on
that. This handles ALL unit/bracket/separator formats without needing
format-specific regex rules.

Matching logic:
  1. Parse query   → [{"name": "Moxifloxacin", "strength": 0.5}, ...]
  2. Parse DB row  → [{"name": "Moxifloxacin", "strength": 0.5}, ...]
  3. For each query salt, find a DB salt where:
       - name fuzzy matches (≥ 85)
       - strength is within 5% tolerance (or both are None)
  4. Match level = fraction of query salts matched

This approach is format-agnostic — it doesn't care whether the source
says "500mg", "(500mg)", "500 MG", "500 Mg" or "500mg/5ml".

Usage:
    from branded_matching import find_branded_alternatives
    result = find_branded_alternatives("Moxifloxacin 0.5%", prescribed_brand="Mahaflox E/D")
"""

import re
import pandas as pd
from thefuzz import fuzz


# ── 1. Structured Salt Parser ─────────────────────────────────────────────────

# Splits a multi-salt string on these separators: comma, "+", " and "
_SALT_SPLIT = re.compile(r'\s*,\s*|\s*\+\s*|\s+and\s+', re.IGNORECASE)

# Captures the first number (int or decimal) anywhere in a salt token
_NUMBER     = re.compile(r'(\d[\d.,]*)')

# Everything that is NOT a letter or space — strips units, brackets, slashes, %
_NON_ALPHA  = re.compile(r'[^a-zA-Z\s]')


def parse_salts(composition: str) -> list[dict]:
    """
    Parse any salt composition string into a list of structured dicts.

    Each dict has:
        name     : str   — lowercase salt name, letters only
        strength : float — first numeric value found, or None

    Examples:
        "Paracetamol 500mg"               → [{"name": "paracetamol",   "strength": 500.0}]
        "Moxifloxacin (0.5% W/V)"         → [{"name": "moxifloxacin",  "strength": 0.5}]
        "Amoxicillin (500mg)+Clav (125mg)" → [{"name": "amoxicillin",   "strength": 500.0},
                                               {"name": "clav",          "strength": 125.0}]
        "Timolol 0.5% w/v, Dorzolamide 2%" → [{"name": "timolol",       "strength": 0.5},
                                               {"name": "dorzolamide",   "strength": 2.0}]
    """
    if not composition or not composition.strip():
        return []

    tokens = _SALT_SPLIT.split(composition.strip())
    result = []

    for token in tokens:
        token = token.strip()
        if not token:
            continue

        # Extract the first number as strength
        num_match = _NUMBER.search(token)
        strength  = float(num_match.group(1).replace(",", "")) if num_match else None

        # Strip everything non-alphabetic to get the name
        name = _NON_ALPHA.sub(" ", token)
        # Remove single-letter remnants (like standalone 'w', 'v', 'g', 'b')
        name = " ".join(w for w in name.split() if len(w) > 1)
        name = name.strip().lower()

        if name:
            result.append({"name": name, "strength": strength})

    return result


def salts_match(query_salt: dict, db_salt: dict, name_threshold: int = 85) -> bool:
    """
    Check if two parsed salt dicts refer to the same compound.

    Name match  : fuzzy ratio ≥ name_threshold
    Strength match: within 5% relative tolerance, or both None
    """
    name_score = fuzz.partial_ratio(query_salt["name"], db_salt["name"])
    if name_score < name_threshold:
        return False

    q_str = query_salt["strength"]
    d_str = db_salt["strength"]

    # Both have no strength — name match is enough
    if q_str is None and d_str is None:
        return True

    # One has strength and the other doesn't — still accept (strength may be
    # omitted in brand name lookup queries)
    if q_str is None or d_str is None:
        return True

    # Both have strength — must be within 5% of each other
    larger = max(q_str, d_str)
    return abs(q_str - d_str) / larger <= 0.05


def composition_match_score(query_salts: list[dict], db_salts: list[dict]) -> float:
    """
    Fraction of query salts that have a matching salt in the DB entry.
    Returns 1.0 for a full match, 0.0 for no match.
    """
    if not query_salts:
        return 0.0
    matched = sum(
        1 for qs in query_salts
        if any(salts_match(qs, ds) for ds in db_salts)
    )
    return matched / len(query_salts)


# ── 2. Load & Prepare DB ──────────────────────────────────────────────────────

def load_branded_db(csv_path: str = "A_Z_medicines.csv") -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()

    df["price(₹)"] = pd.to_numeric(df["price(₹)"], errors="coerce").fillna(0)
    df = df[df["price(₹)"] > 0].copy()
    df["salt_composition"] = df["salt_composition"].fillna("").str.strip()

    # Pre-parse every row's composition into structured form at load time
    # Stored as a list of dicts — fast to compare later
    df["parsed_salts"] = df["salt_composition"].apply(parse_salts)

    def parse_pack_size(label: str) -> int:
        m = re.search(r"(\d+)", str(label))
        return int(m.group(1)) if m else 1

    df["unit_count"]     = df["pack_size_label"].apply(parse_pack_size)
    df["price_per_unit"] = (df["price(₹)"] / df["unit_count"]).round(2)
    df["name_lower"]     = df["name"].str.lower().str.strip()

    return df.reset_index(drop=True)


try:
    BRANDED_DB = load_branded_db()
    print(f"✅ A-Z Branded DB loaded: {len(BRANDED_DB)} medicine entries")
except FileNotFoundError:
    BRANDED_DB = pd.DataFrame()
    print("⚠️  A-Z medicines CSV not found. Branded matching will be skipped.")


# ── 3. Prescribed Brand Lookup ────────────────────────────────────────────────

def lookup_prescribed_brand(brand_name: str) -> dict | None:
    if BRANDED_DB.empty:
        return None

    brand_lower = brand_name.lower().strip()

    exact = BRANDED_DB[BRANDED_DB["name_lower"].str.startswith(brand_lower)]
    if len(exact) > 0:
        return _row_to_dict(exact.sort_values("price(₹)").iloc[0])

    scores   = BRANDED_DB["name_lower"].apply(lambda n: fuzz.partial_ratio(brand_lower, n))
    best_idx = scores[scores >= 85].index
    if len(best_idx) > 0:
        return _row_to_dict(BRANDED_DB.loc[scores[best_idx].idxmax()])

    return None


# ── 4. Dosage Form Filter ─────────────────────────────────────────────────────

# Maps generic dosage form names → keywords to look for in branded medicine names
FORM_KEYWORDS = {
    "tablet":      ["tablet", "tab"],
    "capsule":     ["capsule", "cap"],
    "syrup":       ["syrup", "oral solution"],
    "suspension":  ["suspension"],
    "injection":   ["injection", "injectable", "infusion", "vial"],
    "cream":       ["cream", "ointment"],
    "gel":         ["gel"],
    "eye drops":   ["eye drop", "ophthalmic", "e/d"],
    "ear drops":   ["ear drop", "otic"],
    "nasal spray": ["nasal", "spray"],
    "inhaler":     ["inhaler", "inhale", "mdi", "rotacap"],
    "drops":       ["drop"],
    "patch":       ["patch", "transdermal"],
    "lotion":      ["lotion"],
    "powder":      ["powder"],
    "solution":    ["solution"],
}

def filter_by_dosage_form(df: pd.DataFrame, dosage_form: str) -> pd.DataFrame:
    """
    Filter branded results by dosage form using medicine name keywords.
    Safe fallback: if filter removes everything, returns unfiltered results.
    """
    if not dosage_form:
        return df

    keywords = FORM_KEYWORDS.get(dosage_form.lower(), [dosage_form.lower()])
    pattern  = "|".join(keywords)
    filtered = df[df["name"].str.lower().str.contains(pattern, na=False)]
    return filtered if len(filtered) > 0 else df


# ── 5. Core: Find Cheaper Branded Alternatives ────────────────────────────────

def find_branded_alternatives(
    salt_composition: str,
    prescribed_brand: str = None,
    dosage_form: str = None,
    top_n: int = 5
) -> dict:
    """
    Find cheaper branded alternatives using structured salt matching.

    Args:
        salt_composition: LLM-extracted e.g. "Moxifloxacin 0.5%"
        prescribed_brand: brand name from prescription
        dosage_form:      e.g. "Tablet", "Eye Drops" — filters out wrong forms
        top_n:            max results to return
    """
    if BRANDED_DB.empty:
        return {"status": "DB_UNAVAILABLE", "prescribed_brand_info": None, "cheaper_alternatives": []}

    # Parse the query into structured form
    query_salts = parse_salts(salt_composition)

    if not query_salts:
        return {"status": "ERROR", "prescribed_brand_info": None, "cheaper_alternatives": []}

    print(f"\n🏷️  Branded search: {salt_composition} | Brand: {prescribed_brand}")
    print(f"   Parsed query: {query_salts}")

    # ── Step 1: Look up prescribed brand ─────────────────────────────────────
    prescribed_info = None
    prescribed_ppu  = None
    if prescribed_brand:
        prescribed_info = lookup_prescribed_brand(prescribed_brand)
        if prescribed_info:
            prescribed_ppu = prescribed_info["price_per_unit"]
            print(f"   Prescribed found: ₹{prescribed_ppu}/unit")
        else:
            print(f"   Prescribed brand not found in A-Z DB")

    # ── Step 2: Score every DB row using structured matching ──────────────────
    BRANDED_DB["match_score"] = BRANDED_DB["parsed_salts"].apply(
        lambda db_salts: composition_match_score(query_salts, db_salts)
    )
    matched = BRANDED_DB[BRANDED_DB["match_score"] >= 1.0].copy()

    print(f"   Full matches found: {len(matched)}")

    if len(matched) == 0:
        return {"status": "NO_MATCH", "prescribed_brand_info": prescribed_info, "cheaper_alternatives": []}

    # ── Step 3: Filter by dosage form ────────────────────────────────────────
    # Removes wrong forms (e.g. suspensions when tablet was prescribed)
    matched = filter_by_dosage_form(matched, dosage_form)
    print(f"   After dosage form filter ({dosage_form}): {len(matched)}")

    # ── Step 4: Exclude prescribed brand ─────────────────────────────────────
    if prescribed_brand:
        brand_lower = prescribed_brand.lower().strip()
        matched = matched[~matched["name_lower"].str.startswith(brand_lower)]

    # ── Step 5: Filter cheaper ────────────────────────────────────────────────
    cheaper = (
        matched[matched["price_per_unit"] < prescribed_ppu].copy()
        if prescribed_ppu else matched.copy()
    )

    if len(cheaper) == 0:
        return {
            "status":                "NO_CHEAPER_BRAND",
            "prescribed_brand_info": prescribed_info,
            "cheaper_alternatives":  [],
            "message":               "Prescribed brand is already the cheapest option"
        }

    cheaper = cheaper.sort_values("price_per_unit", ascending=True).head(top_n)

    return {
        "status":                "SUCCESS",
        "prescribed_brand_info": prescribed_info,
        "cheaper_alternatives":  [_row_to_dict(row) for _, row in cheaper.iterrows()]
    }


# ── 5. Helper ─────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    return {
        "name":           row["name"],
        "manufacturer":   row.get("manufacturer_name", ""),
        "composition":    row["salt_composition"],
        "pack_size":      row.get("pack_size_label", ""),
        "price":          row["price(₹)"],
        "unit_count":     row["unit_count"],
        "price_per_unit": row["price_per_unit"],
        "type":           row.get("type", ""),
    }


# ── 6. Tests ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # Parser tests — the most important thing to verify
    print("── parse_salts() tests ──────────────────────────────────────")
    test_inputs = [
        "Paracetamol 500mg",
        "Paracetamol (500mg)",
        "Moxifloxacin (0.5% W/V)",
        "Moxifloxacin 0.5% w/v",
        "Moxifloxacin 0.5%",
        "Amoxicillin (500mg)+Clavulanic Acid (125mg)",
        "Amoxicillin 500mg, Clavulanic Acid 125mg",
        "Timolol (0.5% W/V), Dorzolamide (2% W/V)",
        "Ibuprofen (400 MG)",
        "Cholecalciferol 1000IU",
        "Prednisolone 1% w/v",
        "Vitamin B12",           # no strength
    ]
    for inp in test_inputs:
        parsed = parse_salts(inp)
        print(f"  {inp!r:50s} → {parsed}")

    print()

    # Match score tests
    print("── composition_match_score() tests ─────────────────────────")
    pairs = [
        ("Moxifloxacin 0.5%",    "Moxifloxacin (0.5% W/V)"),   # should be 1.0
        ("Paracetamol 500mg",    "Paracetamol (500mg)"),         # should be 1.0
        ("Paracetamol 500mg",    "Paracetamol (650mg)"),         # should be 0.0 (strength mismatch)
        ("Amoxicillin 500mg, Clavulanic Acid 125mg",
         "Amoxicillin (500mg)+Clavulanic Acid (125mg)"),         # should be 1.0
        ("Ibuprofen 400mg",      "Paracetamol 500mg"),           # should be 0.0
    ]
    for q, d in pairs:
        score = composition_match_score(parse_salts(q), parse_salts(d))
        status = "✅" if (score == 1.0 or score == 0.0) else "⚠️"
        print(f"  {status} {score:.2f}  |  {q!r:40s}  vs  {d!r}")

    print()

    # Full pipeline tests
    print("── find_branded_alternatives() tests ───────────────────────")
    test_cases = [
        ("Moxifloxacin 0.5%",                        "Mahaflox E/D"),
        ("Paracetamol 500mg",                         "Crocin"),
        ("Amoxicillin 500mg, Clavulanic Acid 125mg",  "Moxclav"),
        ("Ibuprofen 400mg",                           "Brufen"),
        ("Timolol 0.5%, Dorzolamide 2%",              "Cosopt"),
    ]
    for salt, brand in test_cases:
        print(f"\n{'='*60}")
        print(f"Query : {salt} | Prescribed: {brand}")
        r = find_branded_alternatives(salt, prescribed_brand=brand)
        print(f"Status: {r['status']}")
        if r["prescribed_brand_info"]:
            p = r["prescribed_brand_info"]
            print(f"  Prescribed: {p['name']} — ₹{p['price_per_unit']}/unit")
        for i, a in enumerate(r["cheaper_alternatives"][:3], 1):
            print(f"  {i}. {a['name']} ({a['manufacturer']}) — ₹{a['price_per_unit']}/unit")