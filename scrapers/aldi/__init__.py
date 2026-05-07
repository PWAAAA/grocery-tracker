"""
scrapers.aldi — Aldi product data via Instacart GraphQL API.

Public API (used by app.py):
    find_products(query, ...) -> list[AldiProduct]
    AldiSession  — reusable session with auth cookies
    DEFAULT_SHOP_ID, DEFAULT_ZONE_ID, DEFAULT_ZIP
"""

from .config import DEFAULT_SHOP_ID, DEFAULT_ZONE_ID, DEFAULT_ZIP
from .session import AldiSession
from .api import find_products, scrape_products, extract_id_from_url

__all__ = [
    "DEFAULT_SHOP_ID", "DEFAULT_ZONE_ID", "DEFAULT_ZIP",
    "AldiSession",
    "find_products", "scrape_products", "extract_id_from_url",
]
