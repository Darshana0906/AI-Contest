"""
Jan Aushadhi Matching Engine
------------------------------
Matches LLM-extracted salt compositions to Jan Aushadhi generic drugs.
Uses pandas string matching + fuzzy matching (thefuzz).

Usage:
    from matching import find_generics
    results = find_generics("Amoxicillin 500mg, Clavulanic Acid 125mg")
"""

import re
import pandas as pd
from thefuzz import fuzz


# ── 1. Load & Prepare DB (runs once on import) ────────────────────────────────

def load_db(csv_path: str = "Jan_Aushadhi.csv") -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip().str.lstrip("\ufeff").str.strip('"')
    df["MRP"] = pd.to_numeric(df["MRP"], errors="coerce").fillna(0)
    df = df[df["MRP"] > 0].copy()
    df["Generic Name"] = df["Generic Name"].str.replace(
        r"\s+and\s+", ", ", regex=True
    )

    def parse_unit_count(unit_size: str) -> int:
        match = re.search(r"(\d+)", str(unit_size))
        return int(match.group(1)) if match else 1

    df["unit_count"]     = df["Unit Size"].apply(parse_unit_count)
    df["price_per_unit"] = (df["MRP"] / df["unit_count"]).round(2)
    return df.reset_index(drop=True)


DB = load_db()
print(f"✅ Jan Aushadhi DB loaded: {len(DB)} drug entries")


# ── 2. Synonym Map ────────────────────────────────────────────────────────────

SYNONYMS = {
    "Vitamin D3":  "Cholecalciferol",
    "Vitamin D":   "Cholecalciferol",
    "Vitamin B12": "Cyanocobalamin",
    "Vitamin B1":  "Thiamine",
    "Vitamin B2":  "Riboflavin",
    "Vitamin B6":  "Pyridoxine",
    "Vitamin B9":  "Folic Acid",
    "Vitamin C":   "Ascorbic Acid",
    "Vitamin E":   "Tocopherol",
    "Vitamin K":   "Phytomenadione",
    "Vitamin A":   "Retinol",
}

def resolve_synonyms(salt_names: list) -> list:
    return [SYNONYMS.get(s.strip(), s) for s in salt_names]


# ── 3. Salt Extraction Helpers ────────────────────────────────────────────────

def extract_salt_names(salt_composition: str) -> list:
    """
    Extract just the drug names from a salt composition string.
    "Amoxicillin 500mg, Clavulanic Acid 125mg" → ["Amoxicillin", "Clavulanic Acid"]
    "Vitamin D3 1000IU" → ["Cholecalciferol"]
    Preserves: D3, B12, B6 — numbers that are PART of the name
    """
    # Remove parenthetical alternate names e.g. "Vitamin D3 (Cholecalciferol)" → "Vitamin D3"
    salt_composition = re.sub(r'\(.*?\)', '', salt_composition)

    salts = [s.strip() for s in salt_composition.split(",")]
    cleaned = []
    for salt in salts:
        # Remove dosage patterns: 500mg, 125 mg, 0.025%w/w, 1000IU, 60 Million Spores
        name = re.sub(
            r'\d[\d,.]*\s*(%\s*w/w|%|mg|ml|mcg|iu|million\s*spore[s]?|per\s*\d+\s*ml)',
            '', salt, flags=re.IGNORECASE
        )
        # Remove standalone numbers BUT preserve numbers attached to letters (D3, B12)
        name = re.sub(r'(?<![A-Za-z])\b\d+\b(?![A-Za-z])', '', name)
        name = name.strip().strip(',').strip()
        if name:
            cleaned.append(name)

    cleaned = resolve_synonyms(cleaned)
    return cleaned


def salt_in_generic(salt_name: str, generic_name: str) -> bool:
    """Check if a salt name is present in a generic drug name using fuzzy matching."""
    if salt_name.lower() in generic_name.lower():
        return True
    ratio = fuzz.partial_ratio(salt_name.lower(), generic_name.lower())
    return ratio >= 88


# ── 4. Dosage Form Filter ─────────────────────────────────────────────────────

FORM_KEYWORDS = {
    "nasal spray":   ["nasal", "spray"],
    "inhaler":       ["inhalation", "inhaler", "mdi"],
    "tablet":        ["tablet", "tablets"],
    "capsule":       ["capsule", "capsules"],
    "syrup":         ["syrup", "oral solution"],
    "suspension":    ["suspension"],
    "injection":     ["injection", "injectable", "infusion"],
    "cream":         ["cream", "ointment"],
    "gel":           ["gel"],
    "drops":         ["drops", "drop"],
    "nebuliser":     ["nebuliser", "nebulizer"],
    "eye drops":     ["eye drops", "ophthalmic"],
    "ear drops":     ["ear drops", "otic"],
    "patch":         ["patch", "transdermal"],
    "suppository":   ["suppository"],
    "lotion":        ["lotion"],
    "powder":        ["powder"],
}

def filter_by_dosage_form(df: pd.DataFrame, dosage_form: str) -> pd.DataFrame:
    """
    Filter results by dosage form.
    Only applies filter if it doesn't wipe out all results — safe fallback.
    """
    if not dosage_form:
        return df

    keywords = FORM_KEYWORDS.get(dosage_form.lower(), [dosage_form.lower()])
    pattern  = '|'.join(keywords)
    filtered = df[df["Generic Name"].str.lower().str.contains(pattern, na=False)]

    # Safety net — if filter wipes everything, return unfiltered
    return filtered if len(filtered) > 0 else df


# ── 5. Core Matching Function ─────────────────────────────────────────────────

def find_generics(salt_composition: str,
                  dosage_form: str = None,
                  top_n: int = 5) -> dict:
    """
    Find generic alternatives for a given salt composition.

    Args:
        salt_composition: e.g. "Budesonide 100mcg"
        dosage_form:      e.g. "Nasal Spray" — filters results by form
        top_n:            max results to return

    Match levels:
        EXACT    - all salts + strength match
        GOOD     - all salts match, different strength
        PARTIAL  - some salts match
        NO_MATCH - nothing found
    """

    salt_names = extract_salt_names(salt_composition)

    if not salt_names:
        return {
            "status":  "ERROR",
            "message": "Could not parse salt composition",
            "results": []
        }

    print(f"\n🔍 Searching for: {salt_composition}")
    print(f"   Extracted salts: {salt_names}")
    print(f"   Dosage form: {dosage_form}")

    # Score each DB entry
    def score_row(generic_name: str) -> float:
        matched = sum(
            1 for salt in salt_names
            if salt_in_generic(salt, generic_name)
        )
        return matched / len(salt_names)

    DB["match_score"] = DB["Generic Name"].apply(score_row)

    # Filter by match level
    perfect_matches = DB[DB["match_score"] >= 1.0].copy()

    if len(perfect_matches) > 0:
        # Try to find exact strength match
        strengths = re.findall(r'(\d+)\s*mg', salt_composition, re.IGNORECASE)
        exact_strength = perfect_matches.copy()
        for strength in strengths:
            exact_strength = exact_strength[
                exact_strength["Generic Name"].str.contains(strength, na=False)
            ]

        if len(exact_strength) > 0:
            result_df  = exact_strength
            match_level = "EXACT"
        else:
            result_df  = perfect_matches
            match_level = "GOOD"

    else:
        partial = DB[DB["match_score"] >= 0.5].copy()
        if len(partial) > 0:
            result_df  = partial
            match_level = "PARTIAL"
        else:
            return {
                "status":         "NO_MATCH",
                "message":        f"No generics found for: {salt_composition}",
                "salts_searched": salt_names,
                "results":        []
            }

    # Apply dosage form filter
    result_df = filter_by_dosage_form(result_df, dosage_form)

    # Sort: fewest salts first, then best match, then cheapest
    result_df["salt_count"] = result_df["Generic Name"].str.count(",") + 1
    result_df = result_df.sort_values(
        ["salt_count", "match_score", "price_per_unit"],
        ascending=[True, False, True]
    ).head(top_n)

    results = []
    for _, row in result_df.iterrows():
        results.append({
            "generic_name":   row["Generic Name"],
            "drug_code":      str(row["Drug Code"]),
            "mrp":            row["MRP"],
            "unit_size":      row["Unit Size"],
            "price_per_unit": row["price_per_unit"],
            "group":          row["Group Name"],
            "match_score":    round(row["match_score"], 2),
        })

    return {
        "status":         "SUCCESS",
        "match_level":    match_level,
        "salts_searched": salt_names,
        "total_found":    len(result_df),
        "results":        results
    }


# ── 6. Savings Calculator ─────────────────────────────────────────────────────

def calculate_savings(branded_price: float, generic_price: float) -> dict:
    if branded_price <= 0:
        return {"savings_amount": 0, "savings_percent": 0}
    savings_amount  = branded_price - generic_price
    savings_percent = round((savings_amount / branded_price) * 100, 1)
    return {
        "savings_amount":  round(savings_amount, 2),
        "savings_percent": savings_percent,
        "is_cheaper":      savings_amount > 0
    }


# ── 7. Sanity Tests ───────────────────────────────────────────────────────────

if __name__ == "__main__":

    test_cases = [
        ("Paracetamol 500mg",                         "Tablet"),
        ("Amoxicillin 500mg, Clavulanic Acid 125mg",  "Capsule"),
        ("Ibuprofen 400mg",                           "Tablet"),
        ("Cetirizine 10mg",                           "Tablet"),
        ("Budesonide 100mcg",                         "Nasal Spray"),  # form filter test
        ("Cholecalciferol 1000IU",                    "Capsule"),       # vitamin test
    ]

    for salt, form in test_cases:
        result = find_generics(salt, dosage_form=form)
        print(f"\n{'='*60}")
        print(f"Query : {salt} | Form: {form}")
        print(f"Status: {result['status']} | Level: {result.get('match_level')}")
        print(f"Found : {result.get('total_found')} results")
        for r in result["results"][:3]:
            print(f"  → {r['generic_name']}")
            print(f"     ₹{r['mrp']} for {r['unit_size']} | ₹{r['price_per_unit']}/unit")