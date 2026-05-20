"""Parse Publix savings API responses into structured product data."""

import html
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)


def parse_savings_text(savings_str: str) -> dict:
    """Parse the 'savings' field from Publix API into structured deal info.

    Returns a dict with:
        deal_type: "bogo" | "multi_buy" | "sale_price" | "dollars_off" | "percent_off" | "other"
        sale_price: float or None (the advertised price per unit)
        per_lb: bool (True if price is per pound)
        buy_qty: int (number to buy for deal, e.g. 2 for "2 for $5")
        get_qty: int (number you get free, e.g. 1 for BOGO)
        dollars_off: float or None (for "$X off" deals)
        percent_off: float or None (for "X% Off" deals)
        raw: str (original text)
    """
    s = html.unescape(savings_str).strip()
    result = {
        "deal_type": "other",
        "sale_price": None,
        "per_lb": False,
        "buy_qty": 1,
        "get_qty": 0,
        "dollars_off": None,
        "percent_off": None,
        "raw": s,
    }

    # "Buy 1 Get 1 FREE"
    m = re.match(r"Buy\s+(\d+)\s+Get\s+(\d+)\s+FREE", s, re.I)
    if m:
        result["deal_type"] = "bogo"
        result["buy_qty"] = int(m.group(1))
        result["get_qty"] = int(m.group(2))
        return result

    # "Buy 2 Get $6.00 Off"
    m = re.match(r"Buy\s+(\d+)\s+Get\s+\$([\d.]+)\s+Off", s, re.I)
    if m:
        result["deal_type"] = "dollars_off"
        result["buy_qty"] = int(m.group(1))
        result["dollars_off"] = float(m.group(2))
        return result

    # "X for $Y.YY" or "X/$Y.YY" (multi-buy)
    m = re.match(r"(\d+)\s*(?:for|/)\s*\$([\d.]+)", s, re.I)
    if m:
        qty = int(m.group(1))
        total = float(m.group(2))
        result["deal_type"] = "multi_buy"
        result["buy_qty"] = qty
        result["sale_price"] = round(total / qty, 2)
        return result

    # "X for $Y.YY Each" — per-item price already given
    m = re.match(r"(\d+)\s+for\s+\$([\d.]+)\s+[Ee]ach", s, re.I)
    if m:
        result["deal_type"] = "multi_buy"
        result["buy_qty"] = int(m.group(1))
        result["sale_price"] = float(m.group(2))
        return result

    # "$X.XX off" (without MFR coupon qualifier)
    m = re.match(r"\$([\d.]+)\s+[Oo]ff\b", s)
    if m and "COUPON" not in s.upper():
        result["deal_type"] = "dollars_off"
        result["dollars_off"] = float(m.group(1))
        return result

    # "$X.XX off WITH MFR DIGITAL COUPON"
    m = re.match(r"\$([\d.]+)\s+off.*COUPON", s, re.I)
    if m:
        result["deal_type"] = "dollars_off"
        result["dollars_off"] = float(m.group(1))
        return result

    # "X% Off"
    m = re.match(r"(\d+)%\s+[Oo]ff", s)
    if m:
        result["deal_type"] = "percent_off"
        result["percent_off"] = float(m.group(1))
        return result

    # "Sale Price $X.XX" or "Sale Price X/$Y.YY"
    m = re.match(r"Sale\s+Price\s+\$([\d.]+)", s, re.I)
    if m:
        result["deal_type"] = "sale_price"
        result["sale_price"] = float(m.group(1))
        return result
    m = re.match(r"Sale\s+Price\s+(\d+)/\$([\d.]+)", s, re.I)
    if m:
        qty = int(m.group(1))
        total = float(m.group(2))
        result["deal_type"] = "multi_buy"
        result["buy_qty"] = qty
        result["sale_price"] = round(total / qty, 2)
        return result

    # Plain "$X.XX" or "$X.XX lb" or "$X.XX each"
    m = re.match(r"\$([\d.]+)(?:\s+(lb|each))?$", s, re.I)
    if m:
        result["deal_type"] = "sale_price"
        result["sale_price"] = float(m.group(1))
        if m.group(2) and m.group(2).lower() == "lb":
            result["per_lb"] = True
        return result

    # Compound BOGO with specific product text (e.g. "Buy Any 1 ... and Get 1 Free")
    m = re.search(r"Buy\s+Any\s+(\d+).*Get.*(\d+)\s+Free", s, re.I)
    if m:
        result["deal_type"] = "bogo"
        result["buy_qty"] = int(m.group(1))
        result["get_qty"] = int(m.group(2))
        return result

    # "$X.XX off $Y.YY WITH MFR DIGITAL COUPON" (spend threshold)
    m = re.match(r"\$([\d.]+)\s+off\s+\$([\d.]+)", s, re.I)
    if m:
        result["deal_type"] = "dollars_off"
        result["dollars_off"] = float(m.group(1))
        return result

    # "Save $X.XX" (digital coupon / generic savings)
    m = re.match(r"Save\s+\$([\d.]+)", s, re.I)
    if m:
        result["deal_type"] = "dollars_off"
        result["dollars_off"] = float(m.group(1))
        return result

    log.debug(f"Unrecognized savings text: {s}")
    return result


def parse_additional_deal_info(info_str: Optional[str]) -> dict:
    """Parse 'additionalDealInfo' like 'SAVE UP TO $6.99 LB' or 'SAVE UP TO $3.99'.

    Returns dict with:
        max_savings: float or None
        per_lb: bool
    """
    if not info_str:
        return {"max_savings": None, "per_lb": False}

    s = html.unescape(info_str).strip()

    m = re.search(r"SAVE\s+UP\s+TO\s+\$([\d.]+)(?:\s+(LB|ON\s+\d+))?", s, re.I)
    if m:
        return {
            "max_savings": float(m.group(1)),
            "per_lb": bool(m.group(2) and m.group(2).upper() == "LB"),
        }

    return {"max_savings": None, "per_lb": False}


def clean_html_text(text: Optional[str]) -> Optional[str]:
    """Unescape HTML entities and clean up text."""
    if not text:
        return text
    cleaned = html.unescape(text)
    # Normalize whitespace from &#13;&#10; etc
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_size_from_description(description: Optional[str]) -> Optional[str]:
    """Try to extract product size from the description field.

    Examples:
        "Or Homestyle, 15 or 24-oz jar" -> "15 or 24-oz jar"
        "750 ml" -> "750 ml"
        "64-oz bot." -> "64-oz bot."
        "12-oz can or 12-oz bot., 6-pk." -> "12-oz can or 12-oz bot., 6-pk."
    """
    if not description:
        return None

    desc = clean_html_text(description)

    # Look for size patterns: digits followed by oz/ml/lb/gal/ct/pk/qt/fl oz/l
    m = re.search(
        r"(\d+(?:\.\d+)?(?:\s*(?:or|to|-)\s*\d+(?:\.\d+)?)?)"
        r"\s*-?\s*"
        r"(fl\s*oz|oz|ml|[Ll]|lb|gal|ct|pk|qt|pt)"
        r"[^,]*",
        desc, re.I
    )
    if m:
        return m.group(0).strip().rstrip(",").strip()

    return None


def _clean_size_string(size: str) -> str:
    """Remove redundant parenthetical weight/volume equivalents from Publix sizes.

    Publix TPR descriptions often include the same weight in multiple formats:
        "28 oz (1 lb 12 oz) 794 g"  -> "28 oz"
        "13.8 oz (390 g)"           -> "13.8 oz"
        "32 fl oz (946 ml)"         -> "32 fl oz"
        "25 OZ (1.56 LB) (709 g)"  -> "25 OZ"

    The pricing parser would otherwise sum all values, producing wrong unit prices.
    """
    # Strip parenthetical groups containing units (metric or imperial equivalents)
    cleaned = re.sub(
        r"\s*\([^)]*(?:oz|lb|g|kg|ml|l)\b[^)]*\)",
        "", size, flags=re.I
    )
    # Also strip trailing bare metric: "28 oz 794 g" -> "28 oz"
    cleaned = re.sub(
        r"\s+\d+(?:\.\d+)?\s*(?:g|kg|ml)\b",
        "", cleaned
    )
    return cleaned.strip()


def parse_description_stacking(description: Optional[str]) -> dict:
    """Parse coupon stacking info from description text.

    Patterns like:
        "Sale Price 5.99 - Digital Coupon 3.00 off 2 = FINAL PRICE WITH 2/8.98 MFR DIGITAL COUPON"
        "Sale Price 2/10.00 - Digital Coupon 3.00 off 2 = FINAL PRICE WITH 2/7.00 MFR DIGITAL COUPON"

    Returns dict with:
        coupon_value: float or None (total coupon discount)
        coupon_min_qty: int or None (items needed for coupon)
        final_total: float or None (total after coupon for coupon_min_qty items)
        final_per_unit: float or None (per-unit price after coupon)
    """
    if not description:
        return {}

    desc = clean_html_text(description)

    # "Digital Coupon X.XX off Y = FINAL PRICE WITH Y/TOTAL"
    m = re.search(
        r"Digital\s+Coupon\s+([\d.]+)\s+off\s+(\d+)\s*=\s*FINAL\s+PRICE\s+WITH\s+(\d+)/([\d.]+)",
        desc, re.I
    )
    if m:
        coupon_value = float(m.group(1))
        coupon_min_qty = int(m.group(2))
        final_qty = int(m.group(3))
        final_total = float(m.group(4))
        return {
            "coupon_value": coupon_value,
            "coupon_min_qty": coupon_min_qty,
            "final_total": final_total,
            "final_per_unit": round(final_total / final_qty, 2) if final_qty else None,
        }

    return {}


def savings_item_to_product_dict(item: dict) -> dict:
    """Convert a raw Publix savings API item to a standardized product dict.

    Handles WeeklyAd, Tpr, Stacked, and DigitalCoupon items.
    """
    title = clean_html_text(item.get("title", ""))
    description = clean_html_text(item.get("description"))
    brand = clean_html_text(item.get("brand"))
    savings_text = item.get("savings", "")
    saving_type = item.get("savingType", "")
    min_purchase = item.get("minimumPurchase") or 0

    deal = parse_savings_text(savings_text)
    additional = parse_additional_deal_info(item.get("additionalDealInfo"))

    # Determine price based on deal type and data source
    price = None
    price_string = None
    base_price = None
    on_sale = True
    bogo_price = None
    coupon_value = None
    coupon_min_qty = None
    coupon_price = None  # effective per-unit price after coupon

    if saving_type == "DigitalCoupon":
        # Standalone digital coupon — no sale price, just the discount
        coupon_value = deal.get("dollars_off")
        coupon_min_qty = max(min_purchase, 1)
        price_string = clean_html_text(savings_text)
        if coupon_min_qty > 1:
            price_string += f" (on {coupon_min_qty})"

    elif saving_type == "Tpr":
        # TPR items have finalPrice directly
        price = item.get("finalPrice")
        savings_amount = None
        m = re.match(r"\$([\d.]+)", savings_text)
        if m:
            savings_amount = float(m.group(1))
        if price:
            price_string = f"${price:.2f}"
            if savings_amount:
                base_price = round(price + savings_amount, 2)
                price_string = f"${price:.2f} (was ${base_price:.2f})"

    elif saving_type == "Stacked":
        # Stacked = sale + coupon combined. Parse as normal deal,
        # then check for coupon info via minimumPurchase and description.
        stacking = parse_description_stacking(description)

        if deal["deal_type"] == "sale_price":
            price = deal["sale_price"]
            if additional["max_savings"]:
                base_price = round(price + additional["max_savings"], 2)
            if stacking.get("final_per_unit") is not None:
                coupon_value = stacking["coupon_value"]
                coupon_min_qty = stacking["coupon_min_qty"]
                coupon_price = stacking["final_per_unit"]
            elif min_purchase > 1 and additional["max_savings"]:
                # Coupon savings = total adi savings - sale savings
                # But we can approximate: coupon_price from adi
                coupon_min_qty = min_purchase
            if deal["per_lb"]:
                price_string = f"${price:.2f}/lb"
            else:
                price_string = f"${price:.2f}"
                if coupon_price is not None:
                    price_string += f" (w/ coupon on {coupon_min_qty}: ${coupon_price:.2f})"
                elif base_price:
                    price_string = f"${price:.2f} (was ${base_price:.2f})"

        elif deal["deal_type"] == "multi_buy":
            price = deal["sale_price"]
            price_string = clean_html_text(savings_text)
            if additional["max_savings"] and deal["buy_qty"]:
                per_item_savings = additional["max_savings"] / deal["buy_qty"]
                base_price = round(price + per_item_savings, 2)
            if stacking.get("final_per_unit") is not None:
                coupon_value = stacking["coupon_value"]
                coupon_min_qty = stacking["coupon_min_qty"]
                coupon_price = stacking["final_per_unit"]
                price_string += f" (w/ coupon on {coupon_min_qty}: ${coupon_price:.2f} ea)"
            elif min_purchase > 1:
                coupon_min_qty = min_purchase

        elif deal["deal_type"] == "bogo":
            max_sav = additional["max_savings"]
            if max_sav:
                buy = deal["buy_qty"]
                get = deal["get_qty"]
                base_price = max_sav
                price = max_sav
                bogo_price = round(max_sav * buy / (buy + get), 2)
                if additional["per_lb"]:
                    price_string = f"${price:.2f}/lb (BOGO: ${bogo_price:.2f}/lb)"
                else:
                    price_string = f"${price:.2f} (BOGO: ${bogo_price:.2f})"
            else:
                price_string = clean_html_text(savings_text)

        else:
            price_string = clean_html_text(savings_text)

    else:
        # WeeklyAd and other types — original logic
        # Check description for coupon stacking info
        stacking = parse_description_stacking(description)

        if deal["deal_type"] == "sale_price":
            price = deal["sale_price"]
            if deal["per_lb"]:
                price_string = f"${price:.2f}/lb"
                if additional["max_savings"]:
                    base_price = round(price + additional["max_savings"], 2)
                    price_string = f"${price:.2f}/lb (was ${base_price:.2f}/lb)"
            else:
                price_string = f"${price:.2f}"
                if additional["max_savings"]:
                    base_price = round(price + additional["max_savings"], 2)
                    price_string = f"${price:.2f} (was ${base_price:.2f})"
            if stacking.get("final_per_unit") is not None:
                coupon_value = stacking["coupon_value"]
                coupon_min_qty = stacking["coupon_min_qty"]
                coupon_price = stacking["final_per_unit"]
                price_string += f" (w/ coupon on {coupon_min_qty}: ${coupon_price:.2f})"

        elif deal["deal_type"] == "multi_buy":
            price = deal["sale_price"]
            price_string = clean_html_text(savings_text)
            if additional["max_savings"] and deal["buy_qty"]:
                per_item_savings = additional["max_savings"] / deal["buy_qty"]
                base_price = round(price + per_item_savings, 2)
            if stacking.get("final_per_unit") is not None:
                coupon_value = stacking["coupon_value"]
                coupon_min_qty = stacking["coupon_min_qty"]
                coupon_price = stacking["final_per_unit"]
                price_string += f" (w/ coupon on {coupon_min_qty}: ${coupon_price:.2f} ea)"

        elif deal["deal_type"] == "bogo":
            max_sav = additional["max_savings"]
            if max_sav:
                buy = deal["buy_qty"]
                get = deal["get_qty"]
                base_price = max_sav
                price = max_sav
                bogo_price = round(max_sav * buy / (buy + get), 2)
                if additional["per_lb"]:
                    price_string = f"${price:.2f}/lb (BOGO: ${bogo_price:.2f}/lb)"
                else:
                    price_string = f"${price:.2f} (BOGO: ${bogo_price:.2f})"
            else:
                price_string = clean_html_text(savings_text)

        elif deal["deal_type"] == "dollars_off":
            price_string = clean_html_text(savings_text)
        elif deal["deal_type"] == "percent_off":
            price_string = clean_html_text(savings_text)
        else:
            price_string = clean_html_text(savings_text)

    # Build size from description (skip for DigitalCoupon — description is the coupon text)
    size = None
    if saving_type != "DigitalCoupon":
        size = extract_size_from_description(description)
        if saving_type == "Tpr" and description and not size:
            size = description
        if size:
            size = _clean_size_string(size)

    # Product ID: use baseProductId (TPR), dcId (coupon), or waId (WeeklyAd)
    product_id = (item.get("baseProductId")
                  or str(item.get("dcId", ""))
                  or str(item.get("waId", item.get("id", ""))))

    # URL for Publix product pages
    if item.get("baseProductId"):
        slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
        url = f"https://www.publix.com/pd/{slug}/{item['baseProductId']}"
    else:
        url = f"https://www.publix.com/savings/weekly-ad"

    # Image
    image_url = item.get("enhancedImageUrl") or item.get("imageUrl")

    # Categories and deal metadata
    categories = item.get("categories", [])
    is_bogo = "bogo" in categories or deal["deal_type"] == "bogo"

    return {
        "name": title,
        "product_id": product_id,
        "price": price,
        "price_string": price_string,
        "base_price": base_price,
        "unit_price_string": None,  # computed later by pricing layer
        "size": size,
        "brand": brand,
        "in_stock": True,
        "on_sale": on_sale,
        "url": url,
        "image": image_url,
        "image_url": image_url,
        "store": "publix",
        "serving_size": None,
        "sponsored": False,
        # Publix-specific deal fields
        "deal": deal,
        "bogo_price": bogo_price,
        "is_bogo": is_bogo,
        "deal_text": clean_html_text(savings_text),
        "deal_start": item.get("wa_startDate") or item.get("dc_startDate") or None,
        "deal_end": item.get("wa_endDate") or item.get("dc_endDate") or None,
        "department": clean_html_text(item.get("department")),
        "categories": categories,
        "saving_type": saving_type,
        # Coupon fields
        "coupon_value": coupon_value,
        "coupon_min_qty": coupon_min_qty,
        "coupon_price": coupon_price,
    }
