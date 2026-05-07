"""
Walmart HTTP layer — how we talk to walmart.com.

Everything in this file is specific to Walmart's request expectations:
  - Cookie format for location-based pricing
  - Header shape that avoids bot detection
  - CAPTCHA detection in response HTML
  - Retry + backoff around CAPTCHA blocks

If Walmart changes their bot-detection or cookie format, this is the
file to update.  The page-structure parsing lives in parser.py.
"""

import base64
import json
import time
import uuid
import random
import logging
from typing import Optional

from .config import USER_AGENTS, BACKOFF_BASE

# --- Try to use curl_cffi for better TLS fingerprinting ---
# Falls back to plain requests if not installed.
# curl_cffi is STRONGLY recommended for search pages.
try:
    import curl_cffi.requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

import requests

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# Request construction
# ────────────────────────────────────────────────────────────

def _build_headers(referer: Optional[str] = None) -> dict:
    """
    Build request headers that mimic a real browser.

    Walmart checks User-Agent, Sec-* headers, and referer to decide
    whether a request looks human.  For search pages, we set a Google
    referer to simulate arriving from a search engine — Walmart treats
    these more leniently.
    """
    ua = random.choice(USER_AGENTS)
    is_firefox = "Firefox" in ua

    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }

    # Sec- headers differ between Chrome and Firefox
    if not is_firefox:
        headers.update({
            "Sec-Ch-Ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="8"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site" if referer else "none",
            "Sec-Fetch-User": "?1",
        })

    if referer:
        headers["Referer"] = referer

    return headers


def _build_cookies(zip_code: str, store_id: Optional[str] = None) -> dict:
    """
    Build locGuestData cookie that overrides Akamai CDN geolocation.

    The base64-encoded locGuestData cookie + assortmentStoreId forces
    Walmart to return store-specific pricing regardless of the request's
    source IP.
    """
    acid = str(uuid.uuid4())
    timestamp = int(time.time() * 1000)

    loc_guest_data = {
        "intent": "SHIPPING",
        "storeIntent": "PICKUP",
        "mergeFlag": True,
        "postalCode": {"base": zip_code, "timestamp": timestamp},
        "validateKey": f"prod:v2:{acid}",
    }
    if store_id:
        loc_guest_data["pickup"] = {"nodeId": store_id, "timestamp": timestamp}

    encoded = base64.urlsafe_b64encode(json.dumps(loc_guest_data).encode()).decode()

    cookies = {
        "ACID": acid,
        "hasACID": "true",
        "hasLocData": "1",
        "locGuestData": encoded,
    }
    if store_id:
        cookies["assortmentStoreId"] = store_id
    return cookies


# ────────────────────────────────────────────────────────────
# CAPTCHA detection
# ────────────────────────────────────────────────────────────

def _is_captcha(html: str) -> bool:
    """
    Check if the response is a CAPTCHA / bot challenge page.

    Walmart returns a full HTML page with one of these phrases when
    it suspects automation.  We check only the first 3000 chars since
    the indicators always appear near the top.
    """
    lower = html[:3000].lower()
    indicators = [
        "robot or human",
        "captcha",
        "are you a human",
        "verify you are human",
        "press & hold",
        "blocked",
    ]
    return any(ind in lower for ind in indicators)


# ────────────────────────────────────────────────────────────
# Page fetching with retry
# ────────────────────────────────────────────────────────────

def fetch_page(
    url: str,
    zip_code: str,
    store_id: Optional[str] = None,
    referer: Optional[str] = None,
    max_retries: int = 1,
) -> Optional[str]:
    """
    Fetch a Walmart page's HTML with retry + exponential backoff.

    Uses curl_cffi if available (mimics Chrome TLS fingerprint),
    otherwise falls back to plain requests.

    Args:
        url:          Full Walmart URL.
        zip_code:     Zip code for pricing.
        store_id:     Optional store ID.
        referer:      Optional referer URL (helps with search pages).
        max_retries:  Number of retry attempts on CAPTCHA (default 1 for
                      product pages, higher for search).
    """
    cookies = _build_cookies(zip_code, store_id)

    for attempt in range(max_retries):
        try:
            if HAS_CFFI:
                # IMPORTANT: When using curl_cffi's impersonate mode, it
                # sets its own User-Agent, Accept, Sec-* headers etc. that
                # match the TLS fingerprint. Overriding them creates a
                # mismatch that anti-bot systems detect. Only pass minimal
                # extras that don't conflict with the impersonation.
                cffi_headers = {}
                if referer:
                    cffi_headers["Referer"] = referer

                resp = cffi_requests.get(
                    url,
                    headers=cffi_headers,
                    cookies=cookies,
                    impersonate="chrome",
                    timeout=15,
                )
            else:
                headers = _build_headers(referer=referer)
                resp = requests.get(
                    url,
                    headers=headers,
                    cookies=cookies,
                    timeout=15,
                )

            if resp.status_code != 200:
                log.warning(f"HTTP {resp.status_code} for {url}")
                return None

            if _is_captcha(resp.text):
                if attempt < max_retries - 1:
                    wait = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 2)
                    log.warning(
                        f"CAPTCHA on attempt {attempt + 1}/{max_retries} — "
                        f"retrying in {wait:.1f}s..."
                    )
                    time.sleep(wait)
                    continue
                else:
                    lib = "curl_cffi" if HAS_CFFI else "requests"
                    log.warning(
                        f"CAPTCHA after {max_retries} attempts for {url} "
                        f"(using {lib})"
                    )
                    if not HAS_CFFI:
                        log.warning(
                            "TIP: Install curl_cffi for much better results: "
                            "pip install curl_cffi"
                        )
                    return None

            return resp.text

        except Exception as e:
            log.error(f"Request failed for {url}: {e}")
            if attempt < max_retries - 1:
                time.sleep(BACKOFF_BASE * (2 ** attempt))
                continue
            return None

    return None
