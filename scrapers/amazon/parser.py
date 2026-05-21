"""
Amazon page-structure parser — extracts product data from HTML.

THIS FILE IS TIGHTLY COUPLED TO AMAZON'S CURRENT PAGE STRUCTURE.
If Amazon changes their frontend, this is the file that breaks.

Current approach (as of 2025):
    Amazon renders product data in HTML with specific CSS classes and
    data attributes.  We parse using BeautifulSoup.

Search results structure:
    div[data-component-type="s-search-result"][data-asin]
        h2 > a > span                      — product title
        span.a-price > span.a-offscreen     — price ("$3.28")
        span[data-a-size="s"] .a-price      — unit price
        img.s-image                         — thumbnail
        i.a-icon-prime                      — Prime badge
        a[href*="/dp/"]                     — product link with ASIN

Product page structure:
    span#productTitle                       — product name
    span.a-price .a-offscreen (in corePrice) — current price
    div#unifiedPrice_feature_div            — price container
    span.a-size-base (near unit price)      — per-unit price
    div#imageBlock img                      — product image
    table.a-bordered (nutrition facts)      — nutrition/serving size
"""

import re
import json
import logging
from typing import Optional

from bs4 import BeautifulSoup, Tag

from scrapers.models import AmazonProduct

log = logging.getLogger(__name__)


def _parse_price(price_str: Optional[str]) -> Optional[float]:
    """Parse a price string like '$3.28' or '$12.99' into a float."""
    if not price_str:
        return None
    cleaned = re.sub(r'[^\d.]', '', price_str)
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _extract_asin_from_link(href: str) -> Optional[str]:
    """Extract ASIN from an Amazon product link."""
    match = re.search(r'/dp/([A-Z0-9]{10})', href)
    return match.group(1) if match else None


def _extract_serving_size_from_page(soup: BeautifulSoup) -> Optional[str]:
    """Extract serving size from a product page's nutrition facts or details.

    Looks in multiple locations where Amazon embeds serving size info:
    - Nutrition facts table
    - "Important information" section
    - Product description / detail bullets
    """
    # Look in nutrition facts tables
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True).lower()
        if "serving size" in text or "serving per" in text:
            # Find the actual serving size value
            rows = table.find_all("tr")
            for row in rows:
                row_text = row.get_text(" ", strip=True)
                if "serving size" in row_text.lower():
                    # Try to extract the value (usually in the next td or same row)
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        return cells[1].get_text(strip=True)
                    # Might be all in one cell
                    match = re.search(r'serving size\s*[:\-]?\s*(.+)', row_text, re.IGNORECASE)
                    if match:
                        return match.group(1).strip()

    # Look in "important information" or product details sections
    for div_id in ("important-information", "productDetails_feature_div",
                    "detail-bullets_feature_div", "productDescription"):
        div = soup.find("div", {"id": div_id})
        if not div:
            continue
        text = div.get_text(" ", strip=True)
        match = re.search(r'serving size\s*[:\-]?\s*([^\n,;]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    # Look for JSON-LD structured data with nutrition info
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                nutrition = data.get("nutrition", {})
                if isinstance(nutrition, dict):
                    ss = nutrition.get("servingSize")
                    if ss:
                        return str(ss)
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def parse_product_page(html: str, product_id: str) -> AmazonProduct:
    """Parse a single Amazon product page HTML into an AmazonProduct."""
    try:
        soup = BeautifulSoup(html, "html.parser")

        # --- Name ---
        title_el = soup.find("span", {"id": "productTitle"})
        name = title_el.get_text(strip=True) if title_el else "Unknown"

        # --- Brand ---
        brand = None
        brand_el = soup.find("a", {"id": "bylineInfo"})
        if brand_el:
            brand_text = brand_el.get_text(strip=True)
            # Strip "Visit the X Store" or "Brand: X" prefixes
            brand_text = re.sub(r'^(Visit the |Brand:\s*)', '', brand_text)
            brand_text = re.sub(r'\s*Store$', '', brand_text)
            brand = brand_text if brand_text else None

        # --- Price ---
        price = None
        price_string = None

        # Try multiple price locations
        for price_container_id in ("corePrice_feature_div", "unifiedPrice_feature_div",
                                    "apex_offerDisplay_desktop", "price"):
            container = soup.find("div", {"id": price_container_id})
            if not container:
                container = soup.find("div", {"id": price_container_id})
            if container:
                price_el = container.find("span", class_="a-offscreen")
                if price_el:
                    price_string = price_el.get_text(strip=True)
                    price = _parse_price(price_string)
                    if price:
                        break

        # Fallback: look for any a-price span
        if price is None:
            price_whole = soup.find("span", class_="a-price-whole")
            price_frac = soup.find("span", class_="a-price-fraction")
            if price_whole:
                whole = price_whole.get_text(strip=True).rstrip(".")
                frac = price_frac.get_text(strip=True) if price_frac else "00"
                try:
                    price = float(f"{whole}.{frac}")
                    price_string = f"${price:.2f}"
                except ValueError:
                    pass

        # --- Unit price ---
        unit_price_string = None

        # Strategy 1: apex-priceperunit-accessibility-label (e.g. "$0.06 per fluid ounce")
        apex_label = soup.find("span", class_="apex-priceperunit-accessibility-label")
        if apex_label:
            apex_text = apex_label.get_text(strip=True)
            m = re.search(r'(\$[\d.]+)\s*per\s+(.+)', apex_text, re.IGNORECASE)
            if m:
                unit_price_string = f"{m.group(1)}/{m.group(2).strip()}"

        # Strategy 2: ($X.XX/unit) pattern in corePrice_feature_div text
        if not unit_price_string:
            core_div = soup.find("div", {"id": "corePrice_feature_div"})
            if core_div:
                core_text = core_div.get_text(" ", strip=True)
                m = re.search(r'\(\s*(\$[\d.]+)\s*/\s*([^)]+)\)', core_text)
                if m:
                    unit_price_string = f"{m.group(1)}/{m.group(2).strip()}"

        # Strategy 3: small a-price span with unit label nearby
        if not unit_price_string:
            unit_price_el = soup.find("span", class_="a-price", attrs={"data-a-size": "s"})
            if unit_price_el:
                parent = unit_price_el.parent
                if parent:
                    parent_text = parent.get_text(" ", strip=True)
                    m = re.search(r'\(\s*(\$[\d.]+)\s*/\s*([^)]+)\)', parent_text)
                    if m:
                        unit_price_string = f"{m.group(1)}/{m.group(2).strip()}"

        # Strategy 4: broad regex scan for "$X.XX / unit" or "$X.XX per unit" anywhere in price area
        if not unit_price_string:
            for div_id in ("corePrice_feature_div", "unifiedPrice_feature_div",
                           "apex_offerDisplay_desktop", "price"):
                div = soup.find("div", {"id": div_id})
                if not div:
                    continue
                div_text = div.get_text(" ", strip=True)
                m = re.search(r'(\$[\d.]+)\s*(?:/|per)\s+([\w\s]+?)(?:\s*[)\]]|$)', div_text, re.IGNORECASE)
                if m:
                    unit_price_string = f"{m.group(1)}/{m.group(2).strip()}"
                    break

        # --- Availability ---
        in_stock = True
        avail_el = soup.find("div", {"id": "availability"})
        if avail_el:
            avail_text = avail_el.get_text(strip=True).lower()
            if "unavailable" in avail_text or "out of stock" in avail_text:
                in_stock = False

        # --- Sale ---
        on_sale = False
        savings_el = soup.find("span", class_="a-color-price")
        if savings_el and "save" in savings_el.get_text(strip=True).lower():
            on_sale = True
        # Also check for "was" price
        was_price = soup.find("span", class_="a-text-strike")
        if was_price:
            on_sale = True

        # --- Image ---
        image_url = None
        img_el = soup.find("img", {"id": "landingImage"})
        if img_el:
            image_url = img_el.get("data-old-hires") or img_el.get("src")

        # --- URL ---
        url = f"https://www.amazon.com/dp/{product_id}"

        # --- Serving size ---
        serving_size = _extract_serving_size_from_page(soup)

        # --- Size/weight from title or product details ---
        size = None
        # Try to get from product details
        detail_rows = soup.find_all("tr", class_="a-spacing-small")
        for row in detail_rows:
            label = row.find("td", class_="a-span3")
            value = row.find("td", class_="a-span9")
            if label and value:
                label_text = label.get_text(strip=True).lower()
                if label_text in ("size", "item weight", "package weight", "net weight"):
                    size = value.get_text(strip=True)
                    break

        # Also check the "item-model-specs" table
        if not size:
            for th in soup.find_all("th"):
                th_text = th.get_text(strip=True).lower()
                if th_text in ("size", "item weight", "package weight", "net weight"):
                    td = th.find_next_sibling("td")
                    if td:
                        size = td.get_text(strip=True)
                        break

        return AmazonProduct(
            name=name,
            product_id=product_id,
            price=price,
            price_string=price_string,
            unit_price_string=unit_price_string,
            size=size,
            brand=brand,
            in_stock=in_stock,
            on_sale=on_sale,
            url=url,
            image_url=image_url,
            serving_size=serving_size,
        )

    except Exception as e:
        log.error(f"Failed to parse product {product_id}: {e}")
        return AmazonProduct(
            name="PARSE_ERROR",
            product_id=product_id,
            price=None,
            price_string=None,
            unit_price_string=None,
            size=None,
            brand=None,
            in_stock=False,
            on_sale=False,
            url=f"https://www.amazon.com/dp/{product_id}",
            error=str(e),
        )


def parse_search_results(html: str) -> list[dict]:
    """
    Extract product summaries from an Amazon search results page.

    Finds div[data-component-type="s-search-result"] elements and extracts:
        - ASIN (product ID) from data-asin attribute
        - Product name from h2 > a > span
        - Price from span.a-price > span.a-offscreen
        - Unit price from "$X.XX /unit" text pattern
        - Image from img.s-image
        - Prime eligibility from udm-primary-delivery-message class
        - Fresh detection from product name keywords
        - Sponsored flag from ad labels
    """
    results = []
    seen_ids = set()
    soup = BeautifulSoup(html, "html.parser")

    # Find all search result cards
    cards = soup.find_all("div", attrs={"data-component-type": "s-search-result"})
    if not cards:
        log.warning("No search result cards found in HTML")
        return results

    for card in cards:
        try:
            asin = card.get("data-asin", "").strip()
            if not asin or asin in seen_ids:
                continue
            seen_ids.add(asin)

            # --- Name ---
            name = None
            h2 = card.find("h2")
            if h2:
                name_span = h2.find("span")
                if name_span:
                    name = name_span.get_text(strip=True)
            if not name:
                continue

            # --- Link ---
            link = None
            if h2:
                a_tag = h2.find("a")
                if a_tag and a_tag.get("href"):
                    href = a_tag["href"]
                    if href.startswith("/"):
                        link = f"https://www.amazon.com{href}"
                    else:
                        link = href

            # --- Price ---
            price = None
            price_string = None
            # First a-price that is NOT a-text-price (which is the unit price)
            for price_el in card.find_all("span", class_="a-price"):
                if "a-text-price" in (price_el.get("class") or []):
                    continue
                offscreen = price_el.find("span", class_="a-offscreen")
                if offscreen:
                    price_string = offscreen.get_text(strip=True)
                    price = _parse_price(price_string)
                    if price is not None:
                        break

            # --- Unit price ---
            unit_price_string = None
            card_text = card.get_text(" ", strip=True)
            # Match "$X.XX /Ounce", "$X.XX /Fl Oz", "$X.XX /Count", etc.
            unit_match = re.search(
                r'(\$[\d.]+)\s*/\s*((?:Fl(?:uid)?\s*)?(?:Ounce|Oz|Count|lb|Pound|Each|Gram))',
                card_text, re.IGNORECASE
            )
            if not unit_match:
                # Match "$X.XX per fluid ounce", "$X.XX per ounce", etc.
                unit_match = re.search(
                    r'(\$[\d.]+)\s+per\s+((?:fluid\s*)?(?:ounce|oz|count|lb|pound|each|gram))',
                    card_text, re.IGNORECASE
                )
            if unit_match:
                unit_price_string = f"{unit_match.group(1)}/{unit_match.group(2)}"

            # --- Image ---
            image = None
            img_el = card.find("img", class_="s-image")
            if img_el:
                image = img_el.get("src")

            # --- Prime / delivery detection ---
            # Amazon now uses udm-primary-delivery-message for items with
            # Prime or free delivery. Fresh items may not have this but are
            # still deliverable via Fresh.
            has_delivery_msg = bool(card.find(class_="udm-primary-delivery-message"))
            is_prime = has_delivery_msg

            # --- Fresh detection ---
            # Products from Whole Foods Market, Amazon Fresh, or Amazon Kitchen
            is_fresh = bool(re.search(
                r'Whole Foods|Amazon Fresh|Amazon Kitchen',
                name, re.IGNORECASE
            ))
            # Fresh items are also considered Prime-eligible (delivered by Amazon)
            if is_fresh:
                is_prime = True

            # --- Sponsored ---
            sponsored = False
            sponsored_el = card.find("span", string=re.compile(r"Sponsored", re.IGNORECASE))
            if sponsored_el:
                sponsored = True

            # --- Rating ---
            rating = None
            rating_el = card.find("span", class_="a-icon-alt")
            if rating_el:
                rating_text = rating_el.get_text(strip=True)
                rating_match = re.search(r'([\d.]+)\s*out of', rating_text)
                if rating_match:
                    try:
                        rating = float(rating_match.group(1))
                    except ValueError:
                        pass

            results.append({
                "name": name,
                "product_id": asin,
                "price": price,
                "price_string": price_string,
                "unit_price_string": unit_price_string,
                "rating": rating,
                "image": image,
                "url": link or f"https://www.amazon.com/dp/{asin}",
                "sponsored": sponsored,
                "is_prime": is_prime,
                "is_fresh": is_fresh,
            })

        except Exception as e:
            log.warning(f"Skipping malformed search result: {e}")

    prime_count = sum(1 for r in results if r.get("is_prime"))
    fresh_count = sum(1 for r in results if r.get("is_fresh"))
    log.info(f"Parsed {len(results)} products from {len(cards)} search result cards "
             f"(Prime: {prime_count}, Fresh: {fresh_count})")
    return results
