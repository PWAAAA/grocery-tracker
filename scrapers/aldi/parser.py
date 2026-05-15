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


def _extract_serving_size_from_details(details: dict) -> str | None:
    """Extract serving size string from the IDP details dict.

    The details dict from Aldi's IDP API may contain nutrition info
    under keys like 'nutrition_information', 'serving_size', or nested
    within a 'nutrition_facts' sub-object.
    """
    if not details or not isinstance(details, dict):
        return None

    # Direct serving_size key
    for key in ("serving_size", "servingSize", "serving_size_string"):
        val = details.get(key)
        if val:
            return str(val)

    # Nested in nutrition_information or nutrition_facts
    for section_key in ("nutrition_information", "nutrition_facts", "nutritionFacts"):
        section = details.get(section_key)
        if isinstance(section, dict):
            for key in ("serving_size", "servingSize", "serving_size_string"):
                val = section.get(key)
                if val:
                    return str(val)

    # Some IDP responses put nutrition in a list of key-value pairs
    nutrition_list = details.get("nutrition") or details.get("nutritional_info")
    if isinstance(nutrition_list, list):
        for item in nutrition_list:
            if isinstance(item, dict):
                label = str(item.get("label", "") or item.get("name", "")).lower()
                if "serving" in label:
                    val = item.get("value") or item.get("display_value")
                    if val:
                        return str(val)

    return None


def parse_idp_product(product: dict) -> AldiProduct:
    """Parse a single product from the IDP /v1/products response."""
    try:
        product_id = str(product.get("id", ""))
        name = product.get("name", "Unknown")
        image_url = product.get("image_url")
        permalink = product.get("permalink_url", "")

        attrs = product.get("shop_level_attributes", {})
        size = attrs.get("size")

        # Pricing
        price = None
        price_string = None
        pricing = attrs.get("pricing", {})
        final = pricing.get("final_price", {})
        unit_price = final.get("unit_price", {})
        cents = unit_price.get("amount_cents")
        if cents is not None:
            price = cents / 100.0
            price_string = f"${price:.2f}"

        # Availability
        avail = attrs.get("availability", "")
        in_stock = avail not in ("OUT_OF_STOCK", "UNAVAILABLE", "")

        # Details — contains nutrition/serving size info
        details = attrs.get("details", {})
        brand = None  # IDP doesn't expose brand separately
        serving_size = _extract_serving_size_from_details(details)

        url = permalink or f"https://www.aldi.us/store/aldi/products/{product_id}"

        return AldiProduct(
            name=name,
            product_id=product_id,
            price=price,
            price_string=price_string,
            unit_price_string=None,
            size=size,
            brand=brand,
            in_stock=in_stock,
            on_sale=False,
            sale_disclaimer=None,
            store_location=None,
            url=url,
            image_url=image_url,
            serving_size=serving_size,
        )

    except (KeyError, TypeError) as e:
        log.error(f"Failed to parse IDP product {product.get('id', '???')}: {e}")
        return AldiProduct(
            name="PARSE_ERROR",
            product_id=str(product.get("id", "???")),
            price=None, price_string=None, unit_price_string=None,
            size=None, brand=None, in_stock=False, on_sale=False,
            sale_disclaimer=None, store_location=None, url="",
            error=str(e),
        )


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
                # For variable-weight items (size="per lb"), pricePerUnitString
                # is the estimated package price ("About $10.95 each") — useless.
                # The real per-unit rate lives in pricingUnitString ("$2.19 / lb").
                pricing_unit = item_details.get("pricingUnitString")
                if pricing_unit and size and size.lower().startswith("per "):
                    unit_price_string = pricing_unit

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

        # --- Serving size ---
        serving_size = None
        item_details = item.get("details")
        if isinstance(item_details, dict):
            serving_size = _extract_serving_size_from_details(item_details)

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
            serving_size=serving_size,
        )

    except (KeyError, TypeError) as e:
        log.error(f"Failed to parse Aldi item {item.get('productId', '???')}: {e}")
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
