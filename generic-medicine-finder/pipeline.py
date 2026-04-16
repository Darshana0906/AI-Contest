"""
Full Pipeline (v2)
-------------------
Wires together:
  1. extraction_chain.py   — LLM extracts drugs from OCR text
  2. branded_matching.py   — prescribed brand price + cheaper branded alternatives
  3. matching.py           — Jan Aushadhi generics

Output per drug has 3 tiers:
  prescribed_brand    — price of the exact brand on the prescription
  cheaper_brands      — cheaper branded alternatives (same salt, lower price)
  generics            — Jan Aushadhi government generics

Usage:
    from pipeline import process_prescription
    results = process_prescription("Tab. Crocin 500mg BD x 5 days")
"""

import re
from extraction_chain import extract_drugs
from matching import find_generics
from branded_matching import find_branded_alternatives


# ── 1. Salt Composition Normalizer ───────────────────────────────────────────

def normalize_salt_composition(salt: str) -> str:
    """Normalize separators the LLM might return."""
    salt = re.sub(r'\s*\+\s*', ', ', salt)
    salt = re.sub(r'\s+and\s+', ', ', salt, flags=re.IGNORECASE)
    return salt.strip()


# ── 2. Core Pipeline ──────────────────────────────────────────────────────────

def process_prescription(ocr_text: str) -> dict:
    """
    Full pipeline: OCR text → extracted drugs → 3-tier price comparison.

    Args:
        ocr_text: raw text from prescription OCR

    Returns:
        dict with all drugs, each containing prescribed_brand,
        cheaper_brands, generics, and savings_vs_generic.
    """

    print(f"\n{'='*60}")
    print(f"🏥 Processing prescription...")
    print(f"{'='*60}")

    # ── Step 1: Extract drugs ─────────────────────────────────────────────────
    extraction = extract_drugs(ocr_text)

    if extraction["status"] != "SUCCESS" or not extraction["drugs"]:
        return {
            "status":  "ERROR",
            "message": "Could not extract drugs from prescription",
            "drugs":   []
        }

    # ── Step 2: Run all 3 lookups for each drug ───────────────────────────────
    results = []

    for drug in extraction["drugs"]:

        salt = normalize_salt_composition(drug["salt_composition"])
        print(f"\n💊 {drug['brand_name']} → {salt}")

        # 2a. Branded lookup — prescribed brand price + cheaper branded options
        branded_result = find_branded_alternatives(
            salt_composition=salt,
            prescribed_brand=drug["brand_name"],
            dosage_form=drug["dosage_form"],
        )

        prescribed_brand_info = branded_result.get("prescribed_brand_info")
        cheaper_brands        = branded_result.get("cheaper_alternatives", [])

        # 2b. Jan Aushadhi generics
        generic_result = find_generics(salt, dosage_form=drug["dosage_form"])

        generics = []
        if generic_result["status"] == "SUCCESS":
            generics = generic_result["results"]

        # ── Savings calculation ───────────────────────────────────────────────
        savings_vs_generic = None
        if prescribed_brand_info and generics:
            prescribed_ppu       = prescribed_brand_info["price_per_unit"]
            cheapest_generic_ppu = generics[0]["price_per_unit"]
            savings_ppu          = round(prescribed_ppu - cheapest_generic_ppu, 2)
            savings_vs_generic   = {
                "prescribed_price_per_unit": prescribed_ppu,
                "cheapest_generic_per_unit": cheapest_generic_ppu,
                "savings_per_unit":          savings_ppu,
                "savings_percent":           round((savings_ppu / prescribed_ppu) * 100, 1)
                                             if prescribed_ppu > 0 else 0
            }

        drug_result = {
            # Identity
            "brand_name":       drug["brand_name"],
            "salt_composition": salt,
            "drug_class":       drug["drug_class"],
            "dosage_form":      drug["dosage_form"],
            "confidence":       drug["confidence"],
            "needs_fallback":   drug["needs_fallback"],

            # Tier 1
            "prescribed_brand":      prescribed_brand_info,

            # Tier 2
            "cheaper_brands":        cheaper_brands,
            "cheaper_brands_status": branded_result["status"],

            # Tier 3
            "generics":              generics,
            "generic_match_level":   generic_result.get("match_level", "NONE"),
            "generic_status":        generic_result["status"],

            # Savings
            "savings_vs_generic":    savings_vs_generic,
        }

        results.append(drug_result)

    return {
        "status":      "SUCCESS",
        "total_drugs": len(results),
        "drugs":       results
    }


# ── 3. Pretty Printer ─────────────────────────────────────────────────────────

def print_results(pipeline_output: dict):
    if pipeline_output["status"] != "SUCCESS":
        print(f"❌ Error: {pipeline_output['message']}")
        return

    print(f"\n{'='*60}")
    print(f"📋 PRESCRIPTION ANALYSIS RESULTS")
    print(f"{'='*60}")
    print(f"Total drugs: {pipeline_output['total_drugs']}")

    for drug in pipeline_output["drugs"]:
        print(f"\n{'─'*50}")
        print(f"💊 Brand : {drug['brand_name']}")
        print(f"   Salt  : {drug['salt_composition']}")
        print(f"   Class : {drug['drug_class']} | Form: {drug['dosage_form']}")

        if drug["needs_fallback"]:
            print(f"   ⚠️  Low confidence — manual verification recommended")

        pb = drug["prescribed_brand"]
        if pb:
            print(f"\n   💰 Prescribed: {pb['name']} | ₹{pb['price_per_unit']}/unit")
        else:
            print(f"\n   ❓ Prescribed brand not found in A-Z DB")

        if drug["cheaper_brands"]:
            print(f"\n   🏷️  Cheaper branded alternatives:")
            for i, b in enumerate(drug["cheaper_brands"][:3], 1):
                print(f"   {i}. {b['name']} — ₹{b['price_per_unit']}/unit")
        else:
            print(f"\n   🏷️  Status: {drug['cheaper_brands_status']}")

        if drug["generics"]:
            print(f"\n   ✅ Jan Aushadhi ({drug['generic_match_level']} match):")
            for i, g in enumerate(drug["generics"][:3], 1):
                print(f"   {i}. {g['generic_name']} — ₹{g['price_per_unit']}/unit")
        else:
            print(f"\n   ❌ No Jan Aushadhi generics found")

        sv = drug["savings_vs_generic"]
        if sv and sv["savings_per_unit"] > 0:
            print(f"\n   💡 Generic saves ₹{sv['savings_per_unit']}/unit ({sv['savings_percent']}%)")


# ── 4. Sanity Test ────────────────────────────────────────────────────────────

if __name__ == "__main__":

    test_prescriptions = [
        "1. Tab. Dolo 650mg TDS x 3 days\n2. Cap. Moxclav 625mg BD x 7 days\n3. Tab. Cetzine 10mg OD x 5 days",
        "Mahaflox E/D 0.5% eye drops 1 drop TDS",
    ]

    for prescription in test_prescriptions:
        result = process_prescription(prescription)
        print_results(result)