"""Public API for the Publix scraper.

Provides search across Publix savings/deals, product fetching, and URL parsing.
Uses the savings API (weekly ad + TPR + digital coupons).
Base prices are extracted from deal data and stored persistently.
"""

import logging
import re
from typing import Optional

from . import config
from .http import PublixSession
from .parser import savings_item_to_product_dict, eligible_product_to_product_dict

log = logging.getLogger(__name__)

# GraphQL query for fetching eligible products by promo group IDs
_ELIGIBLE_PRODUCTS_QUERY = """query GetStoreProductsSavingsSearchResultAsync($keyword: String, $skip: Int!, $take: Int!, $facetOverrideStr: String, $facets: String, $sortOrder: String, $ispu: Boolean, $categoryID: String, $minMatch: Int!, $boostVarIndex: Int!, $wildcardSearch: Boolean!, $isPreviewSite: Boolean!, $segmentVarIndex: Int!, $getOrderHistory: Boolean!, $filterQuery: String, $reorderItemCodes: [Int!], $intents: [String!], $searchRetryIndex: Int!, $intentVarIndex: Int!, $boostBuryQuery: String, $source: String, $elevatedProducts: [KeyValuePairOfStringAndStringInput!], $couponId: String, $forceElevation: Boolean, $searchVariation: [KeyValuePairOfStringAndStringInput!], $userCoupon: String) {
  storeProductsSavingsSearchResult(
    keyword: $keyword
    skip: $skip
    take: $take
    facetOverrideStr: $facetOverrideStr
    facets: $facets
    sortOrder: $sortOrder
    ispu: $ispu
    categoryID: $categoryID
    minMatch: $minMatch
    boostVarIndex: $boostVarIndex
    wildcardSearch: $wildcardSearch
    isPreviewSite: $isPreviewSite
    segmentVarIndex: $segmentVarIndex
    getOrderHistory: $getOrderHistory
    filterQuery: $filterQuery
    reorderItemCodes: $reorderItemCodes
    intents: $intents
    boostBuryQuery: $boostBuryQuery
    searchRetryIndex: $searchRetryIndex
    intentVarIndex: $intentVarIndex
    source: $source
    elevatedProducts: $elevatedProducts
    couponId: $couponId
    forceElevation: $forceElevation
    searchVariation: $searchVariation
    userCoupon: $userCoupon
  ) {
    storeProducts {
      baseProductId
      itemCode
      title
      sizeDescription
      onSale
      priceLine
      imageUrls {
        large {
          a
        }
        small {
          a
        }
      }
      originalPriceLine
      promoConditionsMsg
      promoMsg
      promoType
      promoValidThruMsg
      promoTotalSavings
      onTpr
      hasCoupon
      titleBrand
    }
    totalCount
  }
}
"""


def _store_base_prices(products: list[dict], store_id: str = None):
    """Persist base prices extracted from deal data (best-effort)."""
    try:
        from database import store_publix_base_prices

        store_publix_base_prices(products, store_id)
    except Exception as e:
        log.debug(f"Could not store Publix base prices: {e}")


def scrape_search(
    query: str,
    zip_code: str = config.DEFAULT_ZIP,
    store_id: Optional[str] = None,
    session: Optional[PublixSession] = None,
    limit: int = config.DEFAULT_SEARCH_LIMIT,
    include_tpr: bool = True,
    include_coupons: bool = True,
) -> list[dict]:
    """Search Publix deals/savings by keyword.

    Searches the savings API which includes weekly ad items (BOGOs, sale prices,
    multi-buys), Stacked items (sale + coupon), and optionally TPR (temporary
    price reductions) and digital coupons.

    Args:
        query: Search keyword.
        zip_code: Zip code for store selection.
        store_id: Publix store number (e.g. "1131"). Auto-resolved from zip if not given.
        session: Reusable PublixSession. Created if not provided.
        limit: Max results to return.
        include_tpr: Whether to also search TPR items (default True).
        include_coupons: Whether to also search digital coupons (default True).

    Returns:
        List of product dicts with standardized fields plus Publix deal info.
    """
    if session is None:
        session = PublixSession()

    if not store_id:
        store_id = _resolve_store_id(zip_code, session)

    results = []
    seen_ids = set()
    inline_coupons = []  # DigitalCoupon items from the search results

    # Search weekly ad deals (includes WeeklyAd + Stacked + DigitalCoupon items)
    wa_results = _search_savings(query, store_id, session)
    for item in wa_results:
        if item.get("savingType") == "DigitalCoupon":
            inline_coupons.append(item)
            continue
        product = savings_item_to_product_dict(item)
        pid = product["product_id"]
        if pid not in seen_ids:
            seen_ids.add(pid)
            results.append(product)

    # Also search TPR items
    if include_tpr:
        tpr_results = _search_savings_tpr(query, store_id, session)
        for item in tpr_results:
            product = savings_item_to_product_dict(item)
            pid = product["product_id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                results.append(product)

    # Collect digital coupons and attach them to matching products
    # instead of showing coupons as standalone results
    if include_coupons:
        coupon_results = _search_coupons(query, store_id, session)
        # Dedupe: inline coupons from search may overlap with coupon endpoint
        inline_dc_ids = {c.get("dcId") for c in inline_coupons}
        for c in coupon_results:
            if c.get("dcId") not in inline_dc_ids:
                inline_coupons.append(c)
        _attach_coupons_to_products(results, inline_coupons)

    # Also search eligible products from umbrella deals and merge
    eligible = _search_eligible_products(query, store_id, session, seen_ids)
    if eligible:
        results.extend(eligible)
        log.info(f"Publix: added {len(eligible)} eligible products for '{query}'")

    log.info(f"Publix search '{query}': {len(results)} results (store {store_id})")
    trimmed = results[:limit]
    _store_base_prices(trimmed, store_id)
    return trimmed


def scrape_weekly_ad(
    zip_code: str = config.DEFAULT_ZIP,
    store_id: Optional[str] = None,
    session: Optional[PublixSession] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Fetch the full Publix weekly ad for a store.

    Args:
        zip_code: Zip code for store selection.
        store_id: Publix store number.
        session: Reusable PublixSession.
        category: Filter by category (e.g. "bogo", "meat", "produce").

    Returns:
        List of product dicts for all weekly ad items.
    """
    if session is None:
        session = PublixSession()

    if not store_id:
        store_id = _resolve_store_id(zip_code, session)

    params = {
        "smImg": config.SMALL_IMAGE_SIZE,
        "enImg": config.LARGE_IMAGE_SIZE,
        "fallbackImg": False,
        "isMobile": False,
        "page": 1,
        "pageSize": 0,  # 0 = all
        "includePersonalizedDeals": False,
        "languageID": 1,
        "isWeb": True,
        "getSavingType": config.SAVING_TYPE_WEEKLY_AD,
    }

    data = session.get_json(
        config.SAVINGS_ENDPOINT,
        params=params,
        extra_headers={"PublixStore": store_id},
    )

    if not data:
        log.warning(f"Publix weekly ad fetch failed for store {store_id}")
        return []

    savings = data.get("Savings", []) if isinstance(data, dict) else data
    results = [savings_item_to_product_dict(item) for item in savings]

    if category:
        cat_lower = category.lower()
        results = [r for r in results if cat_lower in r.get("categories", [])]

    log.info(f"Publix weekly ad: {len(results)} items (store {store_id})")
    _store_base_prices(results, store_id)
    return results


def scrape_product(
    product_id: str,
    zip_code: str = config.DEFAULT_ZIP,
    store_id: Optional[str] = None,
    session: Optional[PublixSession] = None,
) -> Optional[dict]:
    """Fetch a single Publix product/deal by ID.

    For weekly ad items (waId), uses the deal detail endpoint.
    For TPR items (baseProductId like RIO-PCI-XXXXX), searches TPR savings.

    Returns a product dict or None if not found.
    """
    if session is None:
        session = PublixSession()

    if not store_id:
        store_id = _resolve_store_id(zip_code, session)

    result = None

    # Check if it's a weekly ad ID (negative integer or UUID)
    if _is_weekly_ad_id(product_id):
        result = _fetch_weekly_ad_deal(product_id, store_id, session)
    # Check if it's a base product ID (RIO-PCI-XXXXX format)
    elif product_id.startswith("RIO-"):
        result = _fetch_tpr_product(product_id, store_id, session)
    else:
        # Try as a weekly ad ID (numeric)
        try:
            wa_id = int(product_id)
            result = _fetch_weekly_ad_deal(str(wa_id), store_id, session)
        except ValueError:
            pass

    if result:
        _store_base_prices([result], store_id)
        return result

    log.warning(f"Publix: could not resolve product ID '{product_id}'")
    return None


def scrape_product_list(
    product_ids: list[str],
    zip_code: str = config.DEFAULT_ZIP,
    store_id: Optional[str] = None,
    session: Optional[PublixSession] = None,
) -> list[dict]:
    """Fetch multiple Publix products with polite delays."""
    import time
    import random

    if session is None:
        session = PublixSession()

    if not store_id:
        store_id = _resolve_store_id(zip_code, session)

    results = []
    for i, pid in enumerate(product_ids):
        product = scrape_product(pid, zip_code, store_id, session)
        if product:
            results.append(product)
        if i < len(product_ids) - 1:
            time.sleep(random.uniform(config.MIN_DELAY, config.MAX_DELAY))

    return results


def extract_id_from_url(url: str) -> Optional[str]:
    """Extract Publix product ID from a URL.

    Handles:
        https://www.publix.com/pd/product-name/RIO-PCI-114037
        https://www.publix.com/pd/RIO-PCI-114037
        https://www.publix.com/savings/weekly-ad (no product ID)
    """
    # RIO-PCI-XXXXXX format (base product ID)
    m = re.search(r"(RIO-PCI-\d+)", url, re.I)
    if m:
        return m.group(1)

    # /pd/slug/SOME-ID pattern
    m = re.search(r"/pd/[^/]+/([A-Z0-9-]+)", url, re.I)
    if m:
        return m.group(1)

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _attach_coupons_to_products(products: list[dict], coupon_items: list[dict]):
    """Match digital coupons to products by brand and attach coupon info.

    Instead of showing coupons as standalone results, we find products that
    a coupon applies to and add coupon fields to those products.
    """
    from .parser import parse_savings_text, clean_html_text

    for raw_coupon in coupon_items:
        savings_text = raw_coupon.get("savings", "")
        deal = parse_savings_text(savings_text)
        coupon_value = deal.get("dollars_off")
        if not coupon_value:
            continue

        coupon_brand = (raw_coupon.get("brand") or "").lower().strip()
        coupon_title = clean_html_text(raw_coupon.get("title", "")) or ""
        min_qty = max(raw_coupon.get("minimumPurchase") or 1, 1)

        if not coupon_brand:
            continue

        # A coupon brand like "Irish Spring or Softsoap" may list multiple brands
        coupon_brands = [b.strip() for b in re.split(r"\s+or\s+", coupon_brand)]

        matched = False
        for product in products:
            product_brand = (product.get("brand") or "").lower().strip()
            if not product_brand:
                continue

            if not any(
                cb in product_brand or product_brand in cb for cb in coupon_brands
            ):
                continue

            # Always attach the coupon description text for display
            if not product.get("coupon_text"):
                product["coupon_text"] = coupon_title

            # Skip full coupon math if product already has coupon info
            # from Stacked deals or description stacking
            if product.get("coupon_value"):
                matched = True
                continue

            # Attach coupon info
            product["coupon_value"] = coupon_value
            product["coupon_min_qty"] = min_qty
            # Compute effective price after coupon if product has a price
            if product.get("price") and min_qty > 0:
                if product.get("is_bogo") and product.get("bogo_price") is not None:
                    # BOGO + coupon: total = buy × regular_price − coupon_value
                    deal_info = product.get("deal", {})
                    buy = deal_info.get("buy_qty", 1)
                    get_qty = deal_info.get("get_qty", 0)
                    total_qty = buy + get_qty
                    total_paid = product["price"] * buy - coupon_value
                    product["coupon_price"] = round(total_paid / total_qty, 2)
                else:
                    per_unit_discount = coupon_value / min_qty
                    product["coupon_price"] = round(
                        product["price"] - per_unit_discount, 2
                    )
            matched = True

        if matched:
            log.debug(f"Attached coupon '{coupon_title}' to products")
        else:
            log.debug(
                f"No products matched coupon '{coupon_title}' (brand: {coupon_brand})"
            )


def _resolve_store_id(zip_code: str, session: PublixSession) -> str:
    """Get the nearest Publix store number for a zip code."""
    from .stores import find_stores_by_zip

    stores = find_stores_by_zip(zip_code, session, limit=1)
    if stores:
        return stores[0]["id"]

    log.warning(f"No Publix stores found for zip {zip_code}, using default")
    return "1131"


def _search_savings(query: str, store_id: str, session: PublixSession) -> list[dict]:
    """Search the savings/search endpoint (weekly ad + digital coupons)."""
    params = {
        "smImg": config.SMALL_IMAGE_SIZE,
        "enImg": config.LARGE_IMAGE_SIZE,
        "fallbackImg": False,
        "isMobile": False,
        "page": 1,
        "pageSize": 0,
        "includePersonalizedDeals": False,
        "languageID": 1,
        "isWeb": True,
        "keyword": query,
    }

    data = session.get_json(
        config.SAVINGS_SEARCH_ENDPOINT,
        params=params,
        extra_headers={"PublixStore": store_id},
    )

    if data is None:
        return []

    # Search endpoint returns a list directly
    if isinstance(data, list):
        return data
    # Or sometimes a dict with Savings key
    return data.get("Savings", [])


def _search_savings_tpr(
    query: str, store_id: str, session: PublixSession
) -> list[dict]:
    """Get all TPR items and filter by keyword locally.

    The savings search endpoint doesn't always include TPR items,
    so we fetch all TPRs and filter.
    """
    params = {
        "smImg": config.SMALL_IMAGE_SIZE,
        "enImg": config.LARGE_IMAGE_SIZE,
        "fallbackImg": False,
        "isMobile": False,
        "page": 1,
        "pageSize": 0,
        "includePersonalizedDeals": False,
        "languageID": 1,
        "isWeb": True,
        "getSavingType": config.SAVING_TYPE_TPR,
    }

    data = session.get_json(
        config.SAVINGS_ENDPOINT,
        params=params,
        extra_headers={"PublixStore": store_id},
    )

    if not data:
        return []

    all_tpr = data.get("Savings", []) if isinstance(data, dict) else data

    # Filter by keyword match in title, description, or brand
    query_lower = query.lower()
    query_words = query_lower.split()
    matched = []
    for item in all_tpr:
        text = " ".join(
            filter(
                None,
                [
                    item.get("title", ""),
                    item.get("description", ""),
                    item.get("brand", ""),
                ],
            )
        ).lower()
        if all(w in text for w in query_words):
            matched.append(item)

    return matched


def _search_coupons(query: str, store_id: str, session: PublixSession) -> list[dict]:
    """Get all digital coupons and filter by keyword locally."""
    params = {
        "smImg": config.SMALL_IMAGE_SIZE,
        "enImg": config.LARGE_IMAGE_SIZE,
        "fallbackImg": False,
        "isMobile": False,
        "page": 1,
        "pageSize": 0,
        "includePersonalizedDeals": False,
        "languageID": 1,
        "isWeb": True,
        "getSavingType": config.SAVING_TYPE_DIGITAL_COUPON,
    }

    data = session.get_json(
        config.SAVINGS_ENDPOINT,
        params=params,
        extra_headers={"PublixStore": store_id},
    )

    if not data:
        return []

    all_coupons = data.get("Savings", []) if isinstance(data, dict) else data

    query_lower = query.lower()
    query_words = query_lower.split()
    matched = []
    for item in all_coupons:
        text = " ".join(
            filter(
                None,
                [
                    item.get("title", ""),
                    item.get("description", ""),
                    item.get("brand", ""),
                ],
            )
        ).lower()
        if all(w in text for w in query_words):
            matched.append(item)

    return matched


def _search_eligible_products(
    query: str, store_id: str, session: PublixSession, seen_ids: set
) -> list[dict]:
    """Search eligible products from umbrella weekly ad deals.

    Fetches the full weekly ad, collects promo group IDs from deals,
    then queries the GraphQL endpoint to find individual products
    that match the search query.
    """
    # Fetch full weekly ad to get all deals with promo group IDs
    params = {
        "smImg": config.SMALL_IMAGE_SIZE,
        "enImg": config.LARGE_IMAGE_SIZE,
        "fallbackImg": False,
        "isMobile": False,
        "page": 1,
        "pageSize": 0,
        "includePersonalizedDeals": False,
        "languageID": 1,
        "isWeb": True,
        "getSavingType": config.SAVING_TYPE_WEEKLY_AD,
    }

    data = session.get_json(
        config.SAVINGS_ENDPOINT,
        params=params,
        extra_headers={"PublixStore": store_id},
    )

    if not data:
        return []

    savings = data.get("Savings", []) if isinstance(data, dict) else data

    # Collect all promo group IDs from deals that have them
    all_promo_ids = []
    for item in savings:
        pgi = item.get("promoGroupIds")
        if pgi:
            for pid in pgi.split(","):
                pid = pid.strip()
                if pid:
                    all_promo_ids.append(pid)

    if not all_promo_ids:
        return []

    # Query GraphQL for eligible products, filtering by search keyword
    products = _fetch_eligible_products(all_promo_ids, store_id, session, keyword=query)

    # Dedup against already-seen product IDs
    results = []
    for p in products:
        pid = p["product_id"]
        if pid not in seen_ids:
            seen_ids.add(pid)
            results.append(p)

    return results


def _fetch_eligible_products(
    promo_group_ids: list[str], store_id: str, session: PublixSession, keyword: str = ""
) -> list[dict]:
    """Fetch eligible products from the GraphQL endpoint by promo group IDs.

    Args:
        promo_group_ids: List of promo group ID strings (e.g. ["23601764-0", ...]).
        store_id: Publix store number.
        session: PublixSession with established cookies.
        keyword: Optional keyword to filter results server-side.

    Returns:
        List of product dicts, or empty list if the endpoint is blocked/fails.
    """
    filter_query = "||".join(f"promoGroupId::{pid}" for pid in promo_group_ids)

    body = {
        "operationName": "GetStoreProductsSavingsSearchResultAsync",
        "query": _ELIGIBLE_PRODUCTS_QUERY,
        "variables": {
            "keyword": keyword,
            "skip": 0,
            "take": 1000,
            "source": "WEB_WEEKLYAD_MODAL",
            "sortOrder": "salesRank asc",
            "minMatch": 0,
            "boostVarIndex": 0,
            "wildcardSearch": False,
            "isPreviewSite": False,
            "segmentVarIndex": 0,
            "getOrderHistory": False,
            "intentVarIndex": 1,
            "searchRetryIndex": 0,
            "filterQuery": filter_query,
            "intents": [],
            "elevatedProducts": [],
            "searchVariation": [],
            "forceElevation": False,
            "boostBuryQuery": "",
            "reorderItemCodes": None,
            "userCoupon": None,
        },
    }

    # Try search-specific warmup before hitting the GraphQL endpoint
    session.warmup_search()

    data = session.post_json(
        config.PRODUCTS_SEARCH_ENDPOINT,
        json_body=body,
        extra_headers={"PublixStore": store_id},
        cache_block=True,
    )

    if not data:
        log.warning(
            "Publix eligible products endpoint returned no data (may be Akamai-blocked)"
        )
        return []

    # Navigate the GraphQL response structure
    result = data
    if isinstance(data, dict):
        result = data.get("data", data)
        if isinstance(result, dict):
            result = result.get("storeProductsSavingsSearchResult", result)
            if isinstance(result, dict):
                result = result.get("storeProducts", [])

    if not isinstance(result, list):
        log.warning(f"Unexpected eligible products response format: {type(result)}")
        return []

    products = []
    for item in result:
        try:
            product = eligible_product_to_product_dict(item)
            if product["product_id"]:
                products.append(product)
        except Exception as e:
            log.debug(f"Failed to parse eligible product: {e}")

    log.debug(f"Fetched {len(products)} eligible products from GraphQL")
    return products


def _is_weekly_ad_id(product_id: str) -> bool:
    """Check if an ID looks like a weekly ad deal ID (negative int or UUID)."""
    try:
        val = int(product_id)
        return val < 0
    except ValueError:
        pass
    # UUID pattern
    return bool(
        re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            product_id,
            re.I,
        )
    )


def _fetch_weekly_ad_deal(
    deal_id: str, store_id: str, session: PublixSession
) -> Optional[dict]:
    """Fetch a single weekly ad deal by its waId."""
    data = session.get_json(
        config.DEAL_DETAIL_ENDPOINT,
        params={"weeklyadId": deal_id},
        extra_headers={"PublixStore": store_id},
    )

    if not data:
        return None

    return savings_item_to_product_dict(data)


def _fetch_tpr_product(
    base_product_id: str, store_id: str, session: PublixSession
) -> Optional[dict]:
    """Find a TPR item by its baseProductId."""
    # Fetch all TPRs and find the matching one
    params = {
        "smImg": config.SMALL_IMAGE_SIZE,
        "enImg": config.LARGE_IMAGE_SIZE,
        "fallbackImg": False,
        "isMobile": False,
        "page": 1,
        "pageSize": 0,
        "includePersonalizedDeals": False,
        "languageID": 1,
        "isWeb": True,
        "getSavingType": config.SAVING_TYPE_TPR,
    }

    data = session.get_json(
        config.SAVINGS_ENDPOINT,
        params=params,
        extra_headers={"PublixStore": store_id},
    )

    if not data:
        return None

    all_tpr = data.get("Savings", []) if isinstance(data, dict) else data
    for item in all_tpr:
        if item.get("baseProductId") == base_product_id:
            return savings_item_to_product_dict(item)

    log.warning(f"Publix TPR product {base_product_id} not found in current deals")
    return None
