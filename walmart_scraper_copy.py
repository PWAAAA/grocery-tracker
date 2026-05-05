"""
Walmart Grocery Price Scraper (No Selenium)
============================================
Extracts product prices from Walmart's __NEXT_DATA__ JSON blob
using plain HTTP requests. No headless browser needed.

Usage:
    # Scrape known products by ID
    python walmart_scraper.py

    # Find product IDs by search query
    python walmart_scraper.py --find "great value whole milk gallon"

    # Scrape specific products by ID
    python walmart_scraper.py --ids 10450114 827268180

    # Extract product ID from a Walmart URL
    python walmart_scraper.py --url "https://www.walmart.com/ip/Great-Value-Milk/10450114"

    # Specify zip code
    python walmart_scraper.py --zip 34747

Dependencies:
    pip install requests beautifulsoup4
    Recommended: pip install curl_cffi  (much better anti-bot evasion, especially for search)
"""

import json
import time
import random
import logging
import argparse
import re
from dataclasses import dataclass, asdict
from typing import Optional
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# --- Try to use curl_cffi for better TLS fingerprinting ---
# Falls back to plain requests if not installed.
# curl_cffi is STRONGLY recommended for search pages.
try:
    import curl_cffi.requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ============================================================
# Configuration
# ============================================================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# Delay range between requests (seconds)
MIN_DELAY = 2.0
MAX_DELAY = 4.0

# Retry config for search pages (they get CAPTCHAd more aggressively)
MAX_RETRIES = 3
BACKOFF_BASE = 3  # seconds — exponential: 3, 6, 12


# ============================================================
# Data model
# ============================================================

@dataclass
class ProductResult:
    name: str
    product_id: str
    price: Optional[float]
    price_string: Optional[str]
    unit_price_string: Optional[str]
    currency: str
    in_stock: bool
    on_sale: bool
    store_id: Optional[str]
    url: str
    brand: Optional[str] = None
    image_url: Optional[str] = None
    error: Optional[str] = None


# ============================================================
# HTTP fetching
# ============================================================

def _build_headers(referer: Optional[str] = None) -> dict:
    """
    Build request headers that mimic a real browser.
    For search pages, we set a Google referer to simulate arriving
    from a search engine, which Walmart treats more leniently.
    """
    ua = random.choice(USER_AGENTS)
    is_firefox = "Firefox" in ua

    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }

    # Sec- headers differ between Chrome and Firefox
    if not is_firefox:
        headers.update({
            "Sec-Ch-Ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="8"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site" if referer else "none",
            "Sec-Fetch-User": "?1",
        })

    if referer:
        headers["Referer"] = referer

    return headers


def _build_cookies(zip_code: str, store_id: Optional[str] = None) -> dict:
    """
    Set location cookies so Walmart returns prices for your zip code.
    This replaces the flaky Selenium zip-code modal interaction.
    """
    cookies = {
        "location-data": json.dumps({
            "postalCode": zip_code,
            "stateOrProvinceCode": "",
            "city": "",
            "isZipLocated": True,
        }),
    }
    if store_id:
        cookies["walmart.nearestStoreId"] = store_id
    return cookies


def _is_captcha(html: str) -> bool:
    """Check if the response is a CAPTCHA/bot challenge page."""
    lower = html[:3000].lower()
    indicators = [
        "robot or human",
        "captcha",
        "are you a human",
        "verify you are human",
        "press & hold",
        "blocked",
    ]
    return any(ind in lower for ind in indicators)


def fetch_page(
    url: str,
    zip_code: str,
    store_id: Optional[str] = None,
    referer: Optional[str] = None,
    max_retries: int = 1,
) -> Optional[str]:
    """
    Fetch a Walmart page's HTML with retry + exponential backoff.

    Uses curl_cffi if available (mimics Chrome TLS fingerprint),
    otherwise falls back to plain requests.

    Args:
        url:          Full Walmart URL.
        zip_code:     Zip code for pricing.
        store_id:     Optional store ID.
        referer:      Optional referer URL (helps with search pages).
        max_retries:  Number of retry attempts on CAPTCHA (default 1 for
                      product pages, higher for search).
    """
    cookies = _build_cookies(zip_code, store_id)

    for attempt in range(max_retries):
        try:
            if HAS_CFFI:
                # IMPORTANT: When using curl_cffi's impersonate mode, it
                # sets its own User-Agent, Accept, Sec-* headers etc. that
                # match the TLS fingerprint. Overriding them creates a
                # mismatch that anti-bot systems detect. Only pass minimal
                # extras that don't conflict with the impersonation.
                cffi_headers = {}
                if referer:
                    cffi_headers["Referer"] = referer

                resp = cffi_requests.get(
                    url,
                    headers=cffi_headers,
                    cookies=cookies,
                    impersonate="chrome",
                    timeout=15,
                )
            else:
                headers = _build_headers(referer=referer)
                resp = requests.get(
                    url,
                    headers=headers,
                    cookies=cookies,
                    timeout=15,
                )

            if resp.status_code != 200:
                log.warning(f"HTTP {resp.status_code} for {url}")
                return None

            if _is_captcha(resp.text):
                if attempt < max_retries - 1:
                    wait = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 2)
                    log.warning(
                        f"CAPTCHA on attempt {attempt + 1}/{max_retries} — "
                        f"retrying in {wait:.1f}s..."
                    )
                    time.sleep(wait)
                    continue
                else:
                    lib = "curl_cffi" if HAS_CFFI else "requests"
                    log.warning(
                        f"CAPTCHA after {max_retries} attempts for {url} "
                        f"(using {lib})"
                    )
                    if not HAS_CFFI:
                        log.warning(
                            "TIP: Install curl_cffi for much better results: "
                            "pip install curl_cffi"
                        )
                    return None

            return resp.text

        except Exception as e:
            log.error(f"Request failed for {url}: {e}")
            if attempt < max_retries - 1:
                time.sleep(BACKOFF_BASE * (2 ** attempt))
                continue
            return None

    return None


# ============================================================
# Parsing __NEXT_DATA__
# ============================================================

def extract_next_data(html: str) -> Optional[dict]:
    """Pull the __NEXT_DATA__ JSON blob from the page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script or not script.string:
        log.warning("No __NEXT_DATA__ script tag found")
        return None
    try:
        return json.loads(script.string)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse __NEXT_DATA__ JSON: {e}")
        return None


def parse_product_page(data: dict, product_id: str) -> ProductResult:
    """
    Extract price and product info from a single product page's
    __NEXT_DATA__ blob.

    Path: props.pageProps.initialData.data.product
    """
    try:
        product = data["props"]["pageProps"]["initialData"]["data"]["product"]

        # --- Price ---
        price_info = product.get("priceInfo", {})
        current = price_info.get("currentPrice", {})
        price = current.get("price")
        price_string = current.get("priceString")
        currency = current.get("currencyUnit", "USD")

        unit_price_node = price_info.get("unitPrice", {})
        unit_price_string = unit_price_node.get("priceString") if unit_price_node else None

        on_sale = price_info.get("isPriceReduced", False)

        # --- Availability ---
        availability = product.get("availabilityStatus", "")
        in_stock = availability.upper() in ("IN_STOCK", "AVAILABLE")

        # --- Basics ---
        name = product.get("name", "Unknown")
        brand = product.get("brand", None)
        canonical_url = product.get("canonicalUrl", "")
        url = f"https://www.walmart.com{canonical_url}" if canonical_url else ""

        # --- Image ---
        image_info = product.get("imageInfo", {})
        thumbnail = image_info.get("thumbnailUrl", None)

        # --- Store ---
        store_node = (
            data.get("props", {})
            .get("pageProps", {})
            .get("initialData", {})
            .get("data", {})
            .get("contentLayout", {})
            .get("storeInfo", {})
        )
        store_id = store_node.get("storeId") if store_node else None

        return ProductResult(
            name=name,
            product_id=product_id,
            price=price,
            price_string=price_string,
            unit_price_string=unit_price_string,
            currency=currency,
            in_stock=in_stock,
            on_sale=on_sale,
            store_id=store_id,
            url=url,
            brand=brand,
            image_url=thumbnail,
        )

    except (KeyError, TypeError) as e:
        log.error(f"Failed to parse product {product_id}: {e}")
        return ProductResult(
            name="PARSE_ERROR",
            product_id=product_id,
            price=None,
            price_string=None,
            unit_price_string=None,
            currency="USD",
            in_stock=False,
            on_sale=False,
            store_id=None,
            url="",
            error=str(e),
        )


def parse_search_results(data: dict) -> list[dict]:
    """
    Extract product summaries from a Walmart search page's
    __NEXT_DATA__ blob.

    Path: props.pageProps.initialData.searchResult.itemStacks[].items
    """
    results = []
    try:
        stacks = data["props"]["pageProps"]["initialData"]["searchResult"]["itemStacks"]
    except (KeyError, TypeError) as e:
        log.error(f"Failed to locate itemStacks in search data: {e}")
        return results

    for stack in stacks:
        if not isinstance(stack, dict):
            continue
        for item in stack.get("items", []):
            try:
                if not isinstance(item, dict):
                    continue
                if item.get("__typename") != "Product":
                    continue
                pid = item.get("usItemId", "")
                price_info = item.get("priceInfo")
                if not isinstance(price_info, dict):
                    price_info = {}
                unit_price_string = price_info.get("unitPrice") or None
                if not isinstance(unit_price_string, str):
                    unit_price_string = None
                results.append({
                    "name": item.get("name"),
                    "product_id": pid,
                    "price": item.get("price"),
                    "unit_price_string": unit_price_string,
                    "rating": item.get("averageRating"),
                    "image": item.get("image"),
                    "url": f"https://www.walmart.com/ip/{pid}",
                    "sponsored": item.get("isSponsoredFlag", False),
                })
            except Exception as e:
                log.warning(f"Skipping malformed search item: {e}")

    return results


# ============================================================
# Public API
# ============================================================

def scrape_product(
    product_id: str,
    zip_code: str = "32801",
    store_id: Optional[str] = None,
) -> ProductResult:
    """
    Scrape a single Walmart product page by product ID.

    Args:
        product_id: The numeric Walmart product ID from the URL.
                    e.g., walmart.com/ip/Some-Product-Name/10450114
                    -> product_id = "10450114"
        zip_code:   Zip code for location-specific pricing.
        store_id:   Optional Walmart store ID for exact store pricing.

    Returns:
        ProductResult dataclass with price and product info.
    """
    url = f"https://www.walmart.com/ip/{product_id}"
    log.info(f"Scraping product {product_id} (zip: {zip_code})")

    html = fetch_page(url, zip_code, store_id, max_retries=1)
    if html is None:
        return ProductResult(
            name="FETCH_ERROR",
            product_id=product_id,
            price=None,
            price_string=None,
            unit_price_string=None,
            currency="USD",
            in_stock=False,
            on_sale=False,
            store_id=store_id,
            url=url,
            error="Failed to fetch page",
        )

    data = extract_next_data(html)
    if data is None:
        return ProductResult(
            name="PARSE_ERROR",
            product_id=product_id,
            price=None,
            price_string=None,
            unit_price_string=None,
            currency="USD",
            in_stock=False,
            on_sale=False,
            store_id=store_id,
            url=url,
            error="No __NEXT_DATA__ found",
        )

    return parse_product_page(data, product_id)


def scrape_search(
    query: str,
    zip_code: str = "32801",
    store_id: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
) -> list[dict]:
    """
    Scrape Walmart search results for a query.

    Search pages are more aggressively protected than product pages,
    so this uses:
      - A Google referer to simulate arriving from search
      - Exponential backoff retry on CAPTCHA
      - curl_cffi TLS impersonation (if installed)

    Args:
        query:       Search term (e.g., "whole milk gallon").
        zip_code:    Zip code for location-specific pricing.
        store_id:    Optional store ID.
        max_retries: Retry attempts on CAPTCHA (default 3).

    Returns:
        List of product summary dicts with name, product_id, price, url.
    """
    url = f"https://www.walmart.com/search?q={quote_plus(query)}"
    log.info(f"Searching Walmart for '{query}' (zip: {zip_code})")

    # Simulate arriving from Google — Walmart is less aggressive with
    # requests that have a search engine referer
    referer = f"https://www.google.com/search?q={quote_plus(query + ' walmart')}"

    html = fetch_page(
        url, zip_code, store_id,
        referer=referer,
        max_retries=max_retries,
    )
    if html is None:
        return []

    data = extract_next_data(html)
    if data is None:
        return []

    return parse_search_results(data)


def scrape_product_list(
    product_ids: list[str],
    zip_code: str = "32801",
    store_id: Optional[str] = None,
) -> list[ProductResult]:
    """
    Scrape multiple products with polite delays between requests.
    """
    results = []
    for i, pid in enumerate(product_ids):
        result = scrape_product(pid, zip_code, store_id)
        results.append(result)
        if i < len(product_ids) - 1:
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            log.info(f"Waiting {delay:.1f}s before next request...")
            time.sleep(delay)
    return results


# ============================================================
# Utility: extract product ID from a Walmart URL
# ============================================================

def extract_id_from_url(url: str) -> Optional[str]:
    """
    Pull the product ID from a Walmart URL.

    Handles:
        https://www.walmart.com/ip/Product-Name-Here/10450114
        https://www.walmart.com/ip/10450114
        walmart.com/ip/Whatever/10450114?some=param
    """
    match = re.search(r'/ip/(?:[^/]+/)?(\d+)', url)
    return match.group(1) if match else None


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Walmart Grocery Price Scraper (No Selenium)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scrape default product list
  python walmart_scraper.py

  # Find product IDs for a grocery item
  python walmart_scraper.py --find "great value whole milk gallon"

  # Scrape specific products by ID
  python walmart_scraper.py --ids 10450114 827268180 123456789

  # Extract product ID from a URL you copied
  python walmart_scraper.py --url "https://www.walmart.com/ip/Great-Value-Milk/10450114"

  # Change zip code
  python walmart_scraper.py --zip 34747
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
            # Delay between searches (not before the first one)
            if qi > 0:
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                log.info(f"Waiting {delay:.1f}s before next search...")
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
                pos = len(all_search_results) + 1  # running 1-based position across all searches
                price_str = f"${item['price']:.2f}" if item["price"] else "N/A"
                name = (item["name"] or "Unknown")[:45]
                pid = item["product_id"] or "???"
                sponsored = " [AD]" if item.get("sponsored") else ""
                print(f"  {pos:<5} {pid:<15} {price_str:>8}  {name}{sponsored}")
                all_search_results.append(item)

            print()

        if not all_search_results:
            return

        # Save all search results for reference
        search_out = Path("walmart_search_results.json")
        with open(search_out, "w") as f:
            json.dump(all_search_results, f, indent=2)
        print(f"  All results saved to {search_out}\n")

        # Prompt user to select products to scrape
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
            # Treat as a list position if the number falls within the result count
            if num is not None and 1 <= num <= len(all_search_results):
                pid = all_search_results[num - 1]["product_id"]
                if pid and pid not in selected_ids:
                    selected_ids.append(pid)
            # Otherwise treat as a literal product ID
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
        # ---- YOUR TRACKED PRODUCTS GO HERE ----
        # Find IDs with:  python walmart_scraper.py --find "product name"
        # Or from URL:    walmart.com/ip/Product-Name/THIS_NUMBER
        # Or paste URL:   python walmart_scraper.py --url "https://walmart.com/ip/..."
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

    # Save results
    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"\n  Results saved to {output_path}")


if __name__ == "__main__":
    main()
