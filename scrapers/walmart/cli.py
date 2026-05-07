"""
Walmart scraper CLI — standalone command-line interface.

Usage:
    python -m scrapers.walmart.cli --find "great value whole milk gallon"
    python -m scrapers.walmart.cli --ids 10450114 827268180
    python -m scrapers.walmart.cli --url "https://www.walmart.com/ip/Great-Value-Milk/10450114"
    python -m scrapers.walmart.cli --zip 34747
"""

import json
import re
import time
import random
import logging
import argparse
from dataclasses import asdict
from pathlib import Path

from .config import MIN_DELAY, MAX_DELAY
from .http import HAS_CFFI
from .api import scrape_search, scrape_product_list, extract_id_from_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    parser = argparse.ArgumentParser(
        description="Walmart Grocery Price Scraper (No Selenium)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scrape default product list
  python -m scrapers.walmart.cli

  # Find product IDs for a grocery item
  python -m scrapers.walmart.cli --find "great value whole milk gallon"

  # Scrape specific products by ID
  python -m scrapers.walmart.cli --ids 10450114 827268180 123456789

  # Extract product ID from a URL you copied
  python -m scrapers.walmart.cli --url "https://www.walmart.com/ip/Great-Value-Milk/10450114"

  # Change zip code
  python -m scrapers.walmart.cli --zip 34747
        """,
    )
    parser.add_argument("--find", nargs="+", type=str, help='Search queries to find product IDs (each in quotes)')
    parser.add_argument("--ids", nargs="+", type=str, help="Product IDs to scrape")
    parser.add_argument("--url", type=str, help="Extract product ID from a Walmart URL")
    parser.add_argument("--zip", type=str, default="32801", help="Zip code (default: 32801 Orlando)")
    parser.add_argument("--store", type=str, default=None, help="Walmart store ID for exact store pricing")
    parser.add_argument("--output", type=str, default="walmart_prices.json", help="Output JSON file")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  Walmart Grocery Price Scraper (No Selenium)")
    engine = "curl_cffi (TLS impersonation)" if HAS_CFFI else "plain requests"
    print(f"  Engine: {engine}")
    print(f"  Zip: {args.zip}")
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

    # ---- Mode: Find product IDs via search ----
    if args.find:
        if not HAS_CFFI:
            print("  WARNING: Search works best with curl_cffi installed.")
            print("  Run: pip install curl_cffi\n")

        all_search_results = []

        for qi, query in enumerate(args.find):
            if qi > 0:
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                time.sleep(delay)

            results = scrape_search(query, zip_code=args.zip, store_id=args.store)

            print(f"  --- Results for: '{query}' ---\n")

            if not results:
                print("  No results found (likely CAPTCHA blocked).")
                if not HAS_CFFI:
                    print("  Try: pip install curl_cffi")
                else:
                    print("  Walmart may be rate-limiting. Wait a few minutes and retry.")
                print()
                continue

            print(f"  Found {len(results)} products:\n")
            print(f"  {'#':<5} {'ID':<15} {'Price':>8}  {'Name'}")
            print(f"  {'-'*5} {'-'*15} {'-'*8}  {'-'*45}")

            for item in results:
                pos = len(all_search_results) + 1
                price_str = f"${item['price']:.2f}" if item["price"] else "N/A"
                name = (item["name"] or "Unknown")[:45]
                pid = item["product_id"] or "???"
                sponsored = " [AD]" if item.get("sponsored") else ""
                print(f"  {pos:<5} {pid:<15} {price_str:>8}  {name}{sponsored}")
                all_search_results.append(item)

            print()

        if not all_search_results:
            return

        search_out = Path("walmart_search_results.json")
        with open(search_out, "w") as f:
            json.dump(all_search_results, f, indent=2)
        print(f"  All results saved to {search_out}\n")

        print("  Enter the list positions (#) or product IDs of the items to scrape.")
        print("  Separate multiple entries with spaces or commas.")
        print("  Example: 1 3 5   or   10450114 827268180   or   1, 10450114\n")

        raw = input("  Your selection: ").strip()
        if not raw:
            print("  No selection made. Exiting.")
            return

        tokens = re.split(r'[\s,]+', raw)
        product_id_set = {item["product_id"] for item in all_search_results}
        selected_ids = []

        for token in tokens:
            token = token.strip()
            if not token:
                continue
            num = int(token) if token.isdigit() else None
            if num is not None and 1 <= num <= len(all_search_results):
                pid = all_search_results[num - 1]["product_id"]
                if pid and pid not in selected_ids:
                    selected_ids.append(pid)
            elif token in product_id_set:
                if token not in selected_ids:
                    selected_ids.append(token)
            else:
                print(f"  WARNING: '{token}' is not a valid position or product ID — skipping.")

        if not selected_ids:
            print("  No valid products selected. Exiting.")
            return

        print(f"\n  Scraping {len(selected_ids)} product(s): {', '.join(selected_ids)}\n")

        scrape_results = scrape_product_list(selected_ids, zip_code=args.zip, store_id=args.store)

        print(f"\n  {'Price':>8} | {'Product':<50} | {'Status'}")
        print(f"  {'-'*8} | {'-'*50} | {'-'*12}")

        for r in scrape_results:
            if r.error:
                print(f"  {'ERROR':>8} | {r.product_id:<50} | {r.error}")
            else:
                stock = "In Stock" if r.in_stock else "Out of Stock"
                print(f"  {r.price_string or 'N/A':>8} | {r.name[:50]:<50} | {stock}")

        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump([asdict(r) for r in scrape_results], f, indent=2)
        print(f"\n  Results saved to {output_path}")
        return

    # ---- Mode: Scrape known product IDs ----
    product_ids = args.ids or [
        "10450114",   # Great Value Whole Milk, 1 Gallon (confirmed working)
    ]

    results = scrape_product_list(product_ids, zip_code=args.zip, store_id=args.store)

    print(f"\n  {'Price':>8} | {'Product':<50} | {'Status'}")
    print(f"  {'-'*8} | {'-'*50} | {'-'*12}")

    for r in results:
        if r.error:
            print(f"  {'ERROR':>8} | {r.product_id:<50} | {r.error}")
        else:
            stock = "In Stock" if r.in_stock else "Out of Stock"
            print(f"  {r.price_string or 'N/A':>8} | {r.name[:50]:<50} | {stock}")

    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"\n  Results saved to {output_path}")


if __name__ == "__main__":
    main()
