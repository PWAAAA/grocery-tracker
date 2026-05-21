"""
Amazon scraper configuration — constants that may need updating.

Covers both regular Amazon (Prime-eligible) and Amazon Fresh/Grocery.
"""

# Rotate through these to reduce fingerprinting.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# Delay range between requests (seconds) — Amazon is more aggressive than Walmart
MIN_DELAY = 2.5
MAX_DELAY = 5.0

# Retry config
MAX_RETRIES = 3
BACKOFF_BASE = 4  # seconds — exponential: 4, 8, 16

# Max pages to fetch for a single search (safety cap)
MAX_SEARCH_PAGES = 3

# Default zip for pricing
DEFAULT_ZIP = "32801"

# Amazon base URL
BASE_URL = "https://www.amazon.com"

# Search department filters
DEPT_GROCERY = "amazonfresh"       # Amazon Fresh / Grocery
DEPT_ALL = ""                       # All departments (filter Prime in results)
