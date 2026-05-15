"""
Walmart public API — storefront-neutral orchestration.

These functions combine HTTP fetching + parsing to provide a clean
interface.  Nothing here knows about Walmart's page structure; that
knowledge is isolated in http.py (request mechanics) and parser.py
(response structure).
"""

import re
import time
import random
import logging
from typing import Optional
from urllib.parse import quote_plus

from scrapers.models import WalmartProduct
from .config import MAX_RETRIES, MIN_DELAY, MAX_DELAY, MAX_SEARCH_PAGES
from .http import fetch_page, HAS_CFFI
from .parser import extract_next_data, parse_product_page, parse_search_results

log = logging.getLogger(__name__)


def scrape_product(
    product_id: str,
    zip_code: str = "32801",
    store_id: Optional[str] = None,
) -> WalmartProduct:
    """
    Scrape a single Walmart product page by product ID.

    Args:
        product_id: The numeric Walmart product ID from the URL.
                    e.g., walmart.com/ip/Some-Product-Name/10450114
                    -> product_id = "10450114"
        zip_code:   Zip code for location-specific pricing.
        store_id:   Optional Walmart store ID for exact store pricing.

    Returns:
        WalmartProduct dataclass with price and product info.
    """
    url = f"https://www.walmart.com/ip/{product_id}"
    log.info(f"Scraping product {product_id} (zip: {zip_code})")

    html = fetch_page(url, zip_code, store_id, max_retries=1)
    if html is None:
        return WalmartProduct(
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
        return WalmartProduct(
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
    limit: int = 40,
) -> list[dict]:
    """
    Scrape Walmart search results for a query, fetching multiple pages if needed.

    Args:
        query:       Search term (e.g., "whole milk gallon").
        zip_code:    Zip code for location-specific pricing.
        store_id:    Optional store ID.
        max_retries: Retry attempts on CAPTCHA (default 3).
        limit:       Maximum number of results to return.

    Returns:
        List of product summary dicts with name, product_id, price, url.
    """
    log.info(f"Searching Walmart for '{query}' (zip: {zip_code}, limit: {limit})")

    # Simulate arriving from Google — Walmart is less aggressive with
    # requests that have a search engine referer
    referer = f"https://www.google.com/search?q={quote_plus(query + ' walmart')}"

    all_results = []
    seen_ids = set()

    for page_num in range(1, MAX_SEARCH_PAGES + 1):
        url = f"https://www.walmart.com/search?q={quote_plus(query)}"
        if page_num > 1:
            url += f"&page={page_num}"

        log.info(f"Walmart search '{query}': fetching page {page_num}")

        html = fetch_page(
            url, zip_code, store_id,
            referer=referer,
            max_retries=max_retries,
        )
        if html is None:
            log.warning(f"Walmart search '{query}': page {page_num} fetch returned no HTML")
            break

        data = extract_next_data(html)
        if data is None:
            log.warning(f"Walmart search '{query}': page {page_num} no __NEXT_DATA__ found")
            break

        page_results = parse_search_results(data)
        if not page_results:
            log.info(f"Walmart search '{query}': page {page_num} returned 0 results, stopping")
            break

        # Deduplicate across pages (Walmart sometimes repeats sponsored items)
        new_count = 0
        for product in page_results:
            pid = product.get("product_id")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                all_results.append(product)
                new_count += 1

        log.info(f"Walmart search '{query}': page {page_num} added {new_count} new results (total: {len(all_results)})")

        if len(all_results) >= limit:
            break

        # Polite delay between page fetches
        if page_num < MAX_SEARCH_PAGES:
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            log.info(f"Waiting {delay:.1f}s before next page...")
            time.sleep(delay)

    log.info(f"Walmart search '{query}': {len(all_results)} total results across pages")
    return all_results


def scrape_product_list(
    product_ids: list[str],
    zip_code: str = "32801",
    store_id: Optional[str] = None,
) -> list[WalmartProduct]:
    """Scrape multiple products with polite delays between requests."""
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
    Pull the product ID from a Walmart URL.

    Handles:
        https://www.walmart.com/ip/Product-Name-Here/10450114
        https://www.walmart.com/ip/10450114
        walmart.com/ip/Whatever/10450114?some=param
    """
    match = re.search(r'/ip/(?:[^/]+/)?(\d+)', url)
    return match.group(1) if match else None
