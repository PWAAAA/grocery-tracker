"""Publix HTTP session management with Akamai bypass via curl_cffi."""

import logging
import time
from typing import Optional

from . import config

log = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests

    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    log.warning(
        "curl_cffi not available — Publix scraper requires it for Akamai bypass"
    )


class PublixSession:
    """Manages a curl_cffi session with Akamai cookie warmup for Publix APIs.

    Must call establish() before making API requests. The warmup visit to
    publix.com picks up _abck / ak_bmsc cookies that Akamai requires.
    """

    def __init__(self):
        if not HAS_CURL_CFFI:
            raise RuntimeError("curl_cffi is required for the Publix scraper")
        self.session = cffi_requests.Session(impersonate="chrome", verify=False)
        self._warmed_up = False
        self._last_warmup = 0.0
        self._warmup_ttl = 1800  # re-warmup every 30 min
        self._graphql_blocked = False
        self._search_warmed_up = False

    def establish(self) -> bool:
        """Visit Publix site to pick up Akamai session cookies.

        Returns True on success, False on failure.
        """
        now = time.time()
        if self._warmed_up and (now - self._last_warmup) < self._warmup_ttl:
            return True

        try:
            r = self.session.get(config.WARMUP_URL)
            if r.status_code == 200:
                self._warmed_up = True
                self._last_warmup = now
                self._graphql_blocked = False
                self._search_warmed_up = False
                log.info("Publix session established (cookies acquired)")
                return True
            else:
                log.warning(f"Publix warmup returned {r.status_code}")
                return False
        except Exception as e:
            log.error(f"Publix session warmup failed: {e}")
            return False

    def warmup_search(self) -> bool:
        """Visit the Publix shop page to acquire Akamai tokens for /search/api/ endpoints.

        Separate from the savings warmup — the search endpoint may require
        cookies from a different Akamai sensor.
        """
        if self._search_warmed_up:
            return True

        try:
            r = self.session.get(config.SEARCH_WARMUP_URL)
            if r.status_code == 200:
                self._search_warmed_up = True
                log.info("Publix search warmup completed (visited shop page)")
                return True
            else:
                log.debug(f"Publix search warmup returned {r.status_code}")
                return False
        except Exception as e:
            log.debug(f"Publix search warmup failed: {e}")
            return False

    def get_json(
        self,
        url: str,
        params: dict,
        extra_headers: Optional[dict] = None,
        max_retries: int = config.MAX_RETRIES,
    ) -> Optional[dict | list]:
        """Make a GET request expecting JSON, with retry on failure."""
        if not self.establish():
            return None

        headers = {**config.API_HEADERS}
        if extra_headers:
            headers.update(extra_headers)

        for attempt in range(max_retries):
            try:
                r = self.session.get(url, params=params, headers=headers)
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 403:
                    log.warning(
                        f"Publix API 403 on attempt {attempt + 1} — re-warming session"
                    )
                    self._warmed_up = False
                    if not self.establish():
                        continue
                    backoff = config.BACKOFF_BASE * (2**attempt)
                    time.sleep(backoff)
                else:
                    log.warning(f"Publix API returned {r.status_code}: {r.text[:200]}")
                    return None
            except Exception as e:
                log.error(f"Publix request error (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(config.BACKOFF_BASE * (2**attempt))

        return None

    def post_json(
        self,
        url: str,
        json_body: dict,
        extra_headers: Optional[dict] = None,
        max_retries: int = config.MAX_RETRIES,
        cache_block: bool = False,
    ) -> Optional[dict | list]:
        """Make a POST request with JSON body expecting JSON response, with retry on failure.

        Args:
            cache_block: If True, skip immediately when endpoint is cached as blocked,
                         and cache the blocked state after all retries are exhausted.
        """
        if cache_block and self._graphql_blocked:
            log.debug("Publix GraphQL endpoint cached as blocked — skipping")
            return None

        if not self.establish():
            return None

        headers = {
            **config.API_HEADERS,
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        for attempt in range(max_retries):
            try:
                r = self.session.post(url, json=json_body, headers=headers)
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 403:
                    log.warning(
                        f"Publix POST 403 on attempt {attempt + 1} — re-warming session"
                    )
                    self._warmed_up = False
                    if not self.establish():
                        continue
                    backoff = config.BACKOFF_BASE * (2**attempt)
                    time.sleep(backoff)
                else:
                    log.warning(f"Publix POST returned {r.status_code}: {r.text[:200]}")
                    return None
            except Exception as e:
                log.error(f"Publix POST error (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(config.BACKOFF_BASE * (2**attempt))

        if cache_block:
            self._graphql_blocked = True
            log.warning(
                "Publix GraphQL endpoint blocked — caching to skip future calls this session"
            )

        return None
