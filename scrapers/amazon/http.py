"""
Amazon HTTP layer — how we talk to amazon.com.

Handles:
  - Header construction mimicking real browsers
  - Location cookies (zip code for delivery/Fresh availability)
  - Bot detection (CAPTCHA pages, 503s)
  - Retry + backoff
  - curl_cffi for TLS fingerprinting (same as Walmart scraper)
"""

import time
import random
import logging
from typing import Optional

from .config import USER_AGENTS, BACKOFF_BASE

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
    """Build request headers that mimic a real browser."""
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


def _build_cookies(zip_code: str) -> dict:
    """
    Build cookies that set the delivery location on Amazon.

    The sp-cdn cookie with "L5Z:{zip}" tells Amazon which zip code
    to use for delivery availability and Fresh pricing.
    """
    return {
        "sp-cdn": f'"L5Z:{zip_code}"',
        "i18n-prefs": "USD",
    }


# ────────────────────────────────────────────────────────────
# Bot detection
# ────────────────────────────────────────────────────────────

def _is_captcha(html: str) -> bool:
    """
    Check if the response is a CAPTCHA / bot challenge page.

    Amazon returns challenge pages with these indicators when it
    suspects automation.
    """
    lower = html[:5000].lower()
    indicators = [
        "captcha",
        "robot",
        "automated access",
        "sorry, we just need to make sure",
        "type the characters you see",
        "enter the characters",
        "api-services-support@amazon.com",
    ]
    return any(ind in lower for ind in indicators)


# ────────────────────────────────────────────────────────────
# Page fetching with retry
# ────────────────────────────────────────────────────────────

def fetch_page(
    url: str,
    zip_code: str,
    referer: Optional[str] = None,
    max_retries: int = 1,
) -> Optional[str]:
    """
    Fetch an Amazon page's HTML with retry + exponential backoff.

    Uses curl_cffi if available (mimics Chrome TLS fingerprint),
    otherwise falls back to plain requests.
    """
    cookies = _build_cookies(zip_code)

    for attempt in range(max_retries):
        try:
            if HAS_CFFI:
                cffi_headers = {}
                if referer:
                    cffi_headers["Referer"] = referer

                resp = cffi_requests.get(
                    url,
                    headers=cffi_headers,
                    cookies=cookies,
                    impersonate="chrome",
                    timeout=15,
                    verify=False,
                )
            else:
                headers = _build_headers(referer=referer)
                resp = requests.get(
                    url,
                    headers=headers,
                    cookies=cookies,
                    timeout=15,
                    verify=False,
                )

            if resp.status_code == 503:
                if attempt < max_retries - 1:
                    wait = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 2)
                    log.warning(
                        f"503 on attempt {attempt + 1}/{max_retries} — "
                        f"retrying in {wait:.1f}s..."
                    )
                    time.sleep(wait)
                    continue
                log.warning(f"503 after {max_retries} attempts for {url}")
                return None

            if resp.status_code != 200:
                log.warning(f"HTTP {resp.status_code} for {url}")
                return None

            if _is_captcha(resp.text):
                if attempt < max_retries - 1:
                    wait = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 3)
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

            log.info(f"Fetched {len(resp.text)} chars from {url}")
            return resp.text

        except Exception as e:
            log.error(f"Request failed for {url}: {e}")
            if attempt < max_retries - 1:
                time.sleep(BACKOFF_BASE * (2 ** attempt))
                continue
            return None

    return None
