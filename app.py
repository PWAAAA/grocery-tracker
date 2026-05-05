"""
Flask backend for the Grocery Price Comparer UI.

Usage:
    pip install flask
    python app.py

Then open http://localhost:5000 in your browser.
"""

import logging
import os
import sys
import threading

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aldi_scraper import (
    DEFAULT_SHOP_ID,
    DEFAULT_ZONE_ID,
    DEFAULT_ZIP,
    AldiSession,
    find_products as aldi_find,
)
from walmart_scraper_copy import scrape_search as walmart_search
from unit_price import standardize_results as standardize_unit_prices

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder=".", static_url_path="")

# Shared Aldi session — reused across requests so we don't re-authenticate every time.
_aldi_session: AldiSession | None = None
_aldi_session_lock = threading.Lock()


def get_aldi_session() -> AldiSession:
    global _aldi_session
    with _aldi_session_lock:
        if _aldi_session is None:
            _aldi_session = AldiSession()
    return _aldi_session


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    stores = [s.strip() for s in request.args.get("stores", "aldi").split(",") if s.strip()]
    zip_code = request.args.get("zip", DEFAULT_ZIP).strip() or DEFAULT_ZIP
    limit = min(max(int(request.args.get("limit", 12)), 1), 48)

    if not q:
        return jsonify({"error": "No query provided"}), 400

    results: dict = {}

    if "aldi" in stores:
        try:
            session = get_aldi_session()
            products = aldi_find(q, postal_code=zip_code, limit=limit, session=session)
            from dataclasses import asdict
            results["aldi"] = [
                {**asdict(p), "store": "aldi"} for p in products if not p.error
            ]
        except Exception as e:
            log.error(f"Aldi search error: {e}")
            results["aldi"] = []
            results["aldi_error"] = str(e)

    if "walmart" in stores:
        try:
            raw = walmart_search(query=q, zip_code=zip_code)
            # Sponsored items go last; cap after sorting
            raw.sort(key=lambda p: bool(p.get("sponsored")))
            raw = raw[:limit]
            results["walmart"] = [
                {
                    "name": p.get("name"),
                    "product_id": p.get("product_id"),
                    "price": p.get("price"),
                    "price_string": f"${p['price']:.2f}" if p.get("price") is not None else None,
                    "unit_price_string": p.get("unit_price_string"),
                    "image_url": p.get("image"),
                    "url": p.get("url"),
                    "store": "walmart",
                    "in_stock": True,
                    "brand": None,
                    "size": None,
                    "sponsored": p.get("sponsored", False),
                }
                for p in raw
            ]
        except Exception as e:
            log.error(f"Walmart search error: {e}")
            results["walmart"] = []
            results["walmart_error"] = str(e)

    # Standardize unit prices across all stores in one pass so a single
    # toggle covers the whole query block. Mutates each product to add
    # `std_units`; one `unit_meta` covers everything.
    all_products = list(results.get("aldi") or []) + list(results.get("walmart") or [])
    meta = standardize_unit_prices(q, all_products)
    default_key = meta["unit_default"]
    if default_key:
        for p in all_products:
            std = p.get("std_units") or {}
            if default_key in std:
                p["unit_price_string"] = std[default_key]["string"]
    results["unit_meta"] = meta

    return jsonify(results)


if __name__ == "__main__":
    import webbrowser

    print("\n" + "=" * 50)
    print("  Grocery Price Comparer")
    print("  Opening http://localhost:5000")
    print("=" * 50 + "\n")
    webbrowser.open("http://localhost:5000")
    app.run(debug=False, port=5000, threaded=True)
