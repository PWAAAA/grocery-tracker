"""
Walmart store lookup — find nearby stores by zip code.

Uses zippopotam.us for zip-to-city resolution, then scrapes Walmart's
store directory and __NEXT_DATA__ for the full nearby-stores list.
"""

import re
import json
import logging
from typing import Optional

from .http import HAS_CFFI

if HAS_CFFI:
    import curl_cffi.requests as cffi_requests
else:
    import requests as cffi_requests

log = logging.getLogger(__name__)

BASE = "https://www.walmart.com"


def _get(url: str, timeout: int = 10) -> Optional[str]:
    """Simple GET that returns response text or None."""
    try:
        if HAS_CFFI:
            resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout, verify=False)
        else:
            resp = cffi_requests.get(url, timeout=timeout, verify=False)
        if resp.status_code != 200:
            log.warning(f"HTTP {resp.status_code} for {url}")
            return None
        # Check for CAPTCHA pages
        lower = resp.text[:3000].lower()
        if any(s in lower for s in ("robot or human", "captcha", "press & hold", "blocked")):
            log.warning(f"CAPTCHA detected on {url}")
            return None
        return resp.text
    except Exception as e:
        log.warning(f"Request failed: {url} — {e}")
    return None


def _zip_to_location(zip_code: str) -> Optional[dict]:
    """Resolve zip to city + state abbreviation via zippopotam.us."""
    text = _get(f"https://api.zippopotam.us/us/{zip_code}")
    if not text:
        return None
    try:
        data = json.loads(text)
        place = data["places"][0]
        return {
            "city": place["place name"],
            "state_abbr": place["state abbreviation"],
        }
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        log.warning(f"Failed to parse zippopotam response for {zip_code}: {e}")
        return None


def _extract_store_ids_from_directory(html: str) -> list[str]:
    """Extract store IDs from /store/{id}-{slug} links in directory HTML."""
    return re.findall(r'/store/(\d+)-[^"\'>\s]+', html)


def _get_nearby_nodes(store_id: str) -> list[dict]:
    """Fetch /store/{id} and extract nearByNodes from __NEXT_DATA__."""
    html = _get(f"{BASE}/store/{store_id}")
    if not html:
        return []

    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
    if not match:
        return []

    try:
        next_data = json.loads(match.group(1))
        nodes = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("initialData", {})
            .get("initialDataNearbyNodes", {})
            .get("data", {})
            .get("nearByNodes", {})
            .get("nodes", [])
        )
        return nodes
    except (json.JSONDecodeError, AttributeError) as e:
        log.warning(f"Failed to parse __NEXT_DATA__ for store {store_id}: {e}")
        return []


def _parse_nodes(nodes: list, supercenter_only: bool = True) -> list[dict]:
    """Parse raw nearByNodes into clean dicts."""
    results = []
    for node in nodes:
        if node.get("type") != "STORE":
            continue
        name = node.get("name", "")
        if supercenter_only and "supercenter" not in name.lower():
            continue

        address = node.get("address", {})
        results.append({
            "id": node.get("id"),
            "displayName": node.get("displayName", ""),
            "name": name,
            "distance": node.get("distance"),
            "address": {
                "street": address.get("streetAddress", ""),
                "city": address.get("city", ""),
                "state": address.get("state", ""),
                "zip": address.get("postalCode", ""),
            },
        })
    return results


def find_stores_by_zip(zip_code: str, limit: int = 10) -> list[dict]:
    """
    Find nearby Walmart Supercenters for a given zip code.

    Returns a list of dicts with: id, displayName, name, distance, address.
    Distances are relative to a store near the user's zip.
    """
    location = _zip_to_location(zip_code)
    if not location:
        log.warning(f"Could not resolve zip {zip_code} to a city")
        return []

    city_slug = location["city"].lower().replace(" ", "-")
    state_slug = location["state_abbr"].lower()

    directory_html = _get(f"{BASE}/store-directory/{state_slug}/{city_slug}")
    if not directory_html:
        log.warning(f"Could not fetch store directory for {state_slug}/{city_slug}")
        return []

    store_ids = _extract_store_ids_from_directory(directory_html)
    if not store_ids:
        log.warning(f"No store IDs found in directory for {state_slug}/{city_slug}")
        return []

    # Get nearby nodes from the first directory store
    nodes = _get_nearby_nodes(store_ids[0])
    if not nodes:
        return []

    # Find a store whose address zip matches the user's zip (or is closest).
    # Then re-fetch nearByNodes from that store for accurate distances.
    best_ref = None
    for node in nodes:
        addr = node.get("address", {})
        if addr.get("postalCode", "").startswith(zip_code[:3]):
            best_ref = node.get("id")
            if addr.get("postalCode", "").startswith(zip_code):
                break  # Exact zip match

    if best_ref and best_ref != store_ids[0]:
        refetched = _get_nearby_nodes(best_ref)
        if refetched:
            nodes = refetched

    results = _parse_nodes(nodes, supercenter_only=False)

    results.sort(key=lambda s: float(s.get("distance") or 999))
    log.info(f"Found {len(results)} stores near zip {zip_code}")
    return results[:limit]
