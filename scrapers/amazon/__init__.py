"""
scrapers.amazon — Amazon product data via HTML scraping.

Supports both regular Amazon (Prime-eligible) and Amazon Fresh/Grocery.

Public API (used by app.py):
    scrape_search(query, zip_code, ...) -> list[dict]
    scrape_product(product_id, zip_code, ...) -> AmazonProduct
    scrape_product_list(product_ids, zip_code, ...) -> list[AmazonProduct]
    extract_id_from_url(url) -> Optional[str]
    find_stores_by_zip(zip_code) -> list[dict]
"""

from .api import scrape_search, scrape_product, scrape_product_list, extract_id_from_url
from .stores import find_stores_by_zip
from .config import DEFAULT_ZIP

__all__ = [
    "scrape_search", "scrape_product", "scrape_product_list",
    "extract_id_from_url", "find_stores_by_zip", "DEFAULT_ZIP",
]
