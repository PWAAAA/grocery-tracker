"""
Amazon public API — storefront-neutral orchestration.

These functions combine HTTP fetching + parsing to provide a clean
interface.  Nothing here knows about Amazon's page structure; that
knowledge is isolated in http.py (request mechanics) and parser.py
(response structure).

Supports two search modes:
  - Regular Amazon search (Prime-eligible items)
  - Amazon Fresh / Grocery search
"""

import re
import time
import random
import logging
from typing import Optional
from urllib.parse import quote_plus

from scrapers.models import AmazonProduct
from .config import (
    MAX_RETRIES, MIN_DELAY, MAX_DELAY, MAX_SEARCH_PAGES,
    DEFAULT_ZIP, BASE_URL, DEPT_GROCERY,
)
from .http import fetch_page
from .parser import parse_product_page, parse_search_results

log = logging.getLogger(__name__)


def scrape_product(
    product_id: str,
    zip_code: str = DEFAULT_ZIP,
    store_id: Optional[str] = None,
) -> AmazonProduct:
    """
    Scrape a single Amazon product page by ASIN.

    Args:
        product_id: The 10-character ASIN from the URL.
                    e.g., amazon.com/dp/B00MNV8E0C -> "B00MNV8E0C"
        zip_code:   Zip code for delivery availability / Fresh pricing.
        store_id:   Unused (kept for interface consistency with other scrapers).
    """
    url = f"{BASE_URL}/dp/{product_id}"
    log.info(f"Scraping Amazon product {product_id} (zip: {zip_code})")

    html = fetch_page(url, zip_code, max_retries=MAX_RETRIES)
    if html is None:
        return AmazonProduct(
            name="FETCH_ERROR",
            product_id=product_id,
            price=None,
            price_string=None,
            unit_price_string=None,
            size=None,
            brand=None,
            in_stock=False,
            on_sale=False,
            url=url,
            error="Failed to fetch page",
        )

    return parse_product_page(html, product_id)


def scrape_search(
    query: str,
    zip_code: str = DEFAULT_ZIP,
    store_id: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
    limit: int = 40,
    fresh_only: bool = False,
) -> list[dict]:
    """
    Scrape Amazon search results for a query.

    Args:
        query:       Search term (e.g., "whole milk gallon").
        zip_code:    Zip code for delivery-specific results.
        store_id:    Unused (interface consistency).
        max_retries: Retry attempts on CAPTCHA.
        limit:       Maximum number of results to return.
        fresh_only:  If True, search only Amazon Fresh/Grocery department.
    """
    log.info(f"Searching Amazon for '{query}' (zip: {zip_code}, limit: {limit}, fresh: {fresh_only})")

    referer = f"https://www.google.com/search?q={quote_plus(query + ' amazon')}"

    all_results = []
    seen_ids = set()

    for page_num in range(1, MAX_SEARCH_PAGES + 1):
        # Build search URL
        url = f"{BASE_URL}/s?k={quote_plus(query)}"
        if fresh_only:
            url += f"&i={DEPT_GROCERY}"
        if page_num > 1:
            url += f"&page={page_num}"

        log.info(f"Amazon search '{query}': fetching page {page_num}")

        html = fetch_page(
            url, zip_code,
            referer=referer,
            max_retries=max_retries,
        )
        if html is None:
            log.warning(f"Amazon search '{query}': page {page_num} fetch failed")
            break

        page_results = parse_search_results(html)
        if not page_results:
            log.info(f"Amazon search '{query}': page {page_num} returned 0 results, stopping")
            break

        # Deduplicate across pages
        new_count = 0
        for product in page_results:
            pid = product.get("product_id")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                all_results.append(product)
                new_count += 1

        log.info(f"Amazon search '{query}': page {page_num} added {new_count} new results (total: {len(all_results)})")

        if len(all_results) >= limit:
            break

        # Polite delay between page fetches
        if page_num < MAX_SEARCH_PAGES:
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            log.info(f"Waiting {delay:.1f}s before next page...")
            time.sleep(delay)

    log.info(f"Amazon search '{query}': {len(all_results)} total results")
    return all_results


def scrape_product_list(
    product_ids: list[str],
    zip_code: str = DEFAULT_ZIP,
    store_id: Optional[str] = None,
) -> list[AmazonProduct]:
    """Scrape multiple Amazon products with polite delays between requests."""
    results = []
    for i, pid in enumerate(product_ids):
        result = scrape_product(pid, zip_code, store_id)
        results.append(result)
        if i < len(product_ids) - 1:
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            log.info(f"Waiting {delay:.1f}s before next request...")
            time.sleep(delay)
    return results


def extract_id_from_url(url: str) -> Optional[str]:
    """
    Pull the ASIN from an Amazon URL.

    Handles:
        https://www.amazon.com/dp/B00MNV8E0C
        https://www.amazon.com/Product-Name/dp/B00MNV8E0C
        https://www.amazon.com/gp/product/B00MNV8E0C
        https://amazon.com/dp/B00MNV8E0C?tag=whatever
    """
    # /dp/ASIN pattern (most common)
    match = re.search(r'/dp/([A-Z0-9]{10})', url, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # /gp/product/ASIN pattern
    match = re.search(r'/gp/product/([A-Z0-9]{10})', url, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # /gp/aw/d/ASIN pattern (mobile)
    match = re.search(r'/gp/aw/d/([A-Z0-9]{10})', url, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    return None
