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
from .config import MAX_RETRIES, MIN_DELAY, MAX_DELAY
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
) -> list[dict]:
    """
    Scrape Walmart search results for a query.

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
