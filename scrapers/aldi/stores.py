"""
Aldi store lookup — find nearby stores by zip code.

Uses the IDP REST API at /idp/v1/shops?postal_code=X which returns
nearby shops for any zip code, regardless of the server's IP.

Product pricing is fetched via the IDP products API (/idp/v1/products)
which only requires shop_id — no zone_id needed. This means the store
selector works correctly for both local and hosted deployments.

Each physical Aldi has 3 shop variants (delivery, pickup, instore).
The API returns many pickup shops (one per nearby location) but only
1 instore shop. We use pickup shops as our store list since the IDP
products API returns valid per-store pricing for any shop_id type.
"""

import logging
from typing import Optional

from .session import AldiSession

log = logging.getLogger(__name__)

# In-memory cache: zip -> list of store dicts
_store_cache: dict[str, list[dict]] = {}

# Max stores to return per zip (API can return 100+)
MAX_STORES = 15


def find_stores_by_zip(zip_code: str, session: Optional[AldiSession] = None) -> list[dict]:
    """
    Find nearby Aldi shops for a given zip code.

    Returns a list of dicts with: shop_id, zone_id, name, address.
    Uses pickup shop variants since the API returns many per zip
    (one per physical location) and the IDP products API accepts them.
    """
    if zip_code in _store_cache:
        return _store_cache[zip_code]

    if session is None:
        session = AldiSession()

    if not session._establish_session():
        log.error("Could not establish Aldi session for store lookup")
        return []

    try:
        log.info(f"Looking up Aldi stores near zip {zip_code}")
        resp = session.session.get(
            "https://www.aldi.us/idp/v1/shops",
            params={"postal_code": zip_code},
            headers={
                "Accept": "application/json",
                "Referer": "https://www.aldi.us/",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            log.error(f"Shops API returned HTTP {resp.status_code}")
            return []

        all_shops = resp.json().get("shops", [])

    except Exception as e:
        log.error(f"Shops API request failed: {e}")
        return []

    # Use pickup shops — one per physical Aldi location, many returned per zip.
    # IDP products API accepts pickup shop IDs for pricing.
    pickup_shops = [s for s in all_shops if s.get("fulfillment_option") == "pickup"]

    if not pickup_shops:
        # Fall back to instore if no pickup shops
        pickup_shops = [s for s in all_shops if s.get("fulfillment_option") == "instore"]

    if not pickup_shops:
        log.warning(f"No Aldi shops found for zip {zip_code}")
        return []

    # Deduplicate by address (some locations may appear twice)
    seen_addresses = set()
    stores = []
    for shop in pickup_shops[:MAX_STORES]:
        addr = shop.get("address", {})
        address_str = ", ".join(filter(None, [
            addr.get("street_address", ""),
            addr.get("city", ""),
            f"{addr.get('state', '')} {addr.get('postal_code', '')}".strip(),
        ]))

        if address_str in seen_addresses:
            continue
        seen_addresses.add(address_str)

        # Use street + city for a clean dropdown label (location_name has
        # internal codes like "ALDI - BAT 64 - Chicago")
        street = addr.get("street_address", "")
        city = addr.get("city", "")
        label = f"{street}, {city}" if street and city else address_str

        stores.append({
            "shop_id": str(shop.get("id", "")),
            "zone_id": "",
            "name": f"ALDI - {label}",
            "address": address_str,
            "distance": None,
        })

    log.info(f"Found {len(stores)} Aldi shop(s) near zip {zip_code}")

    _store_cache[zip_code] = stores
    return stores
