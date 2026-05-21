"""
Amazon store/delivery availability lookup by zip code.

Amazon doesn't have physical stores in the traditional sense (except
Whole Foods, which has its own integration). For Fresh/Grocery, delivery
availability is zip-code-dependent.

This module checks whether Amazon Fresh delivery is available for a given
zip code by looking at the Fresh landing page response.
"""

import logging
from typing import Optional

from .http import fetch_page, HAS_CFFI

if HAS_CFFI:
    import curl_cffi.requests as _requests
else:
    import requests as _requests

log = logging.getLogger(__name__)


def find_stores_by_zip(zip_code: str, limit: int = 10) -> list[dict]:
    """
    Check Amazon delivery availability for a given zip code.

    Returns a list of "store" dicts representing available delivery options.
    For Amazon, these are virtual — Prime delivery and Fresh delivery zones
    rather than physical locations.
    """
    stores = []

    # Prime delivery is generally available everywhere
    stores.append({
        "id": "amazon-prime",
        "displayName": "Amazon Prime Delivery",
        "name": "Amazon.com (Prime)",
        "distance": None,
        "address": {
            "street": "",
            "city": "",
            "state": "",
            "zip": zip_code,
        },
    })

    # Check if Amazon Fresh is available in this zip
    fresh_available = _check_fresh_availability(zip_code)
    if fresh_available:
        stores.append({
            "id": "amazon-fresh",
            "displayName": "Amazon Fresh Delivery",
            "name": "Amazon Fresh",
            "distance": None,
            "address": {
                "street": "",
                "city": "",
                "state": "",
                "zip": zip_code,
            },
        })

    log.info(f"Amazon: {len(stores)} delivery options for zip {zip_code}")
    return stores[:limit]


def _check_fresh_availability(zip_code: str) -> bool:
    """
    Check whether Amazon Fresh delivers to a given zip code.

    Fetches the Fresh landing page with the zip cookie set and checks
    whether the response indicates availability.
    """
    try:
        cookies = {
            "sp-cdn": f'"L5Z:{zip_code}"',
            "i18n-prefs": "USD",
        }
        url = "https://www.amazon.com/alm/storefront?almBrandId=QW1hem9uIEZyZXNo"

        if HAS_CFFI:
            resp = _requests.get(url, cookies=cookies, impersonate="chrome", timeout=10, verify=False)
        else:
            resp = _requests.get(
                url, cookies=cookies, timeout=10, verify=False,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )

        if resp.status_code != 200:
            return False

        text = resp.text.lower()
        # If the page shows products or a storefront, Fresh is available
        # If it redirects or shows "not available", it's not
        if "not available" in text or "currently unavailable" in text:
            return False
        if "fresh" in text and ("add to cart" in text or "product" in text):
            return True
        # Default: assume available if we got a 200 with substantial content
        return len(resp.text) > 5000

    except Exception as e:
        log.warning(f"Fresh availability check failed for {zip_code}: {e}")
        return False
