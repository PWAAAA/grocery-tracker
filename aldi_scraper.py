"""
Aldi Grocery Price Scraper (No Selenium)
=========================================
Queries Aldi's GraphQL API directly for product prices.
No headless browser needed.

Usage:
    # Scrape products by Aldi product ID
    python aldi_scraper.py --ids 20986614 18649227 18647971

    # Search for products by keyword
    python aldi_scraper.py --find "whole milk"

    # Specify zip code and store
    python aldi_scraper.py --ids 20986614 --zip 32825 --store 518104

    # Extract product ID from an Aldi URL
    python aldi_scraper.py --url "https://www.aldi.us/store/aldi/products/16902710-friendly-farms-vitamin-d-milk-1-gal"

Dependencies:
    pip install requests

Notes:
    - Product IDs are the numeric ID from the Aldi product URL.
      e.g., https://www.aldi.us/store/aldi/products/16902710-friendly-farms-...
      -> product_id = "16902710"
    - shopId and zoneId are store-specific. The defaults are for Orlando area.
      You can find yours by inspecting the GraphQL requests in DevTools.
    - The item prefix "items_23277" appears to be a regional/retailer constant.
      If it stops working, check DevTools for the current prefix.
"""

import json
import time
import random
import logging
import argparse
import re
import uuid
from dataclasses import dataclass, asdict
from typing import Optional
from pathlib import Path
from urllib.parse import urlencode, quote

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ============================================================
# Configuration — update these for your local store
# ============================================================

# Item ID prefix — appears to be constant for Aldi US region
# If scraping breaks, check DevTools for the current prefix
ITEM_PREFIX = "items_23277"

# Default store config (Orlando area)
DEFAULT_SHOP_ID = "518104"
DEFAULT_ZONE_ID = "178"
DEFAULT_ZIP = "32825"

# GraphQL endpoint
GRAPHQL_URL = "https://www.aldi.us/graphql"

# Persisted query hash for the "Items" operation
# This is a server-side cached query — Aldi uses Apollo's persisted
# queries so the client sends a hash instead of the full query text.
# If this hash becomes invalid, you'll get an error and need to
# grab the new hash from DevTools.
ITEMS_QUERY_HASH = "5116339819ff07f207fd38f949a8a7f58e52cc62223b535405b087e3076ebf2f"
SEARCH_QUERY_HASH = "6e6b53b10516829d9b7b9fae0cbc9b65bcbbc8792d77836f65b9db6a606057a7"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# Delay between requests
MIN_DELAY = 1.0
MAX_DELAY = 2.5

# Max items per GraphQL request (batching)
BATCH_SIZE = 10

# Default max results returned by a keyword search
DEFAULT_SEARCH_LIMIT = 24


# ============================================================
# Data model
# ============================================================

@dataclass
class AldiProduct:
    name: str
    product_id: str
    price: Optional[float]
    price_string: Optional[str]
    unit_price_string: Optional[str]
    size: Optional[str]
    brand: Optional[str]
    in_stock: bool
    on_sale: bool
    sale_disclaimer: Optional[str]
    store_location: Optional[str]
    url: str
    image_url: Optional[str] = None
    currency: str = "USD"
    error: Optional[str] = None


# ============================================================
# GraphQL API
# ============================================================

class AldiSession:
    """
    Manages a session with Aldi's Instacart-powered backend.

    Aldi's GraphQL API requires session cookies for authentication.
    This class visits the Aldi site first to establish a session,
    then reuses those cookies for all subsequent API calls.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._authenticated = False

    def _establish_session(self) -> bool:
        """
        Visit the Aldi site to pick up Instacart session cookies.
        These cookies authenticate subsequent GraphQL requests.
        """
        if self._authenticated:
            return True

        try:
            log.info("Establishing Aldi session (getting auth cookies)...")
            resp = self.session.get("https://www.aldi.us/", timeout=15)
            if resp.status_code != 200:
                log.error(f"Failed to load aldi.us: HTTP {resp.status_code}")
                return False

            # Check that we got the critical Instacart session cookie
            cookies = self.session.cookies.get_dict()
            has_session = any(
                "instacart" in k.lower() or "ic" in k.lower()
                for k in cookies
            )

            if has_session:
                log.info(f"Session established ({len(cookies)} cookies)")
                self._authenticated = True
                return True
            else:
                # Sometimes the session cookie comes from a redirect or
                # subsequent request — try hitting a product page too
                log.info("No Instacart cookie yet, trying a product page...")
                resp2 = self.session.get(
                    "https://www.aldi.us/store/aldi/",
                    timeout=15,
                )
                cookies = self.session.cookies.get_dict()
                log.info(f"After store page: {len(cookies)} cookies")
                self._authenticated = True
                return True

        except Exception as e:
            log.error(f"Failed to establish session: {e}")
            return False

    def fetch_items(
        self,
        product_ids: list[str],
        shop_id: str = DEFAULT_SHOP_ID,
        zone_id: str = DEFAULT_ZONE_ID,
        postal_code: str = DEFAULT_ZIP,
    ) -> Optional[dict]:
        """
        Query Aldi's GraphQL API for product data.

        Establishes a session first if needed, then makes the
        GraphQL request with session cookies + required headers.

        Args:
            product_ids:  List of numeric Aldi product IDs.
            shop_id:      Aldi store ID (from DevTools).
            zone_id:      Zone ID (from DevTools).
            postal_code:  Zip code for pricing.

        Returns:
            Parsed JSON response, or None on failure.
        """
        # Ensure we have auth cookies
        if not self._establish_session():
            log.error("Could not establish authenticated session")
            return None

        # Build the item IDs in Aldi's format: "items_23277-{productId}"
        item_ids = [f"{ITEM_PREFIX}-{pid}" for pid in product_ids]

        variables = {
            "ids": item_ids,
            "shopId": shop_id,
            "zoneId": zone_id,
            "postalCode": postal_code,
        }

        extensions = {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": ITEMS_QUERY_HASH,
            }
        }

        params = {
            "operationName": "Items",
            "variables": json.dumps(variables, separators=(",", ":")),
            "extensions": json.dumps(extensions, separators=(",", ":")),
        }

        # Headers that Aldi's frontend sends with GraphQL requests
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Referer": "https://www.aldi.us/",
            "x-client-identifier": "web",
            "x-ic-view-layer": "true",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        try:
            log.info(f"Fetching {len(product_ids)} items from Aldi GraphQL API")
            resp = self.session.get(
                GRAPHQL_URL,
                params=params,
                headers=headers,
                timeout=15,
            )

            if resp.status_code != 200:
                log.error(f"HTTP {resp.status_code}: {resp.text[:500]}")
                return None

            data = resp.json()

            # Check for GraphQL errors
            if "errors" in data:
                for err in data["errors"]:
                    log.error(f"GraphQL error: {err.get('message', err)}")
                if any("PersistedQueryNotFound" in str(e) for e in data["errors"]):
                    log.error(
                        "ITEMS_QUERY_HASH is stale. Open DevTools on aldi.us, "
                        "filter Network by 'graphql', find the 'Items' request, "
                        "and copy the new sha256Hash from the Payload tab."
                    )
                # If auth failed, clear session so it retries next time
                if any("Authenticated" in str(e) for e in data["errors"]):
                    log.info("Auth expired — will re-establish session on next call")
                    self._authenticated = False
                return None

            return data

        except Exception as e:
            log.error(f"Request failed: {e}")
            return None

    def search_product_ids(
        self,
        query: str,
        shop_id: str = DEFAULT_SHOP_ID,
        zone_id: str = DEFAULT_ZONE_ID,
        postal_code: str = DEFAULT_ZIP,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> Optional[list[str]]:
        """
        Search Aldi by keyword and return a list of numeric product IDs.

        The SearchResultsPlacements operation returns placement metadata only —
        product IDs are extracted from the SearchItemGrid placement, then passed
        to fetch_items for full product details.
        """
        if not self._establish_session():
            log.error("Could not establish authenticated session")
            return None

        page_view_id = str(uuid.uuid4())

        variables = {
            "filters": [],
            "action": None,
            "query": query,
            "pageViewId": page_view_id,
            "elevatedProductId": None,
            "searchSource": "search",
            "disableReformulation": False,
            "disableLlm": False,
            "forceInspiration": False,
            "orderBy": "bestMatch",
            "clusterId": None,
            "includeDebugInfo": False,
            "clusteringStrategy": None,
            "contentManagementSearchParams": {"itemGridColumnCount": 6},
            "shopId": shop_id,
            "postalCode": postal_code,
            "zoneId": zone_id,
            "first": limit,
        }

        extensions = {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": SEARCH_QUERY_HASH,
            }
        }

        params = {
            "operationName": "SearchResultsPlacements",
            "variables": json.dumps(variables, separators=(",", ":")),
            "extensions": json.dumps(extensions, separators=(",", ":")),
        }

        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Referer": f"https://www.aldi.us/store/aldi/s?k={quote(query)}",
            "x-client-identifier": "web",
            "x-ic-view-layer": "true",
            "x-page-view-id": page_view_id,
            "x-ic-qp": str(uuid.uuid4()),
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        try:
            log.info(f"Searching Aldi for: {query!r}")
            resp = self.session.get(
                GRAPHQL_URL,
                params=params,
                headers=headers,
                timeout=15,
            )

            if resp.status_code != 200:
                log.error(f"HTTP {resp.status_code}: {resp.text[:500]}")
                return None

            data = resp.json()

            if "errors" in data:
                for err in data["errors"]:
                    log.error(f"GraphQL error: {err.get('message', err)}")
                if any("PersistedQueryNotFound" in str(e) for e in data["errors"]):
                    log.error(
                        "SEARCH_QUERY_HASH is stale. Open DevTools on aldi.us, "
                        "search for any product, filter Network by 'graphql', "
                        "find the 'SearchResultsPlacements' request, and copy "
                        "the new sha256Hash from the Payload tab."
                    )
                return None

            placements = (
                data.get("data", {})
                    .get("searchResultsPlacements", {})
                    .get("placements", [])
            )
            for placement in placements:
                content = placement.get("content", {})
                if content.get("__typename") == "SearchContentManagementSearchItemGrid":
                    ids = [
                        str(p["id"])
                        for p in content.get("itemProperties", [])
                        if p.get("id")
                    ]
                    log.info(f"Search returned {len(ids)} results")
                    return ids

            log.warning("No SearchItemGrid found in search response")
            return []

        except Exception as e:
            log.error(f"Search request failed: {e}")
            return None


# ============================================================
# Response parsing
# ============================================================

def parse_item(item: dict) -> AldiProduct:
    """Parse a single item from the GraphQL response."""
    try:
        product_id = item.get("productId", "")
        name = item.get("name", "Unknown")
        size = item.get("size")
        brand = item.get("brandName")

        # --- Price ---
        # Path: item.price.viewSection
        price = None
        price_string = None
        unit_price_string = None
        sale_disclaimer = None

        price_node = item.get("price", {})
        view_section = price_node.get("viewSection", {}) if price_node else {}

        if view_section:
            price_string = view_section.get("priceString")
            price_value = view_section.get("priceValueString")
            if price_value:
                try:
                    price = float(price_value)
                except (ValueError, TypeError):
                    pass

            # Per-unit price is in itemDetails
            item_details = view_section.get("itemDetails", {})
            if item_details:
                unit_price_string = item_details.get("pricePerUnitString")
                sale_disclaimer = item_details.get("saleDisclaimerString")

        # --- Availability ---
        availability = item.get("availability", {})
        in_stock = availability.get("available", False) if availability else False

        # --- On sale ---
        # on_sale_ind lives on item.viewSection.trackingProperties, not price.viewSection
        item_view = item.get("viewSection", {})
        item_tracking = item_view.get("trackingProperties", {}) if item_view else {}
        on_sale_info = item_tracking.get("on_sale_ind", {})
        on_sale = on_sale_info.get("on_sale", False) if on_sale_info else False

        # --- Image ---
        item_image = item_view.get("itemImage", {}) if item_view else {}
        image_url = item_image.get("url") if item_image else None

        # --- Store location ---
        store_location = None
        location_info = item.get("inStoreItemLocation", {})
        if location_info:
            loc_section = location_info.get("viewSection", {})
            store_location = loc_section.get("locationString") if loc_section else None

        # --- URL ---
        evergreen = item.get("evergreenUrl", "")
        url = f"https://www.aldi.us/store/aldi/products/{evergreen}" if evergreen else ""

        return AldiProduct(
            name=name,
            product_id=product_id,
            price=price,
            price_string=price_string,
            unit_price_string=unit_price_string,
            size=size,
            brand=brand,
            in_stock=in_stock,
            on_sale=on_sale,
            sale_disclaimer=sale_disclaimer,
            store_location=store_location,
            url=url,
            image_url=image_url,
        )

    except (KeyError, TypeError) as e:
        return AldiProduct(
            name="PARSE_ERROR",
            product_id=item.get("productId", "???"),
            price=None,
            price_string=None,
            unit_price_string=None,
            size=None,
            brand=None,
            in_stock=False,
            on_sale=False,
            sale_disclaimer=None,
            store_location=None,
            url="",
            error=str(e),
        )


# ============================================================
# Public API
# ============================================================

def scrape_products(
    product_ids: list[str],
    shop_id: str = DEFAULT_SHOP_ID,
    zone_id: str = DEFAULT_ZONE_ID,
    postal_code: str = DEFAULT_ZIP,
    session: Optional[AldiSession] = None,
) -> list[AldiProduct]:
    """
    Scrape multiple Aldi products by ID.

    Batches requests to avoid oversized queries, with polite delays.

    Args:
        product_ids:  List of numeric product IDs (from URL).
        shop_id:      Aldi store ID.
        zone_id:      Zone ID.
        postal_code:  Zip code.
        session:      Optional AldiSession to reuse (creates one if not provided).

    Returns:
        List of AldiProduct results.
    """
    if session is None:
        session = AldiSession()

    all_results = []

    # Batch the IDs
    for i in range(0, len(product_ids), BATCH_SIZE):
        batch = product_ids[i:i + BATCH_SIZE]

        if i > 0:
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            log.info(f"Waiting {delay:.1f}s before next batch...")
            time.sleep(delay)

        data = session.fetch_items(batch, shop_id, zone_id, postal_code)

        if data is None:
            # Return error results for this batch
            for pid in batch:
                all_results.append(AldiProduct(
                    name="FETCH_ERROR",
                    product_id=pid,
                    price=None,
                    price_string=None,
                    unit_price_string=None,
                    size=None,
                    brand=None,
                    in_stock=False,
                    on_sale=False,
                    sale_disclaimer=None,
                    store_location=None,
                    url="",
                    error="GraphQL request failed",
                ))
            continue

        items = data.get("data", {}).get("items", [])
        for item in items:
            all_results.append(parse_item(item))

    return all_results


def find_products(
    query: str,
    shop_id: str = DEFAULT_SHOP_ID,
    zone_id: str = DEFAULT_ZONE_ID,
    postal_code: str = DEFAULT_ZIP,
    limit: int = DEFAULT_SEARCH_LIMIT,
    session: Optional[AldiSession] = None,
) -> list[AldiProduct]:
    """
    Search Aldi by keyword and return full product data.

    Combines search_product_ids and scrape_products into one call.
    """
    if session is None:
        session = AldiSession()

    product_ids = session.search_product_ids(query, shop_id, zone_id, postal_code, limit)
    if not product_ids:
        return []

    return scrape_products(product_ids, shop_id, zone_id, postal_code, session)


# ============================================================
# Utility
# ============================================================

def extract_id_from_url(url: str) -> Optional[str]:
    """
    Pull the product ID from an Aldi URL.

    Handles:
        https://www.aldi.us/store/aldi/products/16902710-friendly-farms-vitamin-d-milk-1-gal
        -> "16902710"
    """
    match = re.search(r'/products/(\d+)', url)
    return match.group(1) if match else None


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Aldi Grocery Price Scraper (No Selenium)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Search for products by keyword
  python aldi_scraper.py --find "whole milk"
  python aldi_scraper.py --find "organic eggs" --limit 10

  # Scrape specific products
  python aldi_scraper.py --ids 20986614 18649227 16902710

  # Extract product ID from a URL
  python aldi_scraper.py --url "https://www.aldi.us/store/aldi/products/16902710-friendly-farms-vitamin-d-milk-1-gal"

  # Use a different zip/store
  python aldi_scraper.py --ids 20986614 --zip 34747 --store 518104 --zone 178

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
            # ---- YOUR TRACKED PRODUCTS GO HERE ----
            # Find IDs from the product URL on aldi.us
            # e.g., /products/16902710-friendly-farms-...  ->  16902710
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

    # Save results
    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"\n  Results saved to {output_path}")


if __name__ == "__main__":
    main()