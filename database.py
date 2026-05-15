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
    conn.close()
    return [dict(row) for row in rows]


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
