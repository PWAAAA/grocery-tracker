"""
Flask backend for the Grocery Price Comparer UI.

Usage:
    pip install flask
    python app.py

Then open http://localhost:5000 in your browser.
"""

import logging
import os
import threading
from dataclasses import asdict

from flask import Flask, jsonify, request, send_from_directory

from scrapers.aldi import (
    DEFAULT_SHOP_ID,
    DEFAULT_ZONE_ID,
    DEFAULT_ZIP,
    AldiSession,
    find_products as aldi_find,
    scrape_products as aldi_scrape_products,
    extract_id_from_url as aldi_extract_id,
)
from scrapers.walmart import (
    scrape_search as walmart_search,
    scrape_product as walmart_scrape_product,
    extract_id_from_url as walmart_extract_id,
    find_stores_by_zip,
)
from pricing import standardize_results as standardize_unit_prices

GROCERY_LIST_PATH = os.path.join(os.path.dirname(__file__), "grocery_list.txt")

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


@app.route("/api/reverse-geocode")
def api_reverse_geocode():
    """Proxy reverse geocode to avoid browser CORS/header restrictions."""
    lat = request.args.get("lat", "").strip()
    lon = request.args.get("lon", "").strip()
    if not lat or not lon:
        return jsonify({"error": "lat and lon required"}), 400
    try:
        import requests as _req
        resp = _req.get(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json",
            headers={"User-Agent": "GroceryPriceComparer/1.0"},
            timeout=5,
        )
        data = resp.json()
        postcode = data.get("address", {}).get("postcode", "")
        return jsonify({"zip": postcode[:5] if postcode else ""})
    except Exception as e:
        log.error(f"Reverse geocode error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/stores")
def api_stores():
    zip_code = request.args.get("zip", "").strip()
    if not zip_code:
        return jsonify({"error": "No zip provided"}), 400
    try:
        stores = find_stores_by_zip(zip_code)
        return jsonify({"stores": stores})
    except Exception as e:
        log.error(f"Store lookup error: {e}")
        return jsonify({"error": str(e)}), 500


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
            results["aldi"] = [
                {**asdict(p), "store": "aldi"} for p in products if not p.error
            ]
        except Exception as e:
            log.error(f"Aldi search error: {e}")
            results["aldi"] = []
            results["aldi_error"] = str(e)

    if "walmart" in stores:
        try:
            store_id = request.args.get("store_id", "").strip() or None
            raw = walmart_search(query=q, zip_code=zip_code, store_id=store_id)
            # Filter out shipping-only products (no in-store pickup)
            raw = [p for p in raw if p.get("in_store", True)]
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
                    "in_stock": p.get("in_store", True),
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


def _read_grocery_list() -> list[str]:
    """Read grocery_list.txt and return non-empty, non-comment lines."""
    if not os.path.exists(GROCERY_LIST_PATH):
        return []
    with open(GROCERY_LIST_PATH, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def _write_grocery_list(items: list[str]):
    with open(GROCERY_LIST_PATH, "w", encoding="utf-8") as f:
        for item in items:
            f.write(item + "\n")


def _classify_item(item: str) -> dict:
    """Classify a grocery list item as a link or search term."""
    if item.startswith("http"):
        if "walmart.com" in item:
            return {"type": "link", "store": "walmart", "value": item}
        elif "aldi.us" in item:
            return {"type": "link", "store": "aldi", "value": item}
        else:
            return {"type": "link", "store": "unknown", "value": item}
    return {"type": "search", "value": item}


@app.route("/api/grocery-list", methods=["GET"])
def api_grocery_list_get():
    items = _read_grocery_list()
    classified = [_classify_item(item) for item in items]
    return jsonify({"items": classified})


@app.route("/api/grocery-list", methods=["POST"])
def api_grocery_list_post():
    data = request.get_json()
    if data is None:
        return jsonify({"error": "JSON body required"}), 400
    items = data.get("items", [])
    _write_grocery_list(items)
    classified = [_classify_item(item) for item in items]
    return jsonify({"items": classified})


@app.route("/api/fetch-links", methods=["POST"])
def api_fetch_links():
    """Fetch product data for direct store links. Returns product dicts ready for cart."""
    data = request.get_json()
    if data is None:
        return jsonify({"error": "JSON body required"}), 400

    links = data.get("links", [])
    zip_code = data.get("zip", DEFAULT_ZIP)
    store_id = data.get("store_id")
    results = []

    # Group links by store
    walmart_links = [l for l in links if "walmart.com" in l]
    aldi_links = [l for l in links if "aldi.us" in l]

    # Fetch Walmart products
    for link in walmart_links:
        pid = walmart_extract_id(link)
        if not pid:
            log.warning(f"Could not extract Walmart product ID from: {link}")
            continue
        try:
            product = walmart_scrape_product(pid, zip_code, store_id)
            if product.error:
                log.warning(f"Walmart product {pid}: {product.error}")
                continue
            results.append({
                "name": product.name,
                "product_id": product.product_id,
                "price": product.price,
                "price_string": product.price_string,
                "unit_price_string": product.unit_price_string,
                "image_url": product.image_url,
                "url": product.url,
                "store": "walmart",
                "in_stock": product.in_stock,
                "brand": product.brand,
                "size": None,
                "sponsored": False,
            })
        except Exception as e:
            log.error(f"Error fetching Walmart product {pid}: {e}")

    # Fetch Aldi products
    aldi_ids = []
    for link in aldi_links:
        pid = aldi_extract_id(link)
        if pid:
            aldi_ids.append(pid)
        else:
            log.warning(f"Could not extract Aldi product ID from: {link}")

    if aldi_ids:
        try:
            session = get_aldi_session()
            products = aldi_scrape_products(aldi_ids, postal_code=zip_code, session=session)
            for p in products:
                if p.error:
                    continue
                results.append({
                    **asdict(p),
                    "store": "aldi",
                })
        except Exception as e:
            log.error(f"Error fetching Aldi products: {e}")

    return jsonify({"products": results})


if __name__ == "__main__":
    import webbrowser

    print("\n" + "=" * 50)
    print("  Grocery Price Comparer")
    print("  Opening http://localhost:5000")
    print("=" * 50 + "\n")
    webbrowser.open("http://localhost:5000")
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
