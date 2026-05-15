"""
Shared data models for scraper results.

These dataclasses define the shape of data that comes OUT of each
store's scraper.  They are storefront-neutral — any new store scraper
should return one of these (or a dict matching the same keys).

The pricing / unit-price layer (pricing/) consumes these and doesn't
need to know which store produced them.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class WalmartProduct:
    """Result from scraping a single Walmart product page."""
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
    serving_size: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AldiProduct:
    """Result from scraping an Aldi product via GraphQL."""
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
    serving_size: Optional[str] = None
    currency: str = "USD"
    error: Optional[str] = None
