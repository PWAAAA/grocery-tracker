"""SQLite database for persistent grocery list storage.

Supports two modes:
- Local (default): plain sqlite3 with a local grocery.db file
- Turso (deployed): set TURSO_URL and TURSO_TOKEN env vars to connect to a remote libSQL database
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "grocery.db")

TURSO_URL = os.environ.get("TURSO_URL")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN")


def get_connection():
    if TURSO_URL and TURSO_TOKEN:
        import libsql_experimental as libsql
        conn = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
        conn.row_factory = sqlite3.Row
        return conn

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS grocery_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('link', 'search')),
            store TEXT,
            position INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            servings INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            image_url TEXT,
            position INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recipe_ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            quantity REAL,
            unit TEXT,
            product_url TEXT,
            product_name TEXT,
            product_price REAL,
            product_size TEXT,
            product_store TEXT,
            product_unit_price TEXT,
            ingredient_cost REAL,
            cost_breakdown TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingredient_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingredient_id INTEGER NOT NULL,
            product_url TEXT,
            product_name TEXT,
            product_price REAL,
            product_size TEXT,
            product_store TEXT,
            product_unit_price TEXT,
            ingredient_cost REAL,
            cost_breakdown TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(ingredient_id) REFERENCES recipe_ingredients(id) ON DELETE CASCADE
        )
    """)

    # Migrations for existing databases
    try:
        conn.execute("ALTER TABLE recipes ADD COLUMN image_url TEXT")
    except Exception:
        pass  # column already exists

    try:
        conn.execute("ALTER TABLE ingredient_products ADD COLUMN density_oz_per_cup REAL")
    except Exception:
        pass  # column already exists

    try:
        conn.execute("ALTER TABLE ingredient_products ADD COLUMN std_units_json TEXT")
    except Exception:
        pass  # column already exists

    conn.execute("""
        CREATE TABLE IF NOT EXISTS publix_base_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            name TEXT NOT NULL,
            brand TEXT,
            size TEXT,
            base_price REAL NOT NULL,
            per_lb INTEGER NOT NULL DEFAULT 0,
            store_id TEXT,
            source TEXT,
            first_seen TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Index for fast lookups by name+brand (weekly ad items lack stable IDs)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_publix_bp_name_brand
        ON publix_base_prices (name, brand)
    """)
    # Index for lookups by product_id (TPR items with RIO-PCI-*)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_publix_bp_product_id
        ON publix_base_prices (product_id)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS publix_unknown_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            name TEXT NOT NULL,
            brand TEXT,
            size TEXT,
            sale_price REAL,
            deal_type TEXT,
            deal_text TEXT,
            reason TEXT NOT NULL,
            url TEXT,
            store_id TEXT,
            source TEXT,
            first_seen TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_publix_up_reason
        ON publix_unknown_prices (reason)
    """)

    # Migrate existing single-product data from recipe_ingredients to ingredient_products
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(recipe_ingredients)").fetchall()]
        if "product_url" in cols:
            rows = conn.execute(
                "SELECT id, product_url, product_name, product_price, product_size, "
                "product_store, product_unit_price, ingredient_cost, cost_breakdown "
                "FROM recipe_ingredients WHERE product_url IS NOT NULL"
            ).fetchall()
            for row in rows:
                existing = conn.execute(
                    "SELECT id FROM ingredient_products WHERE ingredient_id = ? AND product_url = ?",
                    (row[0], row[1])
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO ingredient_products "
                        "(ingredient_id, product_url, product_name, product_price, product_size, "
                        "product_store, product_unit_price, ingredient_cost, cost_breakdown, position) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8]),
                    )
    except Exception:
        pass

    conn.commit()
    conn.close()


def get_grocery_items() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT id, value, type, store FROM grocery_items ORDER BY position").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_grocery_items(items: list[dict]):
    """Replace all grocery items (full list save)."""
    conn = get_connection()
    conn.execute("DELETE FROM grocery_items")
    for i, item in enumerate(items):
        conn.execute(
            "INSERT INTO grocery_items (value, type, store, position) VALUES (?, ?, ?, ?)",
            (item["value"], item["type"], item.get("store"), i),
        )
    conn.commit()
    conn.close()



# ── Recipe CRUD ────────────────────────────────────────────────────

def get_recipes() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT id, name, servings, notes, image_url FROM recipes ORDER BY position").fetchall()
    recipes = []
    for row in rows:
        r = dict(row)
        costs = conn.execute(
            """SELECT ri.id,
                      MIN(ip.ingredient_cost) as cheapest_cost,
                      (SELECT ip2.product_price FROM ingredient_products ip2
                       WHERE ip2.ingredient_id = ri.id
                       ORDER BY ip2.ingredient_cost ASC NULLS LAST, ip2.position LIMIT 1) as shelf_price
               FROM recipe_ingredients ri
               LEFT JOIN ingredient_products ip ON ip.ingredient_id = ri.id
               WHERE ri.recipe_id = ?
               GROUP BY ri.id""",
            (r["id"],),
        ).fetchall()
        recipe_cost = sum(c["cheapest_cost"] for c in costs if c["cheapest_cost"] is not None)
        shelf_total = sum(c["shelf_price"] for c in costs if c["shelf_price"] is not None)
        r["recipe_cost"] = recipe_cost
        r["shelf_price"] = shelf_total
        r["cost_per_serving"] = recipe_cost / r["servings"] if r["servings"] and r["servings"] > 0 else 0
        recipes.append(r)
    conn.close()
    return recipes


def get_recipe(recipe_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT id, name, servings, notes, image_url FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        conn.close()
        return None
    recipe = dict(row)
    ingredients = conn.execute(
        "SELECT id, name, quantity, unit, product_url, product_name, product_price, "
        "product_size, product_store, product_unit_price, ingredient_cost, cost_breakdown, position "
        "FROM recipe_ingredients WHERE recipe_id = ? ORDER BY position",
        (recipe_id,),
    ).fetchall()
    ing_list = []
    for r in ingredients:
        ing = dict(r)
        products = conn.execute(
            "SELECT id, product_url, product_name, product_price, product_size, "
            "product_store, product_unit_price, ingredient_cost, cost_breakdown, density_oz_per_cup, std_units_json, position "
            "FROM ingredient_products WHERE ingredient_id = ? ORDER BY ingredient_cost ASC NULLS LAST, position",
            (ing["id"],),
        ).fetchall()
        ing["products"] = [dict(p) for p in products]
        ing_list.append(ing)
    recipe["ingredients"] = ing_list
    conn.close()
    return recipe


def create_recipe(name: str, servings: int = 1, notes: str = "", image_url: str = None) -> dict:
    conn = get_connection()
    max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) FROM recipes").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO recipes (name, servings, notes, image_url, position) VALUES (?, ?, ?, ?, ?)",
        (name, servings, notes, image_url, max_pos + 1),
    )
    recipe_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"id": recipe_id, "name": name, "servings": servings, "notes": notes, "image_url": image_url, "ingredients": []}


def update_recipe(recipe_id: int, name: str = None, servings: int = None, notes: str = None, image_url: str = None) -> dict | None:
    conn = get_connection()
    existing = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not existing:
        conn.close()
        return None
    if name is not None:
        conn.execute("UPDATE recipes SET name = ? WHERE id = ?", (name, recipe_id))
    if servings is not None:
        conn.execute("UPDATE recipes SET servings = ? WHERE id = ?", (servings, recipe_id))
    if notes is not None:
        conn.execute("UPDATE recipes SET notes = ? WHERE id = ?", (notes, recipe_id))
    if image_url is not None:
        conn.execute("UPDATE recipes SET image_url = ? WHERE id = ?", (image_url, recipe_id))
    conn.commit()
    conn.close()
    return get_recipe(recipe_id)


def delete_recipe(recipe_id: int) -> bool:
    conn = get_connection()
    conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
    cur = conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def add_ingredient(recipe_id: int, name: str, quantity: float = None, unit: str = None) -> dict | None:
    conn = get_connection()
    exists = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not exists:
        conn.close()
        return None
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM recipe_ingredients WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO recipe_ingredients (recipe_id, name, quantity, unit, position) VALUES (?, ?, ?, ?, ?)",
        (recipe_id, name, quantity, unit, max_pos + 1),
    )
    ing_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {
        "id": ing_id, "name": name, "quantity": quantity, "unit": unit,
        "product_url": None, "product_name": None, "product_price": None,
        "product_size": None, "product_store": None, "product_unit_price": None,
        "ingredient_cost": None, "cost_breakdown": None, "position": max_pos + 1,
        "products": [],
    }


def update_ingredient(ingredient_id: int, **fields) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT recipe_id FROM recipe_ingredients WHERE id = ?", (ingredient_id,)).fetchone()
    if not row:
        conn.close()
        return None
    allowed = {
        "name", "quantity", "unit", "product_url", "product_name", "product_price",
        "product_size", "product_store", "product_unit_price", "ingredient_cost",
        "cost_breakdown", "position",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE recipe_ingredients SET {set_clause} WHERE id = ?",
            (*updates.values(), ingredient_id),
        )
        conn.commit()
    result = conn.execute(
        "SELECT id, name, quantity, unit, product_url, product_name, product_price, "
        "product_size, product_store, product_unit_price, ingredient_cost, cost_breakdown, position "
        "FROM recipe_ingredients WHERE id = ?",
        (ingredient_id,),
    ).fetchone()
    conn.close()
    return dict(result) if result else None


def delete_ingredient(ingredient_id: int) -> bool:
    conn = get_connection()
    conn.execute("DELETE FROM ingredient_products WHERE ingredient_id = ?", (ingredient_id,))
    cur = conn.execute("DELETE FROM recipe_ingredients WHERE id = ?", (ingredient_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# ── Ingredient Products CRUD ──────────────────────────────────────

def get_ingredient_products(ingredient_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, ingredient_id, product_url, product_name, product_price, product_size, "
        "product_store, product_unit_price, ingredient_cost, cost_breakdown, density_oz_per_cup, std_units_json, position "
        "FROM ingredient_products WHERE ingredient_id = ? ORDER BY ingredient_cost ASC NULLS LAST, position",
        (ingredient_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_ingredient_product(ingredient_id: int, **fields) -> dict:
    conn = get_connection()
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM ingredient_products WHERE ingredient_id = ?",
        (ingredient_id,),
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO ingredient_products "
        "(ingredient_id, product_url, product_name, product_price, product_size, "
        "product_store, product_unit_price, ingredient_cost, cost_breakdown, density_oz_per_cup, std_units_json, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ingredient_id,
            fields.get("product_url"),
            fields.get("product_name"),
            fields.get("product_price"),
            fields.get("product_size"),
            fields.get("product_store"),
            fields.get("product_unit_price"),
            fields.get("ingredient_cost"),
            fields.get("cost_breakdown"),
            fields.get("density_oz_per_cup"),
            fields.get("std_units_json"),
            max_pos + 1,
        ),
    )
    product_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {
        "id": product_id, "ingredient_id": ingredient_id,
        "product_url": fields.get("product_url"),
        "product_name": fields.get("product_name"),
        "product_price": fields.get("product_price"),
        "product_size": fields.get("product_size"),
        "product_store": fields.get("product_store"),
        "product_unit_price": fields.get("product_unit_price"),
        "ingredient_cost": fields.get("ingredient_cost"),
        "cost_breakdown": fields.get("cost_breakdown"),
        "density_oz_per_cup": fields.get("density_oz_per_cup"),
        "std_units_json": fields.get("std_units_json"),
        "position": max_pos + 1,
    }


def update_ingredient_product(product_id: int, **fields) -> bool:
    allowed = {"ingredient_cost", "cost_breakdown", "density_oz_per_cup", "std_units_json"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    conn = get_connection()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE ingredient_products SET {set_clause} WHERE id = ?",
        (*updates.values(), product_id),
    )
    conn.commit()
    conn.close()
    return True


def delete_ingredient_product(product_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM ingredient_products WHERE id = ?", (product_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_cheapest_product(ingredient_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT id, ingredient_id, product_url, product_name, product_price, product_size, "
        "product_store, product_unit_price, ingredient_cost, cost_breakdown, density_oz_per_cup, std_units_json, position "
        "FROM ingredient_products WHERE ingredient_id = ? AND ingredient_cost IS NOT NULL "
        "ORDER BY ingredient_cost ASC LIMIT 1",
        (ingredient_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Publix Base Prices ────────────────────────────────────────────

def upsert_publix_base_price(name: str, base_price: float, brand: str = None,
                              size: str = None, product_id: str = None,
                              per_lb: bool = False, store_id: str = None,
                              source: str = None):
    """Store or update a known Publix base price.

    Uses product_id (RIO-PCI-*) as primary key when available,
    falls back to name+brand matching for weekly ad items.
    """
    conn = get_connection()

    existing = None
    if product_id:
        existing = conn.execute(
            "SELECT id FROM publix_base_prices WHERE product_id = ?",
            (product_id,),
        ).fetchone()

    if not existing and name:
        existing = conn.execute(
            "SELECT id FROM publix_base_prices WHERE name = ? AND brand IS ?",
            (name, brand),
        ).fetchone()

    if existing:
        conn.execute(
            "UPDATE publix_base_prices SET base_price = ?, size = ?, per_lb = ?, "
            "store_id = ?, source = ?, last_seen = datetime('now'), "
            "product_id = COALESCE(?, product_id) WHERE id = ?",
            (base_price, size, int(per_lb), store_id, source, product_id, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO publix_base_prices "
            "(product_id, name, brand, size, base_price, per_lb, store_id, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (product_id, name, brand, size, base_price, int(per_lb), store_id, source),
        )

    conn.commit()
    conn.close()


def store_publix_base_prices(products: list[dict], store_id: str = None):
    """Bulk-store base prices extracted from Publix deal results.

    Items with a computable base_price are stored in publix_base_prices.
    Items without are stored in publix_unknown_prices with a reason.
    """
    for p in products:
        bp = p.get("base_price")
        rio_pid = p.get("product_id") if p.get("product_id", "").startswith("RIO-") else None

        if bp is not None:
            per_lb = p.get("deal", {}).get("per_lb", False)
            if not per_lb and "/lb" in (p.get("price_string") or ""):
                per_lb = True
            upsert_publix_base_price(
                name=p.get("name"),
                base_price=bp,
                brand=p.get("brand"),
                size=p.get("size"),
                product_id=rio_pid,
                per_lb=per_lb,
                store_id=store_id,
                source=p.get("saving_type", "unknown"),
            )
        else:
            reason = _classify_missing_base_price(p)
            upsert_publix_unknown_price(
                name=p.get("name"),
                brand=p.get("brand"),
                size=p.get("size"),
                product_id=p.get("product_id"),  # store waId too for reference
                sale_price=p.get("price"),
                deal_type=p.get("deal", {}).get("deal_type"),
                deal_text=p.get("deal_text"),
                reason=reason,
                url=p.get("url"),
                store_id=store_id,
                source=p.get("saving_type", "unknown"),
            )


def upsert_publix_unknown_price(name: str, reason: str, brand: str = None,
                                 size: str = None, product_id: str = None,
                                 sale_price: float = None, deal_type: str = None,
                                 deal_text: str = None, url: str = None,
                                 store_id: str = None, source: str = None):
    """Store a Publix deal item whose base price could not be determined."""
    conn = get_connection()

    existing = None
    if product_id:
        existing = conn.execute(
            "SELECT id FROM publix_unknown_prices WHERE product_id = ?",
            (product_id,),
        ).fetchone()
    if not existing and name:
        existing = conn.execute(
            "SELECT id FROM publix_unknown_prices WHERE name = ? AND brand IS ?",
            (name, brand),
        ).fetchone()

    if existing:
        conn.execute(
            "UPDATE publix_unknown_prices SET sale_price = ?, deal_type = ?, "
            "deal_text = ?, reason = ?, url = ?, size = ?, store_id = ?, source = ?, "
            "last_seen = datetime('now'), product_id = COALESCE(?, product_id) WHERE id = ?",
            (sale_price, deal_type, deal_text, reason, url, size, store_id, source,
             product_id, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO publix_unknown_prices "
            "(product_id, name, brand, size, sale_price, deal_type, deal_text, "
            "reason, url, store_id, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (product_id, name, brand, size, sale_price, deal_type, deal_text,
             reason, url, store_id, source),
        )

    conn.commit()
    conn.close()


def get_publix_unknown_prices(reason: str = None) -> list[dict]:
    """Return stored Publix items with unknown base prices. Optional reason filter."""
    conn = get_connection()
    if reason:
        rows = conn.execute(
            "SELECT * FROM publix_unknown_prices WHERE reason = ? ORDER BY last_seen DESC",
            (reason,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM publix_unknown_prices ORDER BY reason, last_seen DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _classify_missing_base_price(product: dict) -> str:
    """Determine why a Publix deal item has no computable base price."""
    deal = product.get("deal", {})
    dt = deal.get("deal_type", "unknown")

    if dt == "dollars_off":
        return "dollars_off_only"
    if dt == "percent_off":
        return "percent_off_only"
    if dt == "other":
        return "unstructured_deal"
    if dt in ("sale_price", "multi_buy", "bogo"):
        return f"{dt}_no_savings_amount"
    return f"{dt}_unknown"


def get_publix_base_price(name: str = None, brand: str = None,
                           product_id: str = None) -> dict | None:
    """Look up a stored Publix base price."""
    conn = get_connection()
    row = None

    if product_id:
        row = conn.execute(
            "SELECT * FROM publix_base_prices WHERE product_id = ? "
            "ORDER BY last_seen DESC LIMIT 1",
            (product_id,),
        ).fetchone()

    if not row and name:
        row = conn.execute(
            "SELECT * FROM publix_base_prices WHERE name = ? AND brand IS ? "
            "ORDER BY last_seen DESC LIMIT 1",
            (name, brand),
        ).fetchone()

    conn.close()
    return dict(row) if row else None


def get_all_publix_base_prices() -> list[dict]:
    """Return all stored Publix base prices."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM publix_base_prices ORDER BY last_seen DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def classify_item(raw: str) -> dict:
    """Classify a raw string (URL or search term) into a grocery item dict."""
    if raw.startswith("http"):
        if "walmart.com" in raw:
            return {"type": "link", "store": "walmart", "value": raw}
        elif "aldi.us" in raw:
            return {"type": "link", "store": "aldi", "value": raw}
        else:
            return {"type": "link", "store": "unknown", "value": raw}
    return {"type": "search", "store": None, "value": raw}
