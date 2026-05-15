"""
Walmart scraper configuration — constants that may need updating.

Nothing here is page-structure-dependent.  These are knobs for
request behavior (user agents, delays, retries).
"""

# Rotate through these to reduce fingerprinting.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# Delay range between requests (seconds)
MIN_DELAY = 2.0
MAX_DELAY = 4.0

# Retry config for search pages (they get CAPTCHAd more aggressively)
MAX_RETRIES = 3
BACKOFF_BASE = 3  # seconds — exponential: 3, 6, 12

# Max pages to fetch for a single search (safety cap)
MAX_SEARCH_PAGES = 5
