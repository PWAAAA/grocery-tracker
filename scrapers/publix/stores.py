"""Publix store location lookup by zip code."""

import logging
from typing import Optional

from . import config
from .http import PublixSession

log = logging.getLogger(__name__)

_store_cache: dict[str, list[dict]] = {}


def find_stores_by_zip(
    zip_code: str, session: Optional[PublixSession] = None, limit: int = 10
) -> list[dict]:
    """Find nearby Publix stores for a given zip code.

    Returns list of dicts with keys: id, name, address, city, state, zip,
    phone, distance, hours, wa_store_num.
    """
    if zip_code in _store_cache:
        return _store_cache[zip_code][:limit]

    if session is None:
        session = PublixSession()

    data = session.get_json(
        config.STORE_LOCATOR_ENDPOINT,
        params={
            "types": "R,G,H,N,S",
            "option": "A",
            "count": limit,
            "includeOpenAndCloseDates": True,
            "zipCode": zip_code,
        },
    )

    if not data:
        log.warning(f"Publix store lookup failed for zip {zip_code}")
        return []

    # Response can be a dict with "Stores" key or a list
    stores_raw = data.get("Stores", data) if isinstance(data, dict) else data

    stores = []
    for s in stores_raw:
        store = {
            "id": s.get("KEY", "").lstrip("0"),
            "name": s.get("NAME") or s.get("SHORTNAME", ""),
            "address": s.get("ADDR", ""),
            "city": s.get("CITY", ""),
            "state": s.get("STATE", ""),
            "zip": s.get("ZIP", "").split("-")[0],
            "phone": s.get("PHONE", ""),
            "distance": s.get("DISTANCE", ""),
            "hours": s.get("STRHOURS", ""),
            "wa_store_num": s.get("WASTORENUM", ""),
        }
        stores.append(store)

    _store_cache[zip_code] = stores
    log.info(f"Publix: found {len(stores)} stores near {zip_code}")
    return stores[:limit]
