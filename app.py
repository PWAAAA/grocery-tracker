"""
Flask backend for the Grocery Price Comparer UI.

Usage:
    pip install flask
    python app.py

Then open http://localhost:5000 in your browser.
"""

import json
import logging
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
    find_stores_by_zip as aldi_find_stores,
)
from scrapers.walmart import (
    scrape_search as walmart_search,
    scrape_product as walmart_scrape_product,
    extract_id_from_url as walmart_extract_id,
    find_stores_by_zip,
)
from scrapers.amazon import (
    scrape_search as amazon_search,
    scrape_product as amazon_scrape_product,
    extract_id_from_url as amazon_extract_id,
    find_stores_by_zip as amazon_find_stores,
)
from pricing import standardize_results as standardize_unit_prices
from database import (
    init_db, get_grocery_items, set_grocery_items, classify_item,
    get_recipes, get_recipe, create_recipe, update_recipe, delete_recipe,
    add_ingredient, update_ingredient, delete_ingredient,
    add_ingredient_product, delete_ingredient_product, get_ingredient_products,
    update_ingredient_product,
)
from pricing.cooking import compute_ingredient_cost, format_ingredient_cost_breakdown, COOKING_UNITS
from pricing.serving_size import parse_serving_size_density

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
    store_type = request.args.get("store", "walmart").strip().lower()
    if not zip_code:
        return jsonify({"error": "No zip provided"}), 400
    try:
        if store_type == "aldi":
            stores = aldi_find_stores(zip_code, session=get_aldi_session())
        elif store_type == "amazon":
            stores = amazon_find_stores(zip_code)
        else:
            stores = find_stores_by_zip(zip_code)
        return jsonify({"stores": stores})
    except Exception as e:
        log.error(f"Store lookup error ({store_type}): {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    stores = [s.strip() for s in request.args.get("stores", "aldi").split(",") if s.strip()]
    zip_code = request.args.get("zip", DEFAULT_ZIP).strip() or DEFAULT_ZIP
    limit = min(max(int(request.args.get("limit", 12)), 1), 96)

    if not q:
        return jsonify({"error": "No query provided"}), 400

    results: dict = {}

    if "aldi" in stores:
        try:
            session = get_aldi_session()
            aldi_shop_id = request.args.get("aldi_shop_id", "").strip() or None
            aldi_zone_id = request.args.get("aldi_zone_id", "").strip() or None
            products = aldi_find(
                q,
                shop_id=aldi_shop_id or DEFAULT_SHOP_ID,
                zone_id=aldi_zone_id or DEFAULT_ZONE_ID,
                postal_code=zip_code,
                limit=limit,
                session=session,
            )
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
            raw = walmart_search(query=q, zip_code=zip_code, store_id=store_id, limit=limit)
            # Filter out shipping-only products (no in-store pickup)
            pre_filter = len(raw)
            raw = [p for p in raw if p.get("in_store", True)]
            if pre_filter != len(raw):
                log.info(f"Walmart '{q}': filtered {pre_filter - len(raw)}/{pre_filter} shipping-only items")
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
                    "serving_size": None,
                    "sponsored": p.get("sponsored", False),
                }
                for p in raw
            ]
            log.info(f"Walmart '{q}': returning {len(results['walmart'])} results")
        except Exception as e:
            log.error(f"Walmart search error: {e}")
            results["walmart"] = []
            results["walmart_error"] = str(e)

    if "amazon" in stores:
        try:
            fresh_only = request.args.get("amazon_fresh", "").strip().lower() == "true"
            raw = amazon_search(query=q, zip_code=zip_code, limit=limit, fresh_only=fresh_only)
            # Filter to Prime-eligible only (unless searching Fresh specifically)
            if not fresh_only:
                pre_filter = len(raw)
                raw = [p for p in raw if p.get("is_prime", False)]
                if pre_filter != len(raw):
                    log.info(f"Amazon '{q}': filtered {pre_filter - len(raw)}/{pre_filter} non-Prime items")
            # Sponsored items go last; cap after sorting
            raw.sort(key=lambda p: bool(p.get("sponsored")))
            raw = raw[:limit]
            results["amazon"] = [
                {
                    "name": p.get("name"),
                    "product_id": p.get("product_id"),
                    "price": p.get("price"),
                    "price_string": p.get("price_string") or (f"${p['price']:.2f}" if p.get("price") is not None else None),
                    "unit_price_string": p.get("unit_price_string"),
                    "image_url": p.get("image"),
                    "url": p.get("url"),
                    "store": "amazon",
                    "in_stock": True,
                    "brand": None,
                    "size": None,
                    "serving_size": None,
                    "sponsored": p.get("sponsored", False),
                    "is_prime": p.get("is_prime", False),
                    "is_fresh": p.get("is_fresh", False),
                }
                for p in raw
            ]
            log.info(f"Amazon '{q}': returning {len(results['amazon'])} results")
        except Exception as e:
            log.error(f"Amazon search error: {e}")
            results["amazon"] = []
            results["amazon_error"] = str(e)

    # Standardize unit prices across all stores in one pass so a single
    # toggle covers the whole query block. Mutates each product to add
    # `std_units`; one `unit_meta` covers everything.
    all_products = list(results.get("aldi") or []) + list(results.get("walmart") or []) + list(results.get("amazon") or [])
    meta = standardize_unit_prices(q, all_products)
    default_key = meta["unit_default"]
    if default_key:
        for p in all_products:
            std = p.get("std_units") or {}
            if default_key in std:
                p["unit_price_string"] = std[default_key]["string"]
    results["unit_meta"] = meta

    return jsonify(results)


@app.route("/api/grocery-list", methods=["GET"])
def api_grocery_list_get():
    items = get_grocery_items()
    return jsonify({"items": items})


@app.route("/api/grocery-list", methods=["POST"])
def api_grocery_list_post():
    data = request.get_json()
    if data is None:
        return jsonify({"error": "JSON body required"}), 400
    raw_items = data.get("items", [])
    classified = [classify_item(item) if isinstance(item, str) else item for item in raw_items]
    set_grocery_items(classified)
    return jsonify({"items": get_grocery_items()})


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
    amazon_links = [l for l in links if "amazon.com" in l]

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
                "serving_size": product.serving_size,
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

    # Fetch Amazon products
    for link in amazon_links:
        pid = amazon_extract_id(link)
        if not pid:
            log.warning(f"Could not extract Amazon ASIN from: {link}")
            continue
        try:
            product = amazon_scrape_product(pid, zip_code)
            if product.error:
                log.warning(f"Amazon product {pid}: {product.error}")
                continue
            results.append({
                **asdict(product),
                "store": "amazon",
            })
        except Exception as e:
            log.error(f"Error fetching Amazon product {pid}: {e}")

    # Standardize unit prices so fetched products get std_units like search results
    meta = standardize_unit_prices("", results)
    default_key = meta["unit_default"]
    if default_key:
        for p in results:
            std = p.get("std_units") or {}
            if default_key in std:
                p["unit_price_string"] = std[default_key]["string"]

    return jsonify({"products": results, "unit_meta": meta})


# ── Recipe endpoints ───────────────────────────────────────────────

@app.route("/api/recipes", methods=["GET"])
def api_recipes_list():
    return jsonify({"recipes": get_recipes()})


@app.route("/api/recipes", methods=["POST"])
def api_recipes_create():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "name required"}), 400
    recipe = create_recipe(
        name=data["name"],
        servings=data.get("servings", 1),
        notes=data.get("notes", ""),
        image_url=data.get("image_url"),
    )
    return jsonify(recipe), 201


@app.route("/api/recipes/<int:recipe_id>", methods=["GET"])
def api_recipe_get(recipe_id):
    recipe = get_recipe(recipe_id)
    if not recipe:
        return jsonify({"error": "not found"}), 404

    return jsonify(recipe)


@app.route("/api/recipes/<int:recipe_id>", methods=["PUT"])
def api_recipe_update(recipe_id):
    data = request.get_json() or {}
    recipe = update_recipe(
        recipe_id,
        name=data.get("name"),
        servings=data.get("servings"),
        notes=data.get("notes"),
        image_url=data.get("image_url"),
    )
    if not recipe:
        return jsonify({"error": "not found"}), 404
    return jsonify(recipe)


@app.route("/api/recipes/<int:recipe_id>", methods=["DELETE"])
def api_recipe_delete(recipe_id):
    if delete_recipe(recipe_id):
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404


@app.route("/api/recipes/<int:recipe_id>/ingredients", methods=["POST"])
def api_ingredient_add(recipe_id):
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "name required"}), 400
    ing = add_ingredient(
        recipe_id,
        name=data["name"],
        quantity=data.get("quantity"),
        unit=data.get("unit"),
    )
    if not ing:
        return jsonify({"error": "recipe not found"}), 404
    return jsonify(ing), 201


@app.route("/api/ingredients/<int:ingredient_id>", methods=["PUT"])
def api_ingredient_update(ingredient_id):
    data = request.get_json() or {}
    ing = update_ingredient(ingredient_id, **data)
    if not ing:
        return jsonify({"error": "not found"}), 404

    # If quantity or unit changed, recompute costs for all linked products
    if "quantity" in data or "unit" in data:
        products = get_ingredient_products(ingredient_id)
        updated_products = []
        for ip in products:
            if not ing["quantity"] or not ing["unit"]:
                updated_products.append(ip)
                continue
            std_units = json.loads(ip["std_units_json"]) if ip.get("std_units_json") else {}
            # Rebuild minimal product dict for cost computation
            product_dict = {
                "name": ip.get("product_name"),
                "price": ip.get("product_price"),
                "size": ip.get("product_size"),
                "store": ip.get("product_store"),
                "std_units": std_units,
            }
            density = ip.get("density_oz_per_cup")
            cost = compute_ingredient_cost(
                recipe_qty=ing["quantity"],
                recipe_unit=ing["unit"],
                product=product_dict,
                ingredient_name=ing["name"],
                density_override=density,
            )
            breakdown = format_ingredient_cost_breakdown(
                recipe_qty=ing["quantity"],
                recipe_unit=ing["unit"],
                product=product_dict,
                ingredient_name=ing["name"],
                density_override=density,
            )
            update_ingredient_product(ip["id"], ingredient_cost=cost, cost_breakdown=breakdown)
            ip["ingredient_cost"] = cost
            ip["cost_breakdown"] = breakdown
            updated_products.append(ip)

        # Update legacy columns on recipe_ingredients with cheapest
        costed = [p for p in updated_products if p.get("ingredient_cost") is not None]
        if costed:
            cheapest = min(costed, key=lambda p: p["ingredient_cost"])
            update_ingredient(ingredient_id, ingredient_cost=cheapest["ingredient_cost"],
                              cost_breakdown=cheapest["cost_breakdown"])
            ing["ingredient_cost"] = cheapest["ingredient_cost"]
            ing["cost_breakdown"] = cheapest["cost_breakdown"]

        ing["products"] = updated_products

    return jsonify(ing)


@app.route("/api/ingredients/<int:ingredient_id>", methods=["DELETE"])
def api_ingredient_delete(ingredient_id):
    if delete_ingredient(ingredient_id):
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404


@app.route("/api/ingredients/<int:ingredient_id>/link-product", methods=["POST"])
def api_ingredient_link_product(ingredient_id):
    """Link a product to an ingredient and compute the cost.

    Expects JSON with a product dict (from search results) that has std_units.
    Inserts into ingredient_products table (supports multiple products per ingredient).
    """
    data = request.get_json()
    if not data or not data.get("product"):
        return jsonify({"error": "product required"}), 400

    product = data["product"]
    ing = update_ingredient(ingredient_id)
    if not ing:
        return jsonify({"error": "ingredient not found"}), 404

    # Parse serving size into density (oz per cup)
    density = None
    if product.get("serving_size"):
        density = parse_serving_size_density(product["serving_size"])

    # Compute cost using the cooking conversion layer
    cost = None
    breakdown = None
    if ing["quantity"] and ing["unit"]:
        cost = compute_ingredient_cost(
            recipe_qty=ing["quantity"],
            recipe_unit=ing["unit"],
            product=product,
            ingredient_name=ing["name"],
            density_override=density,
        )
        breakdown = format_ingredient_cost_breakdown(
            recipe_qty=ing["quantity"],
            recipe_unit=ing["unit"],
            product=product,
            ingredient_name=ing["name"],
            density_override=density,
        )

    # Build URL from product
    url = product.get("url")
    if not url:
        store = product.get("store", "")
        pid = product.get("product_id", "")
        if store == "walmart" and pid:
            url = f"https://www.walmart.com/ip/{pid}"
        elif store == "aldi" and pid:
            url = f"https://new.aldi.us/product/{pid}"
        elif store == "amazon" and pid:
            url = f"https://www.amazon.com/dp/{pid}"

    # Insert into ingredient_products table
    std_units_json = json.dumps(product.get("std_units", {}))
    new_product = add_ingredient_product(
        ingredient_id,
        product_url=url,
        product_name=product.get("name"),
        product_price=product.get("price"),
        product_size=product.get("size"),
        product_store=product.get("store"),
        product_unit_price=product.get("unit_price_string"),
        ingredient_cost=cost,
        cost_breakdown=breakdown,
        density_oz_per_cup=density,
        std_units_json=std_units_json,
    )

    # Also update the legacy columns on the ingredient for backward compat
    update_ingredient(
        ingredient_id,
        product_url=url,
        product_name=product.get("name"),
        product_price=product.get("price"),
        product_size=product.get("size"),
        product_store=product.get("store"),
        product_unit_price=product.get("unit_price_string"),
        ingredient_cost=cost,
        cost_breakdown=breakdown,
    )

    return jsonify(new_product), 201


@app.route("/api/ingredient-products/<int:product_id>", methods=["DELETE"])
def api_ingredient_product_delete(product_id):
    if delete_ingredient_product(product_id):
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404


@app.route("/api/recipes/<int:recipe_id>/recalculate", methods=["POST"])
def api_recipe_recalculate(recipe_id):
    """Recalculate all ingredient costs for a recipe.

    For ingredients with linked products, re-fetches current prices and recomputes costs.
    """
    recipe = get_recipe(recipe_id)
    if not recipe:
        return jsonify({"error": "not found"}), 404

    zip_code = request.args.get("zip", DEFAULT_ZIP).strip() or DEFAULT_ZIP

    for ing in recipe["ingredients"]:
        if not ing["quantity"] or not ing["unit"]:
            continue

        # Recalculate all linked products for this ingredient
        for ip in ing.get("products", []):
            url = ip.get("product_url")
            if not url:
                continue

            product_dict = None

            # Fetch fresh product data
            if "walmart.com" in url:
                pid = walmart_extract_id(url)
                if pid:
                    try:
                        p = walmart_scrape_product(pid, zip_code)
                        if not p.error:
                            product_dict = {
                                "name": p.name, "price": p.price,
                                "unit_price_string": p.unit_price_string,
                                "store": "walmart", "size": None, "url": p.url,
                                "serving_size": p.serving_size,
                            }
                    except Exception as e:
                        log.error(f"Recipe recalc - Walmart fetch error for {pid}: {e}")
            elif "aldi.us" in url:
                pid = aldi_extract_id(url)
                if pid:
                    try:
                        session = get_aldi_session()
                        products = aldi_scrape_products([pid], postal_code=zip_code, session=session)
                        if products and not products[0].error:
                            p = products[0]
                            product_dict = {
                                **asdict(p), "store": "aldi",
                            }
                    except Exception as e:
                        log.error(f"Recipe recalc - Aldi fetch error for {pid}: {e}")
            elif "amazon.com" in url:
                pid = amazon_extract_id(url)
                if pid:
                    try:
                        p = amazon_scrape_product(pid, zip_code)
                        if not p.error:
                            product_dict = {
                                "name": p.name, "price": p.price,
                                "unit_price_string": p.unit_price_string,
                                "store": "amazon", "size": p.size, "url": p.url,
                                "serving_size": p.serving_size,
                            }
                    except Exception as e:
                        log.error(f"Recipe recalc - Amazon fetch error for {pid}: {e}")

            if product_dict:
                # Standardize to get std_units
                all_prods = [product_dict]
                standardize_unit_prices(ing["name"], all_prods)

                # Extract density from serving size
                density = None
                if product_dict.get("serving_size"):
                    density = parse_serving_size_density(product_dict["serving_size"])

                cost = compute_ingredient_cost(
                    recipe_qty=ing["quantity"],
                    recipe_unit=ing["unit"],
                    product=product_dict,
                    ingredient_name=ing["name"],
                    density_override=density,
                )
                breakdown = format_ingredient_cost_breakdown(
                    recipe_qty=ing["quantity"],
                    recipe_unit=ing["unit"],
                    product=product_dict,
                    ingredient_name=ing["name"],
                    density_override=density,
                )

                # Update the ingredient_product row
                conn = __import__('database').get_connection()
                conn.execute(
                    "UPDATE ingredient_products SET product_name=?, product_price=?, "
                    "product_unit_price=?, ingredient_cost=?, cost_breakdown=?, density_oz_per_cup=? WHERE id=?",
                    (product_dict.get("name"), product_dict.get("price"),
                     product_dict.get("unit_price_string"), cost, breakdown, density, ip["id"]),
                )
                conn.commit()
                conn.close()

    return jsonify(get_recipe(recipe_id))


@app.route("/api/cooking-units", methods=["GET"])
def api_cooking_units():
    return jsonify({"units": COOKING_UNITS})


init_db()

if __name__ == "__main__":
    import webbrowser

    print("\n" + "=" * 50)
    print("  Grocery Price Comparer")
    print("  Opening http://localhost:5000")
    print("=" * 50 + "\n")
    webbrowser.open("http://localhost:5000")
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
