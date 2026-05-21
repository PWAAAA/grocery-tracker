"""
Aldi public API — storefront-neutral orchestration.

These functions combine session management + parsing to provide a
clean interface.  Nothing here knows about Aldi's GraphQL schema;
that knowledge is isolated in session.py (request mechanics) and
parser.py (response structure).
"""

import re
import time
import random
import logging
from typing import Optional

from scrapers.models import AldiProduct
from .config import (
    DEFAULT_SHOP_ID, DEFAULT_ZONE_ID, DEFAULT_ZIP,
    BATCH_SIZE, MIN_DELAY, MAX_DELAY, DEFAULT_SEARCH_LIMIT,
)
from .session import AldiSession
from .parser import parse_item, parse_idp_product

log = logging.getLogger(__name__)


def scrape_products(
    product_ids: list[str],
    shop_id: str = DEFAULT_SHOP_ID,
    zone_id: str = DEFAULT_ZONE_ID,
    postal_code: str = DEFAULT_ZIP,
    session: Optional[AldiSession] = None,
) -> list[AldiProduct]:
    """
    Scrape multiple Aldi products by ID.

    Uses the IDP REST API when a non-default shop is selected (works for
    any store without zone_id). Falls back to the GraphQL Items query
    for the default shop (which has a known zone_id).

    Batches requests to avoid oversized queries, with polite delays.
    """
    if session is None:
        session = AldiSession()

    use_idp = shop_id != DEFAULT_SHOP_ID
    all_results = []

    for i in range(0, len(product_ids), BATCH_SIZE):
        batch = product_ids[i:i + BATCH_SIZE]

        if i > 0:
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            log.info(f"Waiting {delay:.1f}s before next batch...")
            time.sleep(delay)

        if use_idp:
            data = session.fetch_items_idp(batch, shop_id)
            if data is None:
                for pid in batch:
                    all_results.append(AldiProduct(
                        name="FETCH_ERROR", product_id=pid,
                        price=None, price_string=None, unit_price_string=None,
                        size=None, brand=None, in_stock=False, on_sale=False,
                        sale_disclaimer=None, store_location=None, url="",
                        error="IDP request failed",
                    ))
                continue
            for product in data.get("products", []):
                all_results.append(parse_idp_product(product))
        else:
            data = session.fetch_items(batch, shop_id, zone_id, postal_code)
            if data is None:
                for pid in batch:
                    all_results.append(AldiProduct(
                        name="FETCH_ERROR", product_id=pid,
                        price=None, price_string=None, unit_price_string=None,
                        size=None, brand=None, in_stock=False, on_sale=False,
                        sale_disclaimer=None, store_location=None, url="",
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
        log.info(f"Aldi search '{query}': no product IDs found")
        return []

    products = scrape_products(product_ids, shop_id, zone_id, postal_code, session)
    errors = sum(1 for p in products if p.error)
    log.info(f"Aldi search '{query}': {len(products)} results ({errors} errors)")
    return products


def extract_id_from_url(url: str) -> Optional[str]:
    """
    Pull the product ID from an Aldi URL.

    Handles:
        https://www.aldi.us/store/aldi/products/16902710-friendly-farms-vitamin-d-milk-1-gal
        https://www.aldi.us/product/friendly-farms-1-milk-1-gal-0000000000001754
    """
    # Format 1: /products/{id}-{slug}
    match = re.search(r'/products/(\d+)', url)
    if match:
        return match.group(1)
    # Format 2: /product/{slug}-{id}  (ID is trailing digits at end of path)
    match = re.search(r'/product/.*?-(\d{7,})(?:\?|$)', url)
    if match:
        return match.group(1)
    return None
