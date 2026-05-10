"""
Aldi GraphQL response parser — extracts product data from API responses.

THIS FILE IS TIGHTLY COUPLED TO ALDI'S GRAPHQL RESPONSE STRUCTURE.
If Aldi (Instacart) changes their schema, this is the file that breaks.

Current response structure for each item in data.items[]:

    item.productId                          — numeric product ID
    item.name                               — product title
    item.size                               — pack size string, e.g. "20 fl oz"
    item.brandName                          — brand

    item.price.viewSection                  — pricing container
        .priceString                        — "$3.29"
        .priceValueString                   — "3.29" (parseable float)
        .itemDetails.pricePerUnitString     — "$0.02/fl oz"
        .itemDetails.saleDisclaimerString   — sale terms

    item.availability.available             — bool

    item.viewSection                        — display metadata
        .trackingProperties.on_sale_ind     — {on_sale: bool}
        .itemImage.url                      — product image URL

    item.inStoreItemLocation.viewSection
        .locationString                     — aisle/location in store

    item.evergreenUrl                       — URL slug for product page
"""

import logging
from scrapers.models import AldiProduct

log = logging.getLogger(__name__)


def parse_item(item: dict) -> AldiProduct:
    """Parse a single item from the GraphQL Items response."""
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
