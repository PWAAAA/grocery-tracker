"""Dev-only: backfill synthetic price history for a believable demo.

A fresh database has at most one observation per product, so trend charts and
the price-drops view look empty. This script inserts back-dated, jittered
observations for products already in `price_history` (and a few canned demo
products if the table is empty) so the analytics UI has something to show.

NOT used by the app. Run manually:  python seed_price_history.py [--days N]
"""

import argparse
import random
from datetime import datetime, timedelta

from database import get_connection, init_db

DEMO_PRODUCTS = [
    # (store, product_id, name, brand, size, base_price)
    ("aldi", "DEMO-MILK", "Whole Milk 1 Gal", "Friendly Farms", "1 gal", 3.29),
    ("walmart", "DEMO-EGGS", "Large Eggs 12 ct", "Great Value", "12 ct", 2.84),
    ("amazon", "DEMO-RICE", "Jasmine Rice 5 lb", "Iberia", "5 lb", 7.49),
    ("publix", "DEMO-BUTTER", "Unsalted Butter 1 lb", "Publix", "16 oz", 4.59),
]


def _existing_products(conn):
    rows = conn.execute(
        "SELECT product_key, store, product_id, name, brand, size, "
        "       AVG(price) AS base "
        "FROM price_history WHERE price IS NOT NULL "
        "GROUP BY product_key"
    ).fetchall()
    return [dict(r) for r in rows]


def seed(days: int):
    init_db()
    conn = get_connection()

    products = _existing_products(conn)
    if not products:
        print("No existing history — seeding canned demo products.")
        products = [
            {
                "product_key": f"{store}:{pid}",
                "store": store,
                "product_id": pid,
                "name": name,
                "brand": brand,
                "size": size,
                "base": base,
            }
            for (store, pid, name, brand, size, base) in DEMO_PRODUCTS
        ]

    now = datetime.now()
    inserted = 0
    for p in products:
        base = p["base"] or 3.0
        # One observation every 3-4 days back over the window, jittered +/-12%.
        day = days
        while day > 0:
            ts = now - timedelta(days=day, hours=random.randint(0, 12))
            price = round(base * random.uniform(0.88, 1.12), 2)
            conn.execute(
                "INSERT INTO price_history "
                "(product_key, store, product_id, name, brand, size, price, "
                " query, scraped_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    p["product_key"],
                    p["store"],
                    p["product_id"],
                    p["name"],
                    p.get("brand"),
                    p.get("size"),
                    price,
                    "seed",
                    ts.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            inserted += 1
            day -= random.randint(3, 4)
        # Make "today" a fresh low for at least some products so price-drops populates.
        if random.random() < 0.5:
            conn.execute(
                "INSERT INTO price_history "
                "(product_key, store, product_id, name, brand, size, price, "
                " query, scraped_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                (
                    p["product_key"],
                    p["store"],
                    p["product_id"],
                    p["name"],
                    p.get("brand"),
                    p.get("size"),
                    round(base * 0.82, 2),
                    "seed",
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()
    print(
        f"Seeded {inserted} observations across {len(products)} products over {days} days."
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="history window to backfill")
    seed(ap.parse_args().days)
