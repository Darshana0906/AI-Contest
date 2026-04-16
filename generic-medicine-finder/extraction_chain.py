"""
LLM Extraction Chain
----------------------
Takes raw OCR text from a prescription and extracts
structured drug information using Qwen 2.5 7B via Ollama.

Usage:
    from extraction_chain import extract_drugs
    drugs = extract_drugs("Tab. Crocin 500mg x 5 days, Cap. Moxclav 625mg x 7 days")
"""
from dotenv import load_dotenv

load_dotenv()

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List


# ── 1. Pydantic Schemas ───────────────────────────────────────────────────────

class Drug(BaseModel):
    brand_name:       str   = Field(description="Brand name as written on prescription")
    salt_composition: str   = Field(description="Active salt(s) with strength e.g. 'Paracetamol 500mg' or 'Amoxicillin 500mg, Clavulanic Acid 125mg'")
    drug_class:       str   = Field(description="Therapeutic category e.g. Antibiotic, Analgesic")
    dosage_form:      str   = Field(description="Tablet, Capsule, Syrup, Injection etc.")
    confidence:       float = Field(description="Confidence score 0.0 to 1.0 for the salt identification")

class PrescriptionExtraction(BaseModel):
    drugs: List[Drug] = Field(description="List of all drugs found in the prescription")


# ── 2. LLM Setup ──────────────────────────────────────────────────────────────

llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0,
    
)
structured_llm = llm.with_structured_output(PrescriptionExtraction)


# ── 3. Prompt Template ────────────────────────────────────────────────────────

prompt = ChatPromptTemplate.from_messages([
    ("system", """You are a clinical pharmacist in India with expert knowledge of branded and generic medicines.

Your job is to extract drug information from prescription text (OCR output).

Rules:
- Identify ALL drugs mentioned in the prescription
- For each drug, identify the salt composition with exact strength
- For combination drugs, list ALL salts with their individual strengths
- ALWAYS use INN (International Nonproprietary Names) for salt names — never use common/colloquial names
- If strength is not mentioned, use the most common strength for that drug in India
- Set confidence < 0.8 if you are unsure about the salt composition
- Ignore non-drug text (doctor name, patient name, dates, instructions)"""),

    ("human", """Extract all drugs from this prescription text:

{ocr_text}

Return structured data with brand name, salt composition(INN names only - never comman names like vitamin C), drug class, dosage form and confidence for each drug.""")
])


# ── 4. Main Extraction Function ───────────────────────────────────────────────

def extract_drugs(ocr_text: str) -> dict:
    """
    Extract structured drug information from OCR prescription text.

    Args:
        ocr_text: raw text from OCR of prescription image

    Returns:
        dict with extracted drugs and metadata
    """

    print(f"\n💊 Extracting drugs from prescription...")
    print(f"   OCR text: {ocr_text[:100]}...")

    try:
        chain = prompt | structured_llm
        result: PrescriptionExtraction = chain.invoke({"ocr_text": ocr_text})

        drugs_list = []
        for drug in result.drugs:
            drugs_list.append({
                "brand_name":       drug.brand_name,
                "salt_composition": drug.salt_composition,
                "drug_class":       drug.drug_class,
                "dosage_form":      drug.dosage_form,
                "confidence":       drug.confidence,
                "needs_fallback":   drug.confidence < 0.8
            })

        print(f"✅ Found {len(drugs_list)} drug(s)")
        return {
            "status": "SUCCESS",
            "drugs":  drugs_list
        }

    except Exception as e:
        return {
            "status":  "ERROR",
            "message": str(e),
            "drugs":   []
        }


# ── 5. Sanity Tests ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    test_prescriptions = [
        # Simple single drug
        "Tab. Crocin 500mg - 1 tablet twice daily for 5 days",

        # Multiple drugs
        "1. Tab. Dolo 650mg TDS x 3 days\n2. Cap. Moxclav 625mg BD x 7 days\n3. Tab. Cetzine 10mg OD x 5 days",

        # Messy OCR style text
        "Brufen 4OOmg 1-0-1 Mox 5OOmg 1-1-1 Pan 4Omg 0-0-1",
    ]

    for i, prescription in enumerate(test_prescriptions, 1):
        print(f"\n{'='*60}")
        print(f"Test {i}: {prescription[:60]}...")
        result = extract_drugs(prescription)
        if result["status"] == "SUCCESS":
            for drug in result["drugs"]:
                print(f"\n  Brand     : {drug['brand_name']}")
                print(f"  Salt      : {drug['salt_composition']}")
                print(f"  Class     : {drug['drug_class']}")
                print(f"  Form      : {drug['dosage_form']}")
                print(f"  Confidence: {drug['confidence']}")
                if drug['needs_fallback']:
                    print(f"  ⚠️  Low confidence — fallback needed")
        else:
            print(f"  ❌ Error: {result['message']}")