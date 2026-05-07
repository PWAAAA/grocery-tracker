"""
Aldi scraper CLI — standalone command-line interface.

Usage:
    python -m scrapers.aldi.cli --find "whole milk"
    python -m scrapers.aldi.cli --ids 20986614 18649227 16902710
    python -m scrapers.aldi.cli --url "https://www.aldi.us/store/aldi/products/16902710-friendly-farms-vitamin-d-milk-1-gal"
    python -m scrapers.aldi.cli --zip 32825 --store 518104 --zone 178
"""

import json
import logging
import argparse
from dataclasses import asdict
from pathlib import Path

from .config import DEFAULT_SHOP_ID, DEFAULT_ZONE_ID, DEFAULT_ZIP, DEFAULT_SEARCH_LIMIT
from .api import find_products, scrape_products, extract_id_from_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    parser = argparse.ArgumentParser(
        description="Aldi Grocery Price Scraper (No Selenium)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Search for products by keyword
  python -m scrapers.aldi.cli --find "whole milk"
  python -m scrapers.aldi.cli --find "organic eggs" --limit 10

  # Scrape specific products
  python -m scrapers.aldi.cli --ids 20986614 18649227 16902710

  # Extract product ID from a URL
  python -m scrapers.aldi.cli --url "https://www.aldi.us/store/aldi/products/16902710-friendly-farms-vitamin-d-milk-1-gal"

  # Use a different zip/store
  python -m scrapers.aldi.cli --ids 20986614 --zip 34747 --store 518104 --zone 178

How to find product IDs:
  1. Go to aldi.us and find the product you want
  2. The ID is the number at the start of the URL path:
     /products/16902710-friendly-farms-...  ->  16902710

How to find your store ID and zone ID:
  1. Open aldi.us in Chrome, open DevTools (F12)
  2. Go to Network tab, filter by "graphql"
  3. Click any graphql request, check Payload tab
  4. Look for shopId and zoneId in the variables
        """,
    )
    parser.add_argument("--ids", nargs="+", type=str, help="Aldi product IDs to scrape")
    parser.add_argument("--url", type=str, help="Extract product ID from an Aldi URL")
    parser.add_argument("--find", type=str, help="Search Aldi for products by keyword")
    parser.add_argument("--limit", type=int, default=DEFAULT_SEARCH_LIMIT, help=f"Max search results (default: {DEFAULT_SEARCH_LIMIT})")
    parser.add_argument("--zip", type=str, default=DEFAULT_ZIP, help=f"Zip code (default: {DEFAULT_ZIP})")
    parser.add_argument("--store", type=str, default=DEFAULT_SHOP_ID, help=f"Store/shop ID (default: {DEFAULT_SHOP_ID})")
    parser.add_argument("--zone", type=str, default=DEFAULT_ZONE_ID, help=f"Zone ID (default: {DEFAULT_ZONE_ID})")
    parser.add_argument("--output", type=str, default="aldi_prices.json", help="Output JSON file")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  Aldi Grocery Price Scraper (No Selenium)")
    print(f"  Zip: {args.zip} | Store: {args.store} | Zone: {args.zone}")
    print("=" * 60 + "\n")

    # ---- Mode: Extract ID from URL ----
    if args.url:
        pid = extract_id_from_url(args.url)
        if pid:
            print(f"  Product ID: {pid}")
            print(f"  Add this to your tracked products list.")
        else:
            print(f"  Could not extract product ID from: {args.url}")
        return

    # ---- Mode: Search by keyword ----
    if args.find:
        results = find_products(
            args.find,
            shop_id=args.store,
            zone_id=args.zone,
            postal_code=args.zip,
            limit=args.limit,
        )

    # ---- Mode: Scrape product IDs ----
    else:
        product_ids = args.ids or [
            "16902710",   # Friendly Farms Vitamin D Milk, 1 gal
            "20986614",   # Friendly Farms 2% Milk, 0.5 gal
        ]

        results = scrape_products(
            product_ids,
            shop_id=args.store,
            zone_id=args.zone,
            postal_code=args.zip,
        )

    print(f"  {'Price':>8} | {'Size':>10} | {'Product':<40} | {'Location'}")
    print(f"  {'-'*8} | {'-'*10} | {'-'*40} | {'-'*15}")

    for r in results:
        if r.error:
            print(f"  {'ERROR':>8} | {'':>10} | {r.product_id:<40} | {r.error}")
        else:
            price = r.price_string or (f"${r.price:.2f}" if r.price else "N/A")
            size = (r.size or "")[:10]
            name = r.name[:40]
            loc = r.store_location or ""
            sale = " [SALE]" if r.on_sale else ""
            print(f"  {price:>8} | {size:>10} | {name:<40} | {loc}{sale}")

    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"\n  Results saved to {output_path}")


if __name__ == "__main__":
    main()
