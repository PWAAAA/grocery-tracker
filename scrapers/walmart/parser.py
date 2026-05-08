"""
Walmart page-structure parser — extracts product data from HTML.

THIS FILE IS TIGHTLY COUPLED TO WALMART'S CURRENT PAGE STRUCTURE.
If Walmart changes their frontend, this is the file that breaks.

Current approach (as of 2025):
    Walmart renders product data server-side into a <script> tag:

        <script id="__NEXT_DATA__" type="application/json">{ ... }</script>

    This JSON blob contains ALL the data the React frontend hydrates from.
    We parse it directly instead of running JS — no headless browser needed.

Product page structure (single product):
    data["props"]["pageProps"]["initialData"]["data"]["product"]
        .name             — product title
        .priceInfo.currentPrice.price       — numeric price
        .priceInfo.currentPrice.priceString — "$3.28"
        .priceInfo.unitPrice.priceString    — "$0.21/fl oz"
        .priceInfo.isPriceReduced           — sale flag
        .availabilityStatus                 — "IN_STOCK" / "AVAILABLE" / other
        .brand                              — brand name
        .canonicalUrl                       — "/ip/Product-Name/12345"
        .imageInfo.thumbnailUrl             — product image

Search results structure (multiple products):
    data["props"]["pageProps"]["initialData"]["searchResult"]["itemStacks"]
        Each stack has an "items" array.  Items with __typename == "Product":
            .usItemId       — product ID
            .name           — title
            .price          — numeric price
            .priceInfo.unitPrice  — "$0.21/fl oz" (string, not nested)
            .averageRating  — star rating
            .image          — thumbnail URL
            .isSponsoredFlag — ad indicator
"""

import json
import logging
from typing import Optional

from bs4 import BeautifulSoup

from scrapers.models import WalmartProduct

log = logging.getLogger(__name__)


def extract_next_data(html: str) -> Optional[dict]:
    """Pull the __NEXT_DATA__ JSON blob from the page HTML.

    Walmart embeds all page data in:
        <script id="__NEXT_DATA__" type="application/json">...</script>

    Returns the parsed dict, or None if the tag is missing/malformed.
    """
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


def parse_product_page(data: dict, product_id: str) -> WalmartProduct:
    """
    Extract price and product info from a single product page's
    __NEXT_DATA__ blob.

    JSON path: props.pageProps.initialData.data.product
        .priceInfo.currentPrice   — {price, priceString, currencyUnit}
        .priceInfo.unitPrice      — {priceString}  e.g. "$0.21/fl oz"
        .priceInfo.isPriceReduced — bool
        .availabilityStatus       — "IN_STOCK" | "AVAILABLE" | ...
        .imageInfo.thumbnailUrl   — product image
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

        return WalmartProduct(
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
        return WalmartProduct(
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

    JSON path: props.pageProps.initialData.searchResult.itemStacks[]
        Each stack contains .items[] where __typename == "Product":
            .usItemId           — numeric product ID
            .name               — product title
            .price              — numeric price (float)
            .priceInfo.unitPrice — unit price string, e.g. "$0.21/fl oz"
            .averageRating      — star rating (float)
            .image              — thumbnail URL
            .isSponsoredFlag    — True for sponsored/ad placements
    """
    results = []
    seen_ids = set()
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
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                price_info = item.get("priceInfo")
                if not isinstance(price_info, dict):
                    price_info = {}
                unit_price_string = price_info.get("unitPrice") or None
                if not isinstance(unit_price_string, str):
                    unit_price_string = None

                # Determine if the item is available in-store (pickup)
                # vs shipping-only. fulfillmentBadgeGroups contains badges
                # like FF_PICKUP (in-store) or FF_SHIPPING (ship-to-home).
                badge_groups = item.get("fulfillmentBadgeGroups") or []
                badge_keys = {bg.get("key") for bg in badge_groups if isinstance(bg, dict)}
                in_store = "FF_PICKUP" in badge_keys

                results.append({
                    "name": item.get("name"),
                    "product_id": pid,
                    "price": item.get("price"),
                    "unit_price_string": unit_price_string,
                    "rating": item.get("averageRating"),
                    "image": item.get("image"),
                    "url": f"https://www.walmart.com/ip/{pid}",
                    "sponsored": item.get("isSponsoredFlag", False),
                    "in_store": in_store,
                })
            except Exception as e:
                log.warning(f"Skipping malformed search item: {e}")

    return results
