"""Price-history persistence and analytics.

Every standardized product seen during a search is logged to the `price_history`
table (see `database.init_db`). The analytics functions here are deliberately
SQL-forward — window functions (LAG, ROW_NUMBER, RANK), CTEs, and the
`latest_prices` view do the work rather than Python loops.

All queries target SQLite (>= 3.25 for window functions) but are written to be
portable to PostgreSQL.
"""

import json
import re

from database import get_connection


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def product_key(p: dict) -> str:
    """Stable dedupe key for a product across searches.

    Prefers the store's native product id; falls back to a name slug when the
    scraper didn't supply one (e.g. some Publix weekly-ad items).
    """
    store = p.get("store") or "unknown"
    pid = p.get("product_id")
    if pid:
        return f"{store}:{pid}"
    return f"{store}:name:{_slug(p.get('name'))}"


def _unit_price_label(p: dict, default_unit_key: str | None):
    """Pull the numeric unit price + a human label from a product's std_units."""
    std = p.get("std_units") or {}
    key = default_unit_key if default_unit_key in std else (next(iter(std), None))
    if not key:
        return None, None
    value = std[key].get("value")
    label = key[4:].replace("_", " ") if key.startswith("per_") else key
    return value, label


def record_observations(
    products: list[dict],
    query: str = None,
    zip_code: str = None,
    store_id: str = None,
    default_unit_key: str = None,
) -> int:
    """Append price observations for a batch of products.

    Dedup rule: skip a product if an identical price for that product_key was
    already recorded today — keeps repeated same-day searches from bloating the
    table while still capturing genuine intra-day price changes.

    Returns the number of rows inserted. Never raises on a bad product; the
    caller's search must not break because logging failed.
    """
    if not products:
        return 0

    conn = get_connection()
    try:
        # One round-trip for today's (key, price) pairs already on record.
        seen = {
            (row["product_key"], row["price"])
            for row in conn.execute(
                "SELECT product_key, price FROM price_history "
                "WHERE date(scraped_at) = date('now')"
            ).fetchall()
        }

        rows = []
        for p in products:
            price = p.get("price")
            if price is None:
                continue
            key = product_key(p)
            if (key, price) in seen:
                continue
            seen.add((key, price))
            unit_price, unit_label = _unit_price_label(p, default_unit_key)
            rows.append(
                (
                    key,
                    p.get("store"),
                    p.get("product_id"),
                    p.get("name"),
                    p.get("brand"),
                    p.get("size"),
                    price,
                    unit_price,
                    unit_label,
                    json.dumps(p.get("std_units") or {}),
                    p.get("url"),
                    p.get("image_url"),
                    query,
                    zip_code,
                    store_id,
                )
            )

        if rows:
            conn.executemany(
                "INSERT INTO price_history "
                "(product_key, store, product_id, name, brand, size, price, "
                "unit_price, unit_label, std_units_json, url, image_url, "
                "query, zip_code, store_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_product_history(store: str, product_id: str, days: int = 90) -> dict:
    """Time series + summary for one product, for the history modal.

    Uses LAG() to compute the change from the previous observation.
    """
    conn = get_connection()
    try:
        series = conn.execute(
            """
            SELECT scraped_at, price, unit_price, unit_label,
                   price - LAG(price) OVER (ORDER BY scraped_at) AS change_prev
            FROM price_history
            WHERE store = ? AND product_id = ?
              AND scraped_at >= datetime('now', ?)
            ORDER BY scraped_at
            """,
            (store, product_id, f"-{int(days)} days"),
        ).fetchall()

        summary = conn.execute(
            """
            SELECT MIN(price) AS min_price, MAX(price) AS max_price,
                   AVG(price) AS avg_price, COUNT(*) AS n_obs,
                   (SELECT price FROM price_history
                    WHERE store = ? AND product_id = ?
                    ORDER BY scraped_at DESC LIMIT 1) AS current_price,
                   (SELECT name FROM price_history
                    WHERE store = ? AND product_id = ?
                    ORDER BY scraped_at DESC LIMIT 1) AS name
            FROM price_history
            WHERE store = ? AND product_id = ?
              AND scraped_at >= datetime('now', ?)
            """,
            (
                store,
                product_id,
                store,
                product_id,
                store,
                product_id,
                f"-{int(days)} days",
            ),
        ).fetchone()

        return {
            "store": store,
            "product_id": product_id,
            "days": days,
            "series": [dict(r) for r in series],
            "summary": dict(summary) if summary else {},
        }
    finally:
        conn.close()


def get_price_drops(days: int = 30, limit: int = 50) -> list[dict]:
    """Products whose current price sits at (or within 0.1% of) their N-day low.

    CTE computes per-product window aggregates; the outer query keeps only the
    latest observation per product and filters to those at their low.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            WITH win AS (
                SELECT product_key, store, product_id, name, brand, size,
                       url, image_url, price, scraped_at,
                       MIN(price) OVER (PARTITION BY product_key) AS min_d,
                       MAX(price) OVER (PARTITION BY product_key) AS max_d,
                       AVG(price) OVER (PARTITION BY product_key) AS avg_d,
                       COUNT(*)   OVER (PARTITION BY product_key) AS n_obs,
                       ROW_NUMBER() OVER (
                           PARTITION BY product_key ORDER BY scraped_at DESC
                       ) AS rn
                FROM price_history
                WHERE scraped_at >= datetime('now', ?) AND price IS NOT NULL
            )
            SELECT product_key, store, product_id, name, brand, size, url,
                   image_url, price AS current_price, min_d, max_d, avg_d, n_obs
            FROM win
            WHERE rn = 1 AND n_obs >= 2 AND price <= min_d * 1.001
            ORDER BY (max_d - price) DESC
            LIMIT ?
            """,
            (f"-{int(days)} days", int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_cheapest_by_store(name: str) -> list[dict]:
    """Rank stores by current price for products whose name matches `name`.

    Reads the `latest_prices` view and ranks with RANK().
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT store, product_id, name, brand, size, price,
                   unit_price, unit_label, scraped_at,
                   RANK() OVER (ORDER BY price ASC) AS price_rank
            FROM latest_prices
            WHERE name LIKE ? AND price IS NOT NULL
            ORDER BY price ASC
            """,
            (f"%{name}%",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_badges_for_keys(product_keys: list[str], days: int = 30) -> dict:
    """Batched lookup of 30-day low/high/avg for a set of product keys.

    One query for the whole result set (no N+1). Returns a dict keyed by
    product_key with the current price's relationship to its window.
    """
    keys = [k for k in dict.fromkeys(product_keys) if k]
    if not keys:
        return {}

    placeholders = ",".join("?" for _ in keys)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            WITH win AS (
                SELECT product_key, price, scraped_at,
                       MIN(price) OVER (PARTITION BY product_key) AS min_d,
                       MAX(price) OVER (PARTITION BY product_key) AS max_d,
                       AVG(price) OVER (PARTITION BY product_key) AS avg_d,
                       COUNT(*)   OVER (PARTITION BY product_key) AS n_obs,
                       ROW_NUMBER() OVER (
                           PARTITION BY product_key ORDER BY scraped_at DESC
                       ) AS rn
                FROM price_history
                WHERE product_key IN ({placeholders})
                  AND scraped_at >= datetime('now', ?) AND price IS NOT NULL
            )
            SELECT product_key, price AS current_price, min_d, max_d, avg_d, n_obs
            FROM win WHERE rn = 1
            """,
            (*keys, f"-{int(days)} days"),
        ).fetchall()

        out = {}
        for r in rows:
            cur, lo = r["current_price"], r["min_d"]
            out[r["product_key"]] = {
                "low_30d": lo,
                "high_30d": r["max_d"],
                "avg_30d": r["avg_d"],
                "n_obs": r["n_obs"],
                "is_30d_low": r["n_obs"] >= 2
                and cur is not None
                and lo is not None
                and cur <= lo * 1.001,
            }
        return out
    finally:
        conn.close()
