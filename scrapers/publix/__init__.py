"""
scrapers.publix -- Publix product data via savings/deals API.

Supports weekly ad items (BOGOs, sale prices, multi-buy deals)
and TPR (temporary price reductions). Base prices are extracted from
deal data and stored persistently for future reference.

Public API (used by app.py):
    scrape_search(query, ...) -> list[dict]
    scrape_weekly_ad(...) -> list[dict]
    scrape_product(product_id, ...) -> dict | None
    scrape_product_list(product_ids, ...) -> list[dict]
    extract_id_from_url(url) -> str | None
    find_stores_by_zip(zip_code) -> list[dict]
    PublixSession -- reusable session with Akamai bypass
"""

from .api import (
    scrape_search,
    scrape_weekly_ad,
    scrape_product,
    scrape_product_list,
    extract_id_from_url,
)
from .stores import find_stores_by_zip
from .http import PublixSession
from .config import DEFAULT_ZIP

__all__ = [
    "scrape_search",
    "scrape_weekly_ad",
    "scrape_product",
    "scrape_product_list",
    "extract_id_from_url",
    "find_stores_by_zip",
    "PublixSession",
    "DEFAULT_ZIP",
]
