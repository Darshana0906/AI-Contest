"""
Flask API
----------
Wraps the full pipeline as HTTP endpoints for React frontend.

Endpoints:
    POST /api/scan          — upload prescription image
    POST /api/scan/text     — send raw text (for testing)
    GET  /api/health        — health check

Usage:
    python app.py
"""

import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from ocr import process_prescription_image
from pipeline import process_prescription

load_dotenv()

# ── App Setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER      = "uploads"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"]      = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB


# ── Helpers ───────────────────────────────────────────────────────────────────

def allowed_file(filename: str) -> bool:
    return "." in filename and \
           filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def format_brand_info(info: dict | None) -> dict | None:
    """Serialize a single branded medicine entry for the frontend."""
    if not info:
        return None
    return {
        "name":           info["name"],
        "manufacturer":   info.get("manufacturer", ""),
        "composition":    info.get("composition", ""),
        "pack_size":      info.get("pack_size", ""),
        "price":          info["price"],
        "price_per_unit": info["price_per_unit"],
    }


def format_response(pipeline_output: dict) -> dict:
    """
    Serialize full 3-tier pipeline output for the React frontend.

    Each drug includes:
        brand_name, salt_composition, drug_class, dosage_form,
        confidence, needs_fallback,
        prescribed_brand        ← Tier 1
        cheaper_brands          ← Tier 2
        cheaper_brands_status
        generics                ← Tier 3
        generic_match_level
        generic_status
        savings_vs_generic
    """
    if pipeline_output["status"] != "SUCCESS":
        return {
            "success": False,
            "error":   pipeline_output.get("message", "Unknown error"),
            "drugs":   []
        }

    drugs = []
    for drug in pipeline_output["drugs"]:
        drugs.append({
            # Identity
            "brand_name":       drug["brand_name"],
            "salt_composition": drug["salt_composition"],
            "drug_class":       drug["drug_class"],
            "dosage_form":      drug["dosage_form"],
            "confidence":       drug["confidence"],
            "needs_fallback":   drug["needs_fallback"],

            # Tier 1 — prescribed brand price
            "prescribed_brand": format_brand_info(drug.get("prescribed_brand")),

            # Tier 2 — cheaper branded alternatives
            "cheaper_brands": [
                format_brand_info(b) for b in drug.get("cheaper_brands", [])[:5]
            ],
            "cheaper_brands_status": drug.get("cheaper_brands_status", "UNKNOWN"),

            # Tier 3 — Jan Aushadhi generics
            "generics": [
                {
                    "generic_name":   g["generic_name"],
                    "mrp":            g["mrp"],
                    "unit_size":      g["unit_size"],
                    "price_per_unit": g["price_per_unit"],
                    "group":          g.get("group", ""),
                    "match_score":    g.get("match_score", 0),
                }
                for g in drug.get("generics", [])[:5]
            ],
            "generic_match_level": drug.get("generic_match_level", "NONE"),
            "generic_status":      drug.get("generic_status", "UNKNOWN"),

            # Savings summary
            "savings_vs_generic": drug.get("savings_vs_generic"),
        })

    response = {
        "success":     True,
        "total_drugs": pipeline_output["total_drugs"],
        "drugs":       drugs
    }

    # Debug — prints full response to Flask terminal so you can verify all
    # fields are present. Remove this once everything is working.
    print("\nDEBUG format_response output:")
    print(json.dumps(response, indent=2, default=str))

    return response


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "ok",
        "message": "Generic medicine finder API is running"
    })


@app.route("/api/scan", methods=["POST"])
def scan_prescription():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded. Send file in 'file' field."}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"success": False, "error": "Empty filename"}), 400

    if not allowed_file(file.filename):
        return jsonify({"success": False, "error": f"File type not allowed. Use: {ALLOWED_EXTENSIONS}"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)
    print(f"\n📁 File saved: {filepath}")

    try:
        result = process_prescription_image(filepath)
        os.remove(filepath)
        return jsonify(format_response(result))
    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scan/text", methods=["POST"])
def scan_text():
    data = request.get_json()

    if not data or "text" not in data:
        return jsonify({"success": False, "error": "Send JSON with 'text' field"}), 400

    text = data["text"].strip()
    if not text:
        return jsonify({"success": False, "error": "Empty text"}), 400

    try:
        result = process_prescription(text)
        return jsonify(format_response(result))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🚀 Starting Generic Medicine Finder API...")
    print("   Health check : http://localhost:5000/api/health")
    print("   Scan image   : POST http://localhost:5000/api/scan")
    print("   Scan text    : POST http://localhost:5000/api/scan/text")
    app.run(debug=True, host="0.0.0.0", port=5000)