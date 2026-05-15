"""
Aldi session management — authentication and GraphQL requests.

THIS FILE IS TIGHTLY COUPLED TO ALDI'S INSTACART-POWERED BACKEND.
If Aldi changes their API, this is the file that breaks.

Current approach (as of 2025):
    Aldi's product data is served by an Instacart GraphQL API at:
        https://www.aldi.us/graphql

    Authentication:
        1. Visit https://www.aldi.us/ to pick up Instacart session cookies.
        2. Reuse those cookies on all subsequent GraphQL requests.
        3. If auth expires, re-establish the session automatically.

    Product lookup (Items operation):
        - Uses Apollo persisted queries: we send a sha256 hash (ITEMS_QUERY_HASH)
          instead of the full GraphQL query text.
        - Item IDs use the format "{ITEM_PREFIX}-{productId}"
          e.g. "items_23277-16902710"
        - Variables: ids[], shopId, zoneId, postalCode

    Search (SearchResultsPlacements operation):
        - Also uses a persisted query hash (SEARCH_QUERY_HASH).
        - Returns placement metadata; product IDs are extracted from the
          SearchContentManagementSearchItemGrid placement type.
        - Variables: query, shopId, zoneId, postalCode, first (limit), etc.

    Required request headers for GraphQL:
        x-client-identifier: "web"
        x-ic-view-layer: "true"
        Referer: "https://www.aldi.us/"
"""

import json
import logging
import random
import uuid
from typing import Optional
from urllib.parse import quote

import requests

from .config import (
    GRAPHQL_URL,
    ITEM_PREFIX,
    ITEMS_QUERY_HASH,
    SEARCH_QUERY_HASH,
    USER_AGENTS,
    DEFAULT_SHOP_ID,
    DEFAULT_ZONE_ID,
    DEFAULT_ZIP,
    DEFAULT_SEARCH_LIMIT,
)

log = logging.getLogger(__name__)


class AldiSession:
    """
    Manages a session with Aldi's Instacart-powered backend.

    Aldi's GraphQL API requires session cookies for authentication.
    This class visits the Aldi site first to establish a session,
    then reuses those cookies for all subsequent API calls.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._authenticated = False

    def _establish_session(self) -> bool:
        """
        Visit the Aldi site to pick up Instacart session cookies.

        Auth flow:
            1. GET https://www.aldi.us/  — picks up initial cookies
            2. If no Instacart cookie found, try the store page as fallback
            3. Session cookies are then reused for all GraphQL calls
        """
        if self._authenticated:
            return True

        try:
            log.info("Establishing Aldi session (getting auth cookies)...")
            resp = self.session.get("https://www.aldi.us/", timeout=15)
            if resp.status_code != 200:
                log.error(f"Failed to load aldi.us: HTTP {resp.status_code}")
                return False

            # Check that we got the critical Instacart session cookie
            cookies = self.session.cookies.get_dict()
            has_session = any(
                "instacart" in k.lower() or "ic" in k.lower()
                for k in cookies
            )

            if has_session:
                log.info(f"Session established ({len(cookies)} cookies)")
                self._authenticated = True
                return True
            else:
                # Sometimes the session cookie comes from a redirect or
                # subsequent request — try hitting a product page too
                log.info("No Instacart cookie yet, trying a product page...")
                resp2 = self.session.get(
                    "https://www.aldi.us/store/aldi/",
                    timeout=15,
                )
                cookies = self.session.cookies.get_dict()
                log.info(f"After store page: {len(cookies)} cookies")
                self._authenticated = True
                return True

        except Exception as e:
            log.error(f"Failed to establish session: {e}")
            return False

    def fetch_items(
        self,
        product_ids: list[str],
        shop_id: str = DEFAULT_SHOP_ID,
        zone_id: str = DEFAULT_ZONE_ID,
        postal_code: str = DEFAULT_ZIP,
    ) -> Optional[dict]:
        """
        Query Aldi's GraphQL API for product data.

        GraphQL operation: Items (persisted query)
        Endpoint: GET https://www.aldi.us/graphql
        Variables:
            ids:        ["items_23277-{pid}", ...]  — prefixed product IDs
            shopId:     store ID (e.g. "518104")
            zoneId:     zone ID (e.g. "178")
            postalCode: zip code for pricing

        Response path: data.items[] — array of item objects (see parser.py)
        """
        if not self._establish_session():
            log.error("Could not establish authenticated session")
            return None

        # Build the item IDs in Aldi's format: "items_23277-{productId}"
        item_ids = [f"{ITEM_PREFIX}-{pid}" for pid in product_ids]

        variables = {
            "ids": item_ids,
            "shopId": shop_id,
            "zoneId": zone_id,
            "postalCode": postal_code,
        }

        extensions = {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": ITEMS_QUERY_HASH,
            }
        }

        params = {
            "operationName": "Items",
            "variables": json.dumps(variables, separators=(",", ":")),
            "extensions": json.dumps(extensions, separators=(",", ":")),
        }

        # Headers that Aldi's frontend sends with GraphQL requests
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Referer": "https://www.aldi.us/",
            "x-client-identifier": "web",
            "x-ic-view-layer": "true",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        try:
            log.info(f"Fetching {len(product_ids)} items from Aldi GraphQL API")
            resp = self.session.get(
                GRAPHQL_URL,
                params=params,
                headers=headers,
                timeout=15,
            )

            if resp.status_code != 200:
                log.error(f"HTTP {resp.status_code}: {resp.text[:500]}")
                return None

            data = resp.json()

            # Check for GraphQL errors
            if "errors" in data:
                for err in data["errors"]:
                    log.error(f"GraphQL error: {err.get('message', err)}")
                if any("PersistedQueryNotFound" in str(e) for e in data["errors"]):
                    log.error(
                        "ITEMS_QUERY_HASH is stale. Open DevTools on aldi.us, "
                        "filter Network by 'graphql', find the 'Items' request, "
                        "and copy the new sha256Hash from the Payload tab."
                    )
                # If auth failed, clear session so it retries next time
                if any("Authenticated" in str(e) for e in data["errors"]):
                    log.info("Auth expired — will re-establish session on next call")
                    self._authenticated = False
                return None

            return data

        except Exception as e:
            log.error(f"Request failed: {e}")
            return None

    def fetch_items_idp(
        self,
        product_ids: list[str],
        shop_id: str,
    ) -> Optional[dict]:
        """
        Fetch product data via the IDP REST API.

        Unlike the GraphQL Items query, this endpoint only requires a shop_id
        (no zone_id needed), making it work for any store regardless of
        the session's geographic location.

        Endpoint: GET https://www.aldi.us/idp/v1/products
        Params:
            shop_id:         store shop ID
            product_ids[]:   list of numeric product IDs
            expands[]:       ["price", "details", "availability"]

        Returns the raw JSON response dict, or None on failure.
        """
        if not self._establish_session():
            log.error("Could not establish authenticated session")
            return None

        # Build URL with repeated params: product_ids[]=X&product_ids[]=Y
        id_params = "&".join(f"product_ids[]={pid}" for pid in product_ids)
        url = (
            f"{GRAPHQL_URL.replace('/graphql', '')}/idp/v1/products"
            f"?shop_id={shop_id}&{id_params}"
            f"&expands[]=price&expands[]=details&expands[]=availability"
        )

        headers = {
            "Accept": "application/json",
            "Referer": "https://www.aldi.us/",
        }

        try:
            log.info(f"Fetching {len(product_ids)} items from Aldi IDP API (shop {shop_id})")
            resp = self.session.get(url, headers=headers, timeout=15)

            if resp.status_code != 200:
                log.error(f"IDP API HTTP {resp.status_code}: {resp.text[:500]}")
                return None

            return resp.json()

        except Exception as e:
            log.error(f"IDP request failed: {e}")
            return None

    def search_product_ids(
        self,
        query: str,
        shop_id: str = DEFAULT_SHOP_ID,
        zone_id: str = DEFAULT_ZONE_ID,
        postal_code: str = DEFAULT_ZIP,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> Optional[list[str]]:
        """
        Search Aldi by keyword and return a list of numeric product IDs.

        GraphQL operation: SearchResultsPlacements (persisted query)
        Endpoint: GET https://www.aldi.us/graphql
        Variables:
            query, shopId, zoneId, postalCode, first (limit),
            plus several fixed params (filters, orderBy, etc.)

        Response path:
            data.searchResultsPlacements.placements[]
                -> find placement where content.__typename ==
                   "SearchContentManagementSearchItemGrid"
                -> content.itemProperties[].id  — numeric product IDs
        """
        if not self._establish_session():
            log.error("Could not establish authenticated session")
            return None

        page_view_id = str(uuid.uuid4())

        variables = {
            "filters": [],
            "action": None,
            "query": query,
            "pageViewId": page_view_id,
            "elevatedProductId": None,
            "searchSource": "search",
            "disableReformulation": False,
            "disableLlm": False,
            "forceInspiration": False,
            "orderBy": "bestMatch",
            "clusterId": None,
            "includeDebugInfo": False,
            "clusteringStrategy": None,
            "contentManagementSearchParams": {"itemGridColumnCount": 6},
            "shopId": shop_id,
            "postalCode": postal_code,
            "zoneId": zone_id,
            "first": limit,
        }

        extensions = {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": SEARCH_QUERY_HASH,
            }
        }

        params = {
            "operationName": "SearchResultsPlacements",
            "variables": json.dumps(variables, separators=(",", ":")),
            "extensions": json.dumps(extensions, separators=(",", ":")),
        }

        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Referer": f"https://www.aldi.us/store/aldi/s?k={quote(query)}",
            "x-client-identifier": "web",
            "x-ic-view-layer": "true",
            "x-page-view-id": page_view_id,
            "x-ic-qp": str(uuid.uuid4()),
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        try:
            log.info(f"Searching Aldi for: {query!r}")
            resp = self.session.get(
                GRAPHQL_URL,
                params=params,
                headers=headers,
                timeout=15,
            )

            if resp.status_code != 200:
                log.error(f"HTTP {resp.status_code}: {resp.text[:500]}")
                return None

            data = resp.json()

            if "errors" in data:
                for err in data["errors"]:
                    log.error(f"GraphQL error: {err.get('message', err)}")
                if any("PersistedQueryNotFound" in str(e) for e in data["errors"]):
                    log.error(
                        "SEARCH_QUERY_HASH is stale. Open DevTools on aldi.us, "
                        "search for any product, filter Network by 'graphql', "
                        "find the 'SearchResultsPlacements' request, and copy "
                        "the new sha256Hash from the Payload tab."
                    )
                return None

            placements = (
                data.get("data", {})
                    .get("searchResultsPlacements", {})
                    .get("placements", [])
            )
            for placement in placements:
                content = placement.get("content", {})
                if content.get("__typename") == "SearchContentManagementSearchItemGrid":
                    ids = [
                        str(p["id"])
                        for p in content.get("itemProperties", [])
                        if p.get("id")
                    ]
                    log.info(f"Search returned {len(ids)} results")
                    return ids

            log.warning("No SearchItemGrid found in search response")
            return []

        except Exception as e:
            log.error(f"Search request failed: {e}")
            return None
