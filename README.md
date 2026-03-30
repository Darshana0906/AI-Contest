# GenericRx — Generic Medicine Finder

> Scan a branded prescription → get affordable generic alternatives from Jan Aushadhi DB instantly.

Branded medicines in India are 5–10x more expensive than their generic equivalents. GenericRx helps patients find the same salt composition at a fraction of the cost by scanning their prescription using AI.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [How It Works](#how-it-works)
- [Setup & Installation](#setup--installation)
- [Running the App](#running-the-app)
- [API Reference](#api-reference)
- [Key Design Decisions](#key-design-decisions)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)

---

## What It Does

1. Patient uploads a prescription photo (JPG, PNG, WEBP)
2. Vision LLM reads the prescription image
3. LLM extracts all medicines with their salt compositions
4. App searches Jan Aushadhi DB for matching generic alternatives
5. Patient sees generic name, pack size, MRP and per-unit price

**Example:**

```
Prescription: "Tab. Moxclav 625mg BD x 7 days"
              ↓
Salt found:   Amoxicillin 500mg, Clavulanic Acid 125mg
              ↓
Generic:      Amoxycillin 500mg + Clavulanic Acid 125mg
              + Lactic Acid Bacillus Tablets
              ₹93.75 for 10's | ₹9.38/unit
```

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Frontend | React.js | UI — upload, display results |
| Backend | Flask (Python) | REST API |
| Vision OCR | Groq — Llama 4 Scout 17B | Read prescription image |
| Drug extraction | Groq — Llama 3.3 70B | Extract medicines + salts |
| Matching | Pandas + thefuzz | Find generics in DB |
| LLM framework | LangChain | Model abstraction layer |
| Database | Jan Aushadhi CSV (2052 drugs) | Generic drug pricing |

---

## Project Structure

```
generic-medicine-finder/          # Flask backend
├── .env                          # API keys (never commit!)
├── .gitignore
├── Jan_Aushadhi.csv              # Government generic drug DB
│
├── app.py                        # Flask API — HTTP endpoints
├── ocr.py                        # Vision LLM — reads prescription image
├── extraction_chain.py           # LLM chain — extracts drug info from text
├── matching.py                   # Matching engine — finds generics in DB
├── pipeline.py                   # Wires OCR + extraction + matching together
│
└── venv/                         # Python virtual environment

generic-medicine-ui/              # React frontend
└── src/
    ├── App.js                    # Main component
    └── App.css                   # Styles
```

---

## How It Works

### Step 1 — OCR (`ocr.py`)

The prescription image is sent directly to **Llama 4 Scout 17B** (vision model) via Groq API. No traditional OCR library is used — the vision LLM reads and understands the image in one shot, handling:
- Handwritten prescriptions
- Printed prescriptions
- Messy/unclear text (e.g. `4OOmg` → `400mg`)
- Mixed languages

### Step 2 — Drug Extraction (`extraction_chain.py`)

The raw text from OCR is passed to **Llama 3.3 70B** via LangChain's structured output feature. The LLM returns a typed Pydantic object for each drug:

```python
class Drug(BaseModel):
    brand_name:       str    # "Moxclav"
    salt_composition: str    # "Amoxicillin 500mg, Clavulanic Acid 125mg"
    drug_class:       str    # "Antibiotic"
    dosage_form:      str    # "Capsule"
    confidence:       float  # 0.0 - 1.0
```

The LLM uses its training knowledge to map branded Indian drug names to their salt compositions (e.g. Crocin → Paracetamol 500mg, Pan → Pantoprazole 40mg).

### Step 3 — Generic Matching (`matching.py`)

The salt composition is matched against the Jan Aushadhi DB using:

1. **Salt name extraction** — strips dosage from composition
   `"Amoxicillin 500mg, Clavulanic Acid 125mg"` → `["Amoxicillin", "Clavulanic Acid"]`

2. **Fuzzy matching** — handles Indian spelling variants
   `Amoxicillin` ↔ `Amoxycillin` (88% similarity threshold via thefuzz)

3. **Scoring** — each DB row scored by how many prescription salts it contains
   All salts matched = score 1.0 (perfect), partial match = score 0.5+

4. **Strength filtering** — further filters by dosage strength (500mg, 650mg etc.)

5. **Smart sorting** — results sorted by fewest salts first (plain drug before combinations), then by price

Match levels returned:
- `EXACT` — all salts + strength match
- `GOOD` — all salts match, different strength
- `PARTIAL` — some salts match
- `NO_MATCH` — nothing found

### Step 4 — Flask API (`app.py`)

Two endpoints wrap the pipeline:
- `POST /api/scan` — accepts image upload
- `POST /api/scan/text` — accepts raw text (for testing)

### Step 5 — React UI (`App.js`)

Drag-and-drop upload interface that displays results as drug cards with generic alternatives table.

---

## Setup & Installation

### Prerequisites

- Python 3.12+
- Node.js 18+
- Ollama installed (for local models, optional)
- Groq API key — free at [console.groq.com](https://console.groq.com)

### Backend Setup

```bash
# 1. Clone the repo
git clone https://github.com/yourusername/generic-medicine-finder
cd generic-medicine-finder

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. Install dependencies
pip install langchain langchain-groq langchain-community \
            langchain-core pydantic pandas thefuzz \
            python-Levenshtein flask flask-cors \
            python-dotenv werkzeug

# 4. Create .env file
touch .env
# Add your Groq API key:
# GROQ_API_KEY=gsk_your_key_here
```

### Frontend Setup

```bash
# In a separate terminal
npx create-react-app generic-medicine-ui
cd generic-medicine-ui
npm install axios

# Replace src/App.js and src/App.css with the provided files
```

---

## Running the App

### Start Flask backend

```bash
cd generic-medicine-finder
source venv/bin/activate
python app.py
# API running at http://localhost:5000
```

### Start React frontend

```bash
cd generic-medicine-ui
npm start
# UI running at http://localhost:3000
```

### Test without UI (curl)

```bash
# Health check
curl http://localhost:5000/api/health

# Text-based test
curl -X POST http://localhost:5000/api/scan/text \
  -H "Content-Type: application/json" \
  -d '{"text": "Tab. Dolo 650mg TDS x 3 days, Cap. Moxclav 625mg BD x 7 days"}'

# Image upload test
curl -X POST http://localhost:5000/api/scan \
  -F "file=@prescription.jpg"
```

---

## API Reference

### `GET /api/health`

```json
{
  "status": "ok",
  "message": "Generic medicine finder API is running"
}
```

### `POST /api/scan`

**Request:** `multipart/form-data` with `file` field (jpg, jpeg, png, webp)

**Response:**
```json
{
  "success": true,
  "total_drugs": 2,
  "drugs": [
    {
      "brand_name": "Dolo",
      "salt_composition": "Paracetamol 650mg",
      "drug_class": "Analgesic",
      "dosage_form": "Tablet",
      "confidence": 0.9,
      "needs_fallback": false,
      "match_level": "EXACT",
      "generics": [
        {
          "generic_name": "Paracetamol Tablets IP 650 mg",
          "mrp": 14.07,
          "unit_size": "15's",
          "price_per_unit": 0.94,
          "group": "Analgesic/Antipyretic/Anti-Inflammatory",
          "match_score": 1.0
        }
      ]
    }
  ]
}
```

### `POST /api/scan/text`

**Request:**
```json
{
  "text": "Tab. Crocin 500mg twice daily for 5 days"
}
```

**Response:** Same format as `/api/scan`

---

## Key Design Decisions

### Why not RAG/Vector Search?

RAG is designed for unstructured document search. Jan Aushadhi DB is a structured CSV with 2052 entries — pandas string matching with fuzzy matching is faster, more precise, and safer for medical use.

Vector search returns semantically *similar* results. For medicines, similarity is dangerous — `Amoxicillin 250mg` is not a safe substitute for `Amoxicillin 500mg`. Exact matching is the right approach here.

RAG is planned for Phase 2 when the DB grows beyond 50,000 entries (CDSCO dataset) and for symptom-based search ("find something for acidity").

### Why Groq over local Ollama?

Local Qwen 2.5 7B was tested first but incorrectly identified `Pan` (Pantoprazole) as Paracetamol. Llama 3.3 70B on Groq gets it right. Groq's LPU chips also make inference ~10x faster than CPU-based Ollama.

LangChain's model abstraction means switching back to a local model (or any other provider) is a one-line change.

### Why Vision LLM over traditional OCR?

Traditional OCR (Tesseract, EasyOCR) extracts text blindly. A vision LLM reads the prescription and understands context — it correctly interprets `4OOmg` as `400mg`, handles mixed handwriting, and filters out non-drug text (doctor name, date, instructions).

### Why LangChain?

Single abstraction layer over all LLM providers. If Groq pricing changes or a better model is released, the entire pipeline switches with one line. Also provides structured output (Pydantic schemas) out of the box.

---

## Known Limitations

- **Jan Aushadhi stores only** — generics found are available at Jan Aushadhi government pharmacies, not all local chemists
- **2052 drug entries** — DB coverage is limited; rare drugs may not be found
- **Indian brand names** — LLM knowledge covers common Indian brands well but may miss niche/new brands
- **No drug interaction checking** — app only finds generics, does not check for interactions
- **Images only** — PDF support not yet implemented (Phase 2)
- **English prescriptions** — regional language prescriptions not yet supported

---

## Roadmap

### Phase 2 — Wider Coverage
- Add CDSCO dataset (100,000+ drugs)
- PDF prescription support

### Phase 3 — Smart Features
- RAG-based symptom search ("find something for acidity")
- Mobile app (React Native)

---

## Disclaimer

GenericRx is for informational purposes only. Always consult your doctor or pharmacist before substituting any medicine. The app does not provide medical advice.

---

## License

MIT License — free to use, modify and distribute.

## Author

Team DeepMinds.

